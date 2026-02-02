"""
Runtime CFI resolver using Calibre's CFI parser.

This module provides exact CFI-to-chunk mapping by:
1. Calling calibre-debug to resolve CFI to spine_index + local_char_offset
2. Converting local offset to global offset using spine metadata
3. Binary searching chunks by global offset to find chunk_id
"""

import json
import subprocess
import logging
from pathlib import Path
from typing import Optional, Dict, Tuple
from dataclasses import dataclass

from chunk_index import ChunkIndex, load_chunk_index


# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class CFIResolution:
    """Result of CFI resolution."""
    spine_index: int
    href: str
    local_char_offset: int
    global_char_offset: int
    chunk_id: int
    confidence: float = 1.0  # Always 1.0 for exact resolution
    
    def to_dict(self) -> dict:
        return {
            'spine_index': self.spine_index,
            'href': self.href,
            'local_char_offset': self.local_char_offset,
            'global_char_offset': self.global_char_offset,
            'chunk_id': self.chunk_id,
            'confidence': self.confidence
        }


class CalibreCFIResolver:
    """
    Resolves EPUBCFI strings to chunk_id using Calibre's CFI parser.
    
    This provides exact (non-heuristic) resolution by using Calibre's
    own CFI implementation to determine the character offset.
    """
    
    def __init__(self, epub_path: str, timeline_path: str):
        self.epub_path = Path(epub_path)
        self.timeline_path = Path(timeline_path)
        
        # Load chunk index for binary search
        self.chunk_index = load_chunk_index(str(timeline_path))
        
        # Path to helper script
        self.helper_script = Path(__file__).parent / "tools" / "resolve_cfi_calibre.py"
        
        # Cache for last successful resolution
        self._cache_cfi: Optional[str] = None
        self._cache_result: Optional[CFIResolution] = None
        
        logger.info(f"CalibreCFIResolver initialized for {self.epub_path}")
        logger.info(f"Timeline: {self.chunk_index.get_chunk_count()} chunks, "
                   f"{self.chunk_index.get_spine_count()} spine entries")
    
    def resolve(self, epubcfi: str) -> Optional[CFIResolution]:
        """
        Resolve an EPUBCFI string to a chunk_id.
        
        Args:
            epubcfi: EPUBCFI string like "epubcfi(/132/2/4/140/1:5)"
            
        Returns:
            CFIResolution with spine_index, offsets, and chunk_id,
            or None if resolution fails.
        """
        # Check cache
        if epubcfi == self._cache_cfi and self._cache_result is not None:
            logger.debug(f"Cache hit for CFI: {epubcfi}")
            return self._cache_result
        
        logger.info(f"Resolving CFI: {epubcfi}")
        
        # Call Calibre helper
        helper_result = self._call_calibre_helper(epubcfi)
        
        if helper_result is None:
            logger.error(f"Calibre helper failed for CFI: {epubcfi}")
            return self._cache_result  # Return last known good result
        
        if 'error' in helper_result:
            logger.error(f"Calibre helper error: {helper_result['error']}")
            return self._cache_result
        
        spine_index = helper_result['spine_index']
        href = helper_result['href']
        local_offset = helper_result['local_char_offset']
        
        # Convert to global offset
        global_offset = self.chunk_index.global_offset_from_local(spine_index, local_offset)
        
        if global_offset is None:
            logger.error(f"Failed to convert local offset to global: spine_index={spine_index}")
            return self._cache_result
        
        # Lookup chunk
        chunk = self.chunk_index.lookup_chunk(global_offset)
        
        if chunk is None:
            logger.error(f"Failed to find chunk for global offset {global_offset}")
            return self._cache_result
        
        # Build result
        result = CFIResolution(
            spine_index=spine_index,
            href=href,
            local_char_offset=local_offset,
            global_char_offset=global_offset,
            chunk_id=chunk.chunk_id
        )
        
        # Update cache
        self._cache_cfi = epubcfi
        self._cache_result = result
        
        logger.info(f"Resolved: CFI -> spine={spine_index}, href={href}, "
                   f"local={local_offset}, global={global_offset}, chunk={chunk.chunk_id}")
        
        return result
    
    def _call_calibre_helper(self, epubcfi: str) -> Optional[dict]:
        """
        Call the Calibre CFI resolver helper script.
        
        Returns parsed JSON result or None on failure.
        """
        if not self.helper_script.exists():
            logger.error(f"Helper script not found: {self.helper_script}")
            return None
        
        cmd = [
            "calibre-debug",
            "--exec-file", str(self.helper_script),
            "--",
            str(self.epub_path),
            epubcfi
        ]
        
        logger.debug(f"Running: {' '.join(cmd)}")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            # Log stderr (helper's log output)
            if result.stderr:
                for line in result.stderr.strip().split('\n'):
                    logger.debug(f"[calibre-helper] {line}")
            
            # Parse stdout as JSON
            if result.stdout:
                try:
                    return json.loads(result.stdout.strip())
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse helper output: {e}")
                    logger.error(f"Output was: {result.stdout[:500]}")
                    return None
            
            logger.error("No output from calibre helper")
            return None
            
        except subprocess.TimeoutExpired:
            logger.error("Calibre helper timed out")
            return None
        except Exception as e:
            logger.error(f"Failed to run calibre helper: {e}")
            return None
    
    def get_chunk_id(self, epubcfi: str) -> Optional[int]:
        """Convenience method to get just the chunk_id."""
        result = self.resolve(epubcfi)
        return result.chunk_id if result else None
    
    def get_book_id(self) -> Optional[str]:
        """Get the book ID from the timeline."""
        return self.chunk_index.book_id


def create_resolver(epub_path: str, data_dir: str = "data") -> Optional[CalibreCFIResolver]:
    """
    Create a CFI resolver for an EPUB file.
    
    Looks for timeline.json in data/<book_id>/ directory.
    """
    epub_path = Path(epub_path)
    data_dir = Path(data_dir)
    
    # Try to find timeline
    # First, check if there's a matching book_id directory
    for subdir in data_dir.iterdir():
        if subdir.is_dir():
            timeline_path = subdir / "timeline.json"
            if timeline_path.exists():
                try:
                    with open(timeline_path, 'r') as f:
                        data = json.load(f)
                    
                    # Check if epub_path matches
                    if Path(data.get('epub_path', '')).resolve() == epub_path.resolve():
                        return CalibreCFIResolver(str(epub_path), str(timeline_path))
                except Exception:
                    continue
    
    logger.error(f"No timeline found for EPUB: {epub_path}")
    return None


if __name__ == '__main__':
    import sys
    
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )
    
    if len(sys.argv) < 4:
        print("Usage: python resolver_calibre.py <epub_path> <timeline_path> <epubcfi>")
        sys.exit(1)
    
    epub_path = sys.argv[1]
    timeline_path = sys.argv[2]
    epubcfi = sys.argv[3]
    
    resolver = CalibreCFIResolver(epub_path, timeline_path)
    result = resolver.resolve(epubcfi)
    
    if result:
        print(f"\nResolution result:")
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print("\nFailed to resolve CFI")
        sys.exit(1)
