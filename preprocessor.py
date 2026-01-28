"""
EPUB preprocessor: extract canonical text and build chunk timeline.
"""

import json
import re
import unicodedata
from pathlib import Path
from typing import List, Dict
from xml.etree import ElementTree as ET

from ebooklib import epub
from ebooklib import ITEM_DOCUMENT


class EPUBPreprocessor:
    """
    Preprocesses EPUB files to extract canonical text and create chunks.
    """
    
    def __init__(self, epub_path: str, output_dir: str = "data"):
        self.epub_path = Path(epub_path)
        self.output_dir = Path(output_dir)
        self.book = None
        self.book_id = None
        
        # Chunk parameters
        self.min_chunk_words = 250
        self.max_chunk_words = 400
    
    def process(self):
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
        
        # Extract canonical text from spine
        spine_texts = self._extract_spine_texts()
        print(f"Extracted {len(spine_texts)} spine items")
        
        # Build chunks
        chunks = self._build_chunks(spine_texts)
        print(f"Created {len(chunks)} chunks")
        
        # Save timeline
        timeline = {
            "book_id": self.book_id,
            "epub_path": str(self.epub_path),
            "total_chunks": len(chunks),
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
    
    def _extract_spine_texts(self) -> List[Dict]:
        """
        Extract canonical text from all spine items.
        Returns a list of {spine_id, text, item_href}.
        """
        spine_texts = []
        
        # Get spine items
        spine = self.book.spine
        
        for spine_id, (item_id, linear) in enumerate(spine):
            # Get item
            item = self.book.get_item_with_id(item_id)
            
            if item is None:
                continue
            
            # Extract text from XHTML
            try:
                content = item.get_content()
                text = self._extract_text_from_xhtml(content)
                
                spine_texts.append({
                    'spine_id': spine_id,
                    'text': text,
                    'item_href': item.get_name()
                })
            
            except Exception as e:
                print(f"Warning: Failed to extract text from spine {spine_id}: {e}")
        
        return spine_texts
    
    def _extract_text_from_xhtml(self, content: bytes) -> str:
        """
        Extract canonical text from XHTML content.
        - Strip scripts, styles, nav
        - Normalize whitespace
        - Preserve paragraph boundaries
        - Unicode normalize
        """
        try:
            # Parse XHTML, removing namespaces for easier processing
            root = ET.fromstring(content)
            self._strip_namespaces(root)
            
            # Remove unwanted elements
            self._remove_elements(root, ['script', 'style', 'nav', 'meta', 'link'])
            
            # Extract text
            text = self._extract_text_recursive(root)
            
            # Normalize
            text = self._normalize_text(text)
            
            return text
        
        except ET.ParseError as e:
            print(f"Warning: XML parse error: {e}")
            # Fallback: crude text extraction
            text = content.decode('utf-8', errors='ignore')
            text = re.sub(r'<[^>]+>', ' ', text)
            return self._normalize_text(text)
    
    def _strip_namespaces(self, elem):
        """Remove namespaces from all elements."""
        if '}' in elem.tag:
            elem.tag = elem.tag.split('}', 1)[1]
        
        for child in elem:
            self._strip_namespaces(child)
    
    def _remove_elements(self, root, tag_names: List[str]):
        """Remove elements by tag name (no namespace)."""
        for tag in tag_names:
            for elem in root.findall(f'.//{tag}'):
                parent = root.find(f'.//{tag}/..')
                if parent is None:
                    # Try direct iteration
                    for p in root.iter():
                        if elem in list(p):
                            p.remove(elem)
                            break
                else:
                    parent.remove(elem)
    
    def _extract_text_recursive(self, element, paragraphs: List[str] = None) -> str:
        """
        Recursively extract text, preserving paragraph boundaries.
        """
        if paragraphs is None:
            paragraphs = []
        
        # Block-level elements that indicate paragraph boundaries
        block_tags = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'blockquote', 'section', 'article'}
        
        # Get tag name (already stripped of namespace)
        tag = element.tag.lower() if element.tag else ''
        
        # Collect text from this element and children
        parts = []
        
        if element.text:
            parts.append(element.text)
        
        for child in element:
            child_text = self._extract_text_recursive(child, None)
            if child_text:
                parts.append(child_text)
            
            if child.tail:
                parts.append(child.tail)
        
        text = ' '.join(parts).strip()
        
        # Add to paragraphs if block-level
        if tag in block_tags and text:
            paragraphs.append(text)
            return '\n\n'.join(paragraphs)
        
        return text
    
    def _normalize_text(self, text: str) -> str:
        """
        Normalize text:
        - Unicode normalize (NFC)
        - Collapse multiple whitespace
        - Preserve paragraph breaks (double newline)
        """
        # Unicode normalize
        text = unicodedata.normalize('NFC', text)
        
        # Split into paragraphs
        paragraphs = re.split(r'\n\s*\n', text)
        
        # Normalize each paragraph
        normalized = []
        for para in paragraphs:
            # Collapse whitespace
            para = re.sub(r'\s+', ' ', para)
            para = para.strip()
            
            if para:
                normalized.append(para)
        
        # Rejoin with double newline
        return '\n\n'.join(normalized)
    
    def _build_chunks(self, spine_texts: List[Dict]) -> List[Dict]:
        """
        Build chunks from spine texts.
        Each chunk is 250-400 words, never splitting paragraphs.
        """
        chunks = []
        chunk_id = 0
        
        for spine_item in spine_texts:
            spine_id = spine_item['spine_id']
            text = spine_item['text']
            
            # Split into paragraphs
            paragraphs = text.split('\n\n')
            
            # Build chunks
            current_chunk = []
            current_word_count = 0
            current_start_char = 0
            
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue
                
                para_words = len(para.split())
                
                # Check if adding this paragraph exceeds max
                if current_word_count > 0 and current_word_count + para_words > self.max_chunk_words:
                    # Finalize current chunk (if it meets minimum)
                    if current_word_count >= self.min_chunk_words:
                        chunk_text = '\n\n'.join(current_chunk)
                        end_char = current_start_char + len(chunk_text)
                        
                        chunks.append({
                            'chunk_id': chunk_id,
                            'spine_id': spine_id,
                            'start_char': current_start_char,
                            'end_char': end_char,
                            'word_count': current_word_count,
                            'text_preview': chunk_text[:100] + '...' if len(chunk_text) > 100 else chunk_text
                        })
                        
                        chunk_id += 1
                        current_start_char = end_char + 2  # +2 for paragraph break
                        current_chunk = []
                        current_word_count = 0
                
                # Add paragraph to current chunk
                current_chunk.append(para)
                current_word_count += para_words
            
            # Finalize remaining chunk
            if current_chunk and current_word_count >= self.min_chunk_words:
                chunk_text = '\n\n'.join(current_chunk)
                end_char = current_start_char + len(chunk_text)
                
                chunks.append({
                    'chunk_id': chunk_id,
                    'spine_id': spine_id,
                    'start_char': current_start_char,
                    'end_char': end_char,
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
