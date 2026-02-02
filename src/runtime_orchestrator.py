"""
Runtime orchestrator that integrates CFI resolution with chunk tracking.

This module provides the main runtime coordination between:
- Calibre watcher (reading position updates)
- CFI resolver (exact position resolution)
- Controller (scene switching logic)
- Audio engine (ambience playback)
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from .resolver_calibre import CalibreCFIResolver


logger = logging.getLogger(__name__)


class RuntimeOrchestrator:
    """
    Coordinates runtime CFI resolution and chunk tracking.
    
    This wraps the CalibreCFIResolver and provides:
    - Book switching (loads correct timeline when book changes)
    - Caching of last CFI to avoid re-resolution
    - Integration with the controller for scene switching
    """
    
    def __init__(
        self,
        data_dir: str = "data",
        book_id_mapping: Optional[Dict[str, str]] = None
    ):
        self.data_dir = Path(data_dir)
        self.book_id_mapping = book_id_mapping or {}
        
        # Current state
        self.current_book_id: Optional[str] = None
        self.current_epub_path: Optional[str] = None
        self.resolver: Optional[CalibreCFIResolver] = None
        
        # Cache
        self.last_cfi: Optional[str] = None
        self.last_chunk_id: Optional[int] = None
        self.last_resolution: Optional[Dict] = None
        
        logger.info("RuntimeOrchestrator initialized")
    
    def set_book(self, calibre_book_id: str) -> bool:
        """
        Set the current book by Calibre's book ID.
        
        Args:
            calibre_book_id: Book ID from Calibre annotations (usually a hash)
            
        Returns:
            True if book was loaded successfully
        """
        # Map Calibre ID to our book ID
        book_id = self.book_id_mapping.get(calibre_book_id, calibre_book_id)
        
        if book_id == self.current_book_id:
            return True  # Already loaded
        
        # Find timeline
        timeline_path = self.data_dir / book_id / "timeline.json"
        
        if not timeline_path.exists():
            logger.warning(f"No timeline found for book '{book_id}' at {timeline_path}")
            return False
        
        # Load timeline to get epub_path
        try:
            with open(timeline_path, 'r', encoding='utf-8') as f:
                timeline_data = json.load(f)
            
            epub_path = timeline_data.get('epub_path')
            
            if not epub_path or not Path(epub_path).exists():
                logger.error(f"EPUB not found: {epub_path}")
                return False
            
            # Create resolver
            self.resolver = CalibreCFIResolver(epub_path, str(timeline_path))
            self.current_book_id = book_id
            self.current_epub_path = epub_path
            
            # Clear cache
            self.last_cfi = None
            self.last_chunk_id = None
            self.last_resolution = None
            
            logger.info(f"Loaded book: {book_id} ({epub_path})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load book '{book_id}': {e}")
            return False
    
    def resolve_position(self, epubcfi: str) -> Optional[Dict[str, Any]]:
        """
        Resolve a CFI to chunk information.
        
        Args:
            epubcfi: EPUBCFI string from Calibre
            
        Returns:
            Dict with chunk_id, spine_index, global_offset, etc., or None
        """
        if not self.resolver:
            logger.warning("No resolver loaded - call set_book() first")
            return None
        
        # Check cache
        if epubcfi == self.last_cfi and self.last_resolution:
            logger.debug(f"Cache hit for CFI: {epubcfi}")
            return self.last_resolution
        
        # Resolve
        result = self.resolver.resolve(epubcfi)
        
        if result is None:
            logger.warning(f"Failed to resolve CFI: {epubcfi}")
            return self.last_resolution  # Return last known position
        
        # Build result dict
        resolution = {
            'chunk_id': result.chunk_id,
            'spine_index': result.spine_index,
            'href': result.href,
            'local_char_offset': result.local_char_offset,
            'global_char_offset': result.global_char_offset,
            'confidence': result.confidence
        }
        
        # Update cache
        self.last_cfi = epubcfi
        self.last_chunk_id = result.chunk_id
        self.last_resolution = resolution
        
        # Log resolution
        logger.info(
            f"Resolved: cfi={epubcfi[:40]}... -> "
            f"chunk={result.chunk_id}, spine={result.spine_index}, "
            f"global_offset={result.global_char_offset}"
        )
        
        return resolution
    
    def get_chunk_id(self, epubcfi: str) -> Optional[int]:
        """Convenience method to get just the chunk_id."""
        result = self.resolve_position(epubcfi)
        return result['chunk_id'] if result else None
    
    def get_current_book_id(self) -> Optional[str]:
        """Get the current book ID."""
        return self.current_book_id
    
    def get_chunk_count(self) -> int:
        """Get total number of chunks in current book."""
        if self.resolver:
            return self.resolver.chunk_index.get_chunk_count()
        return 0


def create_runtime_orchestrator(
    config_path: str = "config.json",
    data_dir: str = "data"
) -> RuntimeOrchestrator:
    """
    Create a RuntimeOrchestrator from config.
    """
    config = {}
    if Path(config_path).exists():
        with open(config_path, 'r') as f:
            config = json.load(f)
    
    book_id_mapping = config.get('book_id_mapping', {})
    
    return RuntimeOrchestrator(
        data_dir=data_dir,
        book_id_mapping=book_id_mapping
    )


if __name__ == '__main__':
    import sys
    
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )
    
    if len(sys.argv) < 4:
        print("Usage: python runtime_orchestrator.py <book_id> <data_dir> <epubcfi>")
        sys.exit(1)
    
    book_id = sys.argv[1]
    data_dir = sys.argv[2]
    epubcfi = sys.argv[3]
    
    orchestrator = RuntimeOrchestrator(data_dir=data_dir)
    
    if orchestrator.set_book(book_id):
        result = orchestrator.resolve_position(epubcfi)
        if result:
            print(f"\nResolution: {json.dumps(result, indent=2)}")
        else:
            print("\nFailed to resolve")
    else:
        print(f"\nFailed to load book: {book_id}")
