"""
Shared canonicalization logic for deterministic text extraction.
Used by both preprocessing and runtime CFI resolution.
"""

import re
import unicodedata
from typing import List, Tuple, Optional
from xml.etree import ElementTree as ET


# Block-level elements that indicate paragraph boundaries
BLOCK_TAGS = frozenset({
    'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 
    'li', 'blockquote', 'section', 'article', 'header', 
    'footer', 'aside', 'figure', 'figcaption', 'pre'
})

# Elements to remove entirely (content and all)
REMOVE_TAGS = frozenset({
    'script', 'style', 'nav', 'meta', 'link', 'head'
})


def strip_namespace(tag: str) -> str:
    """Remove XML namespace from tag name."""
    if '}' in tag:
        return tag.split('}', 1)[1]
    return tag


def strip_namespaces_recursive(elem: ET.Element) -> None:
    """Remove namespaces from element and all descendants."""
    elem.tag = strip_namespace(elem.tag)
    for child in elem:
        strip_namespaces_recursive(child)


def should_remove_element(tag: str) -> bool:
    """Check if element should be removed entirely."""
    return strip_namespace(tag).lower() in REMOVE_TAGS


def is_block_element(tag: str) -> bool:
    """Check if element is block-level (paragraph boundary)."""
    return strip_namespace(tag).lower() in BLOCK_TAGS


def normalize_whitespace(text: str) -> str:
    """Collapse multiple whitespace to single space."""
    return re.sub(r'\s+', ' ', text)


def normalize_text_final(text: str) -> str:
    """
    Final normalization of extracted text:
    - Unicode normalize (NFC)
    - Collapse whitespace within paragraphs
    - Preserve paragraph breaks (double newline)
    """
    # Unicode normalize
    text = unicodedata.normalize('NFC', text)
    
    # Split into paragraphs
    paragraphs = re.split(r'\n\s*\n', text)
    
    # Normalize each paragraph
    normalized = []
    for para in paragraphs:
        para = normalize_whitespace(para).strip()
        if para:
            normalized.append(para)
    
    # Rejoin with double newline
    return '\n\n'.join(normalized)


def canonicalize_xhtml(content: bytes) -> str:
    """
    Extract canonical text from XHTML content.
    
    Canonicalization rules:
    - Strip script, style, nav, meta, link, head elements
    - Normalize whitespace (collapse multiple spaces)
    - Preserve paragraph boundaries (double newline)
    - Unicode normalize (NFC)
    
    Returns the canonical text string.
    """
    try:
        root = ET.fromstring(content)
        strip_namespaces_recursive(root)
        text = _extract_canonical_text(root)
        return normalize_text_final(text)
    except ET.ParseError as e:
        # Fallback: crude text extraction
        text = content.decode('utf-8', errors='ignore')
        text = re.sub(r'<[^>]+>', ' ', text)
        return normalize_text_final(text)


def _extract_canonical_text(element: ET.Element) -> str:
    """
    Recursively extract text from element tree.
    Returns text with paragraph boundaries marked by double newlines.
    """
    tag = element.tag.lower() if element.tag else ''
    
    # Skip removed elements
    if tag in REMOVE_TAGS:
        return ''
    
    parts = []
    
    # Element's direct text
    if element.text:
        parts.append(normalize_whitespace(element.text))
    
    # Process children
    for child in element:
        child_text = _extract_canonical_text(child)
        if child_text:
            parts.append(child_text)
        
        # Child's tail text
        if child.tail:
            parts.append(normalize_whitespace(child.tail))
    
    text = ' '.join(p for p in parts if p).strip()
    
    # Add paragraph boundary for block elements
    if tag in BLOCK_TAGS and text:
        return '\n\n' + text + '\n\n'
    
    return text


def canonicalize_xhtml_with_positions(content: bytes) -> Tuple[str, List[dict]]:
    """
    Extract canonical text AND track positions of text nodes.
    
    Returns:
        (canonical_text, positions)
        
    positions is a list of dicts:
        {
            'element_path': [...],  # path of element indices
            'text_slot': 1|3|5|...,  # 1=elem.text, 3=child[0].tail, etc.
            'start_char': int,  # start in canonical text
            'end_char': int,  # end in canonical text
        }
    
    This is used for CFI resolution to map text slots to canonical offsets.
    """
    try:
        root = ET.fromstring(content)
        strip_namespaces_recursive(root)
        
        positions = []
        text_parts = []
        current_offset = [0]  # Use list to allow mutation in nested function
        
        def traverse(elem, path):
            tag = elem.tag.lower() if elem.tag else ''
            
            if tag in REMOVE_TAGS:
                return
            
            # Track whether we're in a block element
            is_block = tag in BLOCK_TAGS
            
            # Add paragraph break before block element
            if is_block and current_offset[0] > 0:
                text_parts.append('\n\n')
                current_offset[0] += 2
            
            # Element's text (slot 1)
            if elem.text:
                normalized = normalize_whitespace(elem.text)
                if normalized.strip():
                    start = current_offset[0]
                    text_parts.append(normalized)
                    current_offset[0] += len(normalized)
                    
                    positions.append({
                        'element_path': list(path),
                        'text_slot': 1,
                        'start_char': start,
                        'end_char': current_offset[0],
                        'raw_text': normalized
                    })
                    
                    # Add space separator
                    text_parts.append(' ')
                    current_offset[0] += 1
            
            # Process children
            for i, child in enumerate(elem):
                child_path = path + [i]
                traverse(child, child_path)
                
                # Child's tail (slot 3, 5, 7, ...)
                if child.tail:
                    normalized = normalize_whitespace(child.tail)
                    if normalized.strip():
                        start = current_offset[0]
                        text_parts.append(normalized)
                        current_offset[0] += len(normalized)
                        
                        # Tail belongs to parent element, slot = 3 + 2*child_index
                        positions.append({
                            'element_path': list(path),
                            'text_slot': 3 + 2 * i,
                            'start_char': start,
                            'end_char': current_offset[0],
                            'raw_text': normalized
                        })
                        
                        text_parts.append(' ')
                        current_offset[0] += 1
            
            # Add paragraph break after block element
            if is_block:
                text_parts.append('\n\n')
                current_offset[0] += 2
        
        traverse(root, [])
        
        raw_text = ''.join(text_parts)
        canonical = normalize_text_final(raw_text)
        
        return canonical, positions
        
    except ET.ParseError as e:
        # Fallback
        text = content.decode('utf-8', errors='ignore')
        text = re.sub(r'<[^>]+>', ' ', text)
        return normalize_text_final(text), []


def compute_sha256(text: str) -> str:
    """Compute SHA256 hash of canonical text."""
    import hashlib
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


if __name__ == '__main__':
    # Test canonicalization
    test_xhtml = b'''<?xml version="1.0" encoding="utf-8"?>
    <html xmlns="http://www.w3.org/1999/xhtml">
    <head><title>Test</title></head>
    <body>
        <script>alert('ignore');</script>
        <p>First paragraph with   extra   spaces.</p>
        <p>Second paragraph.</p>
        <div>
            <p>Nested paragraph.</p>
        </div>
    </body>
    </html>'''
    
    result = canonicalize_xhtml(test_xhtml)
    print("Canonical text:")
    print(repr(result))
    print()
    
    result2, positions = canonicalize_xhtml_with_positions(test_xhtml)
    print("With positions:")
    print(repr(result2))
    print("\nPositions:")
    for p in positions:
        print(f"  {p}")
