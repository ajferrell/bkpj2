"""
EPUB preprocessor: extract canonical text and build chunk timeline.

Updated to support exact CFI-to-chunk mapping with global char offsets.
"""

import json
import re
import unicodedata
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from xml.etree import ElementTree as ET

from ebooklib import epub
from ebooklib import ITEM_DOCUMENT

from .canonicalize import canonicalize_xhtml, compute_sha256


class EPUBPreprocessor:
    """
    Preprocesses EPUB files to extract canonical text and create chunks.
    
    Builds a single global canonical text stream by concatenating spine docs,
    then chunks over that with global char offsets for exact CFI resolution.
    """
    
    def __init__(self, epub_path: str, output_dir: str = "data"):
        self.epub_path = Path(epub_path)
        self.output_dir = Path(output_dir)
        self.book = None
        self.book_id = None
        
        # Chunk parameters
        self.min_chunk_words = 250
        self.max_chunk_words = 400
    
    def process(self) -> Path:
        """Main processing pipeline."""
        print(f"Processing EPUB: {self.epub_path}")
        
        # Load EPUB
        self.book = epub.read_epub(str(self.epub_path))
        
        # Derive book ID
        self.book_id = self._get_book_id()
        print(f"Book ID: {self.book_id}")
        
        # Create output directory
        book_dir = self.output_dir / self.book_id
        book_dir.mkdir(parents=True, exist_ok=True)
        
        # Extract spine documents with global offsets
        spine_entries, global_text = self._extract_spine_with_global_offsets()
        print(f"Extracted {len(spine_entries)} spine items, {len(global_text)} chars total")
        
        # Build chunks with global offsets
        chunks = self._build_chunks_global(spine_entries, global_text)
        print(f"Created {len(chunks)} chunks")
        
        # Save timeline with new format
        timeline = {
            "book_id": self.book_id,
            "epub_path": str(self.epub_path),
            "total_chars": len(global_text),
            "total_chunks": len(chunks),
            "spine": spine_entries,
            "chunks": chunks
        }
        
        timeline_path = book_dir / "timeline.json"
        with open(timeline_path, 'w', encoding='utf-8') as f:
            json.dump(timeline, f, indent=2, ensure_ascii=False)
        
        print(f"Saved timeline to: {timeline_path}")
        
        return timeline_path
    
    def _get_book_id(self) -> str:
        """Derive a book ID from metadata or filename."""
        # Try to get identifier
        identifier = self.book.get_metadata('DC', 'identifier')
        
        if identifier:
            book_id = identifier[0][0]
            # Sanitize
            book_id = re.sub(r'[^\w\-]', '_', book_id)
            return book_id[:50]  # Limit length
        
        # Fallback to filename
        return self.epub_path.stem
    
    def _extract_spine_with_global_offsets(self) -> Tuple[List[Dict], str]:
        """
        Extract canonical text from all spine items and build global text stream.
        
        Returns:
            (spine_entries, global_text)
            
        spine_entries is a list of dicts:
            {
                'spine_index': int,
                'href': str,
                'global_start_char': int,
                'canonical_len': int,
                'sha256': str
            }
        """
        spine_entries = []
        global_text_parts = []
        global_offset = 0
        
        spine = self.book.spine
        
        for spine_index, (item_id, linear) in enumerate(spine):
            item = self.book.get_item_with_id(item_id)
            
            if item is None:
                continue
            
            try:
                content = item.get_content()
                canonical_text = canonicalize_xhtml(content)
                canonical_len = len(canonical_text)
                text_hash = compute_sha256(canonical_text)
                
                spine_entries.append({
                    'spine_index': spine_index,
                    'href': item.get_name(),
                    'global_start_char': global_offset,
                    'canonical_len': canonical_len,
                    'sha256': text_hash
                })
                
                global_text_parts.append(canonical_text)
                global_offset += canonical_len
                
                # Add separator between spine docs (double newline for paragraph break)
                if canonical_len > 0:
                    global_text_parts.append('\n\n')
                    global_offset += 2
                
            except Exception as e:
                print(f"Warning: Failed to extract text from spine {spine_index}: {e}")
        
        global_text = ''.join(global_text_parts)
        return spine_entries, global_text
    
    def _build_chunks_global(self, spine_entries: List[Dict], global_text: str) -> List[Dict]:
        """
        Build chunks from global text with global char offsets.
        Each chunk is 250-400 words, never splitting paragraphs.
        Respects spine/chapter boundaries.
        """
        chunks = []
        chunk_id = 0
        
        # Process each spine document separately to respect chapter boundaries
        for spine_entry in spine_entries:
            spine_index = spine_entry['spine_index']
            spine_start = spine_entry['global_start_char']
            spine_len = spine_entry['canonical_len']
            
            if spine_len == 0:
                continue
            
            # Extract this spine's text from global text
            spine_text = global_text[spine_start:spine_start + spine_len]
            
            # Split into paragraphs
            paragraphs = spine_text.split('\n\n')
            
            # Build chunks for this spine
            current_chunk_paragraphs = []
            current_word_count = 0
            current_start_local = 0  # Local offset within spine
            local_offset = 0  # Tracks current position in spine text
            
            for para in paragraphs:
                para = para.strip()
                para_len = len(para) + 2  # +2 for paragraph separator
                
                if not para:
                    local_offset += 2  # Empty paragraph separator
                    continue
                
                para_words = len(para.split())
                
                # Check if adding this paragraph exceeds max
                if current_word_count > 0 and current_word_count + para_words > self.max_chunk_words:
                    # Finalize current chunk (if it meets minimum)
                    if current_word_count >= self.min_chunk_words:
                        chunk_text = '\n\n'.join(current_chunk_paragraphs)
                        start_global = spine_start + current_start_local
                        end_global = start_global + len(chunk_text)
                        
                        chunks.append({
                            'chunk_id': chunk_id,
                            'start_char_global': start_global,
                            'end_char_global': end_global,
                            'start_doc_spine_index': spine_index,
                            'word_count': current_word_count,
                            'text_preview': chunk_text[:100] + '...' if len(chunk_text) > 100 else chunk_text
                        })
                        
                        chunk_id += 1
                        current_start_local = local_offset
                        current_chunk_paragraphs = []
                        current_word_count = 0
                
                # Add paragraph to current chunk
                current_chunk_paragraphs.append(para)
                current_word_count += para_words
                local_offset += para_len
            
            # Finalize remaining chunk for this spine
            if current_chunk_paragraphs and current_word_count >= self.min_chunk_words:
                chunk_text = '\n\n'.join(current_chunk_paragraphs)
                start_global = spine_start + current_start_local
                end_global = start_global + len(chunk_text)
                
                chunks.append({
                    'chunk_id': chunk_id,
                    'start_char_global': start_global,
                    'end_char_global': end_global,
                    'start_doc_spine_index': spine_index,
                    'word_count': current_word_count,
                    'text_preview': chunk_text[:100] + '...' if len(chunk_text) > 100 else chunk_text
                })
                
                chunk_id += 1
            elif current_chunk_paragraphs:
                # Small final chunk - merge with previous if possible
                if chunks and chunks[-1].get('start_doc_spine_index') == spine_index:
                    # Merge with previous chunk
                    chunk_text = '\n\n'.join(current_chunk_paragraphs)
                    chunks[-1]['end_char_global'] = spine_start + current_start_local + len(chunk_text)
                    chunks[-1]['word_count'] += current_word_count
                else:
                    # Create small chunk anyway
                    chunk_text = '\n\n'.join(current_chunk_paragraphs)
                    start_global = spine_start + current_start_local
                    end_global = start_global + len(chunk_text)
                    
                    chunks.append({
                        'chunk_id': chunk_id,
                        'start_char_global': start_global,
                        'end_char_global': end_global,
                        'start_doc_spine_index': spine_index,
                        'word_count': current_word_count,
                        'text_preview': chunk_text[:100] + '...' if len(chunk_text) > 100 else chunk_text
                    })
                    
                    chunk_id += 1
        
        return chunks


def preprocess_epub(epub_path: str, output_dir: str = "data") -> str:
    """
    Preprocess an EPUB file and return the timeline path.
    """
    preprocessor = EPUBPreprocessor(epub_path, output_dir)
    timeline_path = preprocessor.process()
    return str(timeline_path)


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python preprocessor.py <epub_path>")
        sys.exit(1)
    
    epub_path = sys.argv[1]
    timeline = preprocess_epub(epub_path)
    print(f"\nDone! Timeline: {timeline}")
