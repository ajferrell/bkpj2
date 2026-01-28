"""
EPUBCFI resolver: coarse mapping from EPUBCFI to chunk_id.
"""

import json
import re
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass


@dataclass
class LocationHint:
    """Coarse location hint derived from EPUBCFI."""
    spine_id: int  # Chapter/spine index
    path_depth: int  # DOM depth
    char_offset: int  # Character offset within element
    
    def __hash__(self):
        return hash((self.spine_id, self.path_depth, self.char_offset))


class CFIResolver:
    """
    Resolves EPUBCFI strings to chunk_id using coarse matching.
    Implements stickiness to avoid thrashing on ambiguous positions.
    """
    
    def __init__(self, timeline_path: str):
        self.timeline_path = Path(timeline_path)
        self.timeline: List[Dict] = []
        self.book_id: Optional[str] = None
        
        # Stickiness state
        self.last_chunk_id: Optional[int] = None
        self.last_location: Optional[LocationHint] = None
        self.stick_threshold = 0.5  # Confidence below this triggers stickiness
        
        self._load_timeline()
    
    def _load_timeline(self):
        """Load the preprocessed timeline."""
        if not self.timeline_path.exists():
            print(f"Warning: Timeline not found: {self.timeline_path}")
            return
        
        try:
            with open(self.timeline_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self.book_id = data.get('book_id', 'unknown')
            self.timeline = data.get('chunks', [])
            
            print(f"Loaded timeline: {len(self.timeline)} chunks for '{self.book_id}'")
        
        except Exception as e:
            print(f"Error loading timeline: {e}")
    
    def resolve(self, epubcfi: str) -> Tuple[Optional[int], float]:
        """
        Resolve an EPUBCFI string to a chunk_id.
        Returns (chunk_id, confidence).
        
        Confidence values:
        - 0.9+: High confidence, direct match
        - 0.6-0.9: Medium confidence, coarse match
        - 0.3-0.6: Low confidence, ambiguous
        - <0.3: Very low confidence, stick to previous
        """
        if not self.timeline:
            return None, 0.0
        
        # Parse EPUBCFI
        location = self._parse_epubcfi(epubcfi)
        
        if not location:
            # Unparseable, stick to previous
            return self.last_chunk_id, 0.1
        
        # Find matching chunk
        chunk_id, confidence = self._find_chunk(location)
        
        # Apply stickiness
        if confidence < self.stick_threshold and self.last_chunk_id is not None:
            # Low confidence, check if jump is large
            if chunk_id is not None:
                jump_size = abs(chunk_id - self.last_chunk_id)
                
                if jump_size > 5:
                    # Large jump with low confidence, stick to previous
                    return self.last_chunk_id, confidence * 0.5
        
        # Update state
        if chunk_id is not None:
            self.last_chunk_id = chunk_id
            self.last_location = location
        
        return chunk_id, confidence
    
    def _parse_epubcfi(self, epubcfi: str) -> Optional[LocationHint]:
        """
        Parse an EPUBCFI string into a coarse LocationHint.
        
        Example CFI: epubcfi(/6/4[chap01]!/4/2/16:23)
        - /6/4[chap01] = spine position
        - !/4/2/16 = path within document
        - :23 = character offset
        """
        try:
            # Extract spine path
            spine_match = re.search(r'/(\d+)(?:\[.*?\])?', epubcfi)
            if not spine_match:
                return None
            
            spine_step = int(spine_match.group(1))
            # Convert step to 0-based spine index (steps are 2-based)
            spine_id = (spine_step - 2) // 2
            spine_id = max(0, spine_id)
            
            # Extract content path (after !)
            content_match = re.search(r'!(.*?)(?::|$)', epubcfi)
            path_depth = 0
            
            if content_match:
                path_str = content_match.group(1)
                # Count path steps
                steps = re.findall(r'/(\d+)', path_str)
                path_depth = len(steps)
            
            # Extract character offset
            offset_match = re.search(r':(\d+)', epubcfi)
            char_offset = 0
            
            if offset_match:
                char_offset = int(offset_match.group(1))
            
            return LocationHint(
                spine_id=spine_id,
                path_depth=path_depth,
                char_offset=char_offset
            )
        
        except Exception as e:
            print(f"Error parsing EPUBCFI '{epubcfi}': {e}")
            return None
    
    def _find_chunk(self, location: LocationHint) -> Tuple[Optional[int], float]:
        """
        Find the best matching chunk for a location hint.
        Returns (chunk_id, confidence).
        """
        if not self.timeline:
            return None, 0.0
        
        # Filter chunks by spine_id
        spine_chunks = [
            c for c in self.timeline
            if c.get('spine_id') == location.spine_id
        ]
        
        if not spine_chunks:
            # No exact spine match, try adjacent spines
            spine_chunks = [
                c for c in self.timeline
                if abs(c.get('spine_id', 0) - location.spine_id) <= 1
            ]
            
            if not spine_chunks:
                # Fallback: just clamp to valid range
                chunk_id = self._clamp_chunk_id(location.spine_id)
                return chunk_id, 0.3
        
        # Rank chunks by estimated position within spine
        # Use char_offset as a rough guide
        scored = []
        
        for chunk in spine_chunks:
            chunk_id = chunk.get('chunk_id')
            start_char = chunk.get('start_char', 0)
            end_char = chunk.get('end_char', start_char + 1)
            
            # Calculate score based on char_offset
            if start_char <= location.char_offset < end_char:
                # Direct hit
                score = 1.0
            else:
                # Distance-based score
                if location.char_offset < start_char:
                    distance = start_char - location.char_offset
                else:
                    distance = location.char_offset - end_char
                
                # Decay with distance
                score = 1.0 / (1.0 + distance / 1000.0)
            
            scored.append((chunk_id, score))
        
        if not scored:
            return None, 0.0
        
        # Pick best match
        scored.sort(key=lambda x: x[1], reverse=True)
        best_chunk, best_score = scored[0]
        
        # Convert score to confidence
        confidence = min(0.95, best_score)
        
        # Boost confidence if spine_id matches exactly
        for chunk in spine_chunks:
            if chunk.get('chunk_id') == best_chunk:
                if chunk.get('spine_id') == location.spine_id:
                    confidence = min(1.0, confidence * 1.1)
                break
        
        return best_chunk, confidence
    
    def _clamp_chunk_id(self, spine_id: int) -> Optional[int]:
        """Clamp a spine_id to a valid chunk_id range."""
        if not self.timeline:
            return None
        
        # Find chunk range for this spine
        spine_chunks = [
            c.get('chunk_id')
            for c in self.timeline
            if c.get('spine_id') == spine_id
        ]
        
        if spine_chunks:
            return min(spine_chunks)
        
        # Fallback: return first chunk
        return self.timeline[0].get('chunk_id', 0)
    
    def get_book_id(self) -> Optional[str]:
        """Get the book ID."""
        return self.book_id


def test_resolver():
    """Test the resolver with a sample timeline."""
    # Create a dummy timeline
    timeline_path = Path("data/test_book/timeline.json")
    timeline_path.parent.mkdir(parents=True, exist_ok=True)
    
    dummy_timeline = {
        "book_id": "test_book",
        "chunks": [
            {"chunk_id": 0, "spine_id": 0, "start_char": 0, "end_char": 300},
            {"chunk_id": 1, "spine_id": 0, "start_char": 300, "end_char": 600},
            {"chunk_id": 2, "spine_id": 1, "start_char": 0, "end_char": 250},
            {"chunk_id": 3, "spine_id": 1, "start_char": 250, "end_char": 500},
        ]
    }
    
    with open(timeline_path, 'w', encoding='utf-8') as f:
        json.dump(dummy_timeline, f)
    
    # Test resolver
    resolver = CFIResolver(str(timeline_path))
    
    test_cfis = [
        "epubcfi(/6/2!/4/2:100)",  # chunk 0
        "epubcfi(/6/2!/4/2:400)",  # chunk 1
        "epubcfi(/6/4!/4/2:100)",  # chunk 2
        "epubcfi(/6/4!/4/2:300)",  # chunk 3
    ]
    
    print("\nTesting CFI resolver:\n")
    
    for cfi in test_cfis:
        chunk_id, confidence = resolver.resolve(cfi)
        print(f"CFI: {cfi}")
        print(f"  → chunk_id={chunk_id}, confidence={confidence:.2f}\n")


if __name__ == '__main__':
    test_resolver()
