"""
Chunk index utilities for binary search lookup of chunk_id from global char offset.
"""

import json
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import bisect


@dataclass
class SpineEntry:
    """Represents a spine document in the timeline."""
    spine_index: int
    href: str
    global_start_char: int
    canonical_len: int
    sha256: Optional[str] = None


@dataclass
class ChunkEntry:
    """Represents a chunk in the timeline."""
    chunk_id: int
    start_char_global: int
    end_char_global: int
    start_doc_spine_index: Optional[int] = None
    word_count: Optional[int] = None
    text_preview: Optional[str] = None


class ChunkIndex:
    """
    Index for fast chunk lookup by global char offset.
    Uses binary search for O(log n) lookups.
    """
    
    def __init__(self, timeline_path: str):
        self.timeline_path = Path(timeline_path)
        self.book_id: Optional[str] = None
        self.spine: List[SpineEntry] = []
        self.chunks: List[ChunkEntry] = []
        
        # Precomputed for binary search
        self._chunk_starts: List[int] = []
        self._spine_starts: List[int] = []
        
        self._load_timeline()
    
    def _load_timeline(self) -> None:
        """Load and index the timeline."""
        if not self.timeline_path.exists():
            raise FileNotFoundError(f"Timeline not found: {self.timeline_path}")
        
        with open(self.timeline_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        self.book_id = data.get('book_id', 'unknown')
        
        # Load spine entries
        for s in data.get('spine', []):
            self.spine.append(SpineEntry(
                spine_index=s['spine_index'],
                href=s['href'],
                global_start_char=s['global_start_char'],
                canonical_len=s['canonical_len'],
                sha256=s.get('sha256')
            ))
        
        # Load chunk entries
        for c in data.get('chunks', []):
            self.chunks.append(ChunkEntry(
                chunk_id=c['chunk_id'],
                start_char_global=c['start_char_global'],
                end_char_global=c['end_char_global'],
                start_doc_spine_index=c.get('start_doc_spine_index'),
                word_count=c.get('word_count'),
                text_preview=c.get('text_preview')
            ))
        
        # Build binary search arrays
        self._chunk_starts = [c.start_char_global for c in self.chunks]
        self._spine_starts = [s.global_start_char for s in self.spine]
        
        # Sort chunks by start position (should already be sorted)
        if self.chunks != sorted(self.chunks, key=lambda c: c.start_char_global):
            # Re-sort if needed
            sorted_chunks = sorted(self.chunks, key=lambda c: c.start_char_global)
            self.chunks = sorted_chunks
            self._chunk_starts = [c.start_char_global for c in self.chunks]
    
    def lookup_chunk(self, global_char_offset: int) -> Optional[ChunkEntry]:
        """
        Find the chunk containing the given global char offset.
        Uses binary search for efficiency.
        
        Returns None if offset is out of range.
        """
        if not self.chunks:
            return None
        
        # Binary search for the rightmost chunk whose start <= offset
        idx = bisect.bisect_right(self._chunk_starts, global_char_offset) - 1
        
        if idx < 0:
            # Offset is before first chunk - return first chunk
            return self.chunks[0]
        
        if idx >= len(self.chunks):
            # Offset is after last chunk - return last chunk
            return self.chunks[-1]
        
        chunk = self.chunks[idx]
        
        # Verify offset is within chunk bounds
        if chunk.start_char_global <= global_char_offset < chunk.end_char_global:
            return chunk
        
        # Offset falls between chunks (shouldn't happen with contiguous chunks)
        # Return the nearest chunk
        if idx + 1 < len(self.chunks):
            next_chunk = self.chunks[idx + 1]
            if abs(global_char_offset - chunk.end_char_global) < abs(global_char_offset - next_chunk.start_char_global):
                return chunk
            return next_chunk
        
        return chunk
    
    def lookup_chunk_id(self, global_char_offset: int) -> Optional[int]:
        """Convenience method to get just the chunk_id."""
        chunk = self.lookup_chunk(global_char_offset)
        return chunk.chunk_id if chunk else None
    
    def lookup_spine(self, global_char_offset: int) -> Optional[SpineEntry]:
        """
        Find the spine entry containing the given global char offset.
        """
        if not self.spine:
            return None
        
        idx = bisect.bisect_right(self._spine_starts, global_char_offset) - 1
        
        if idx < 0:
            return self.spine[0]
        
        if idx >= len(self.spine):
            return self.spine[-1]
        
        return self.spine[idx]
    
    def get_spine_entry(self, spine_index: int) -> Optional[SpineEntry]:
        """Get spine entry by index."""
        for s in self.spine:
            if s.spine_index == spine_index:
                return s
        return None
    
    def global_offset_from_local(self, spine_index: int, local_offset: int) -> Optional[int]:
        """
        Convert a local char offset within a spine doc to global offset.
        
        Args:
            spine_index: Index of the spine document
            local_offset: Character offset within that document's canonical text
            
        Returns:
            Global character offset, or None if spine_index invalid
        """
        spine_entry = self.get_spine_entry(spine_index)
        if spine_entry is None:
            return None
        
        global_offset = spine_entry.global_start_char + local_offset
        
        # Validate bounds
        max_offset = spine_entry.global_start_char + spine_entry.canonical_len
        if global_offset > max_offset:
            # Clamp to end of document
            global_offset = max_offset
        
        return global_offset
    
    def get_chunk_count(self) -> int:
        """Get total number of chunks."""
        return len(self.chunks)
    
    def get_spine_count(self) -> int:
        """Get total number of spine entries."""
        return len(self.spine)
    
    def get_total_length(self) -> int:
        """Get total length of canonical text."""
        if not self.spine:
            return 0
        last = self.spine[-1]
        return last.global_start_char + last.canonical_len


def load_chunk_index(timeline_path: str) -> ChunkIndex:
    """Convenience function to load a chunk index."""
    return ChunkIndex(timeline_path)


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python chunk_index.py <timeline_path> [offset]")
        sys.exit(1)
    
    timeline_path = sys.argv[1]
    index = ChunkIndex(timeline_path)
    
    print(f"Book ID: {index.book_id}")
    print(f"Spine entries: {index.get_spine_count()}")
    print(f"Chunks: {index.get_chunk_count()}")
    print(f"Total length: {index.get_total_length()} chars")
    
    if len(sys.argv) >= 3:
        offset = int(sys.argv[2])
        chunk = index.lookup_chunk(offset)
        if chunk:
            print(f"\nOffset {offset} -> chunk_id={chunk.chunk_id}")
            print(f"  range: [{chunk.start_char_global}, {chunk.end_char_global})")
        else:
            print(f"\nOffset {offset} -> no chunk found")
