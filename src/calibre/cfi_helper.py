#!/usr/bin/env python
"""
Calibre CFI resolver helper script.

This script is executed via calibre-debug to resolve EPUBCFI strings
to spine_index + local_char_offset using Calibre's own CFI parser.

Usage:
    calibre-debug --exec-file tools/resolve_cfi_calibre.py -- "<epub_path>" "<epubcfi(...)>"

Output (JSON to stdout):
    {"spine_index": 12, "href": "index_split_012.html", "local_char_offset": 3456}

Errors are logged to stderr; stdout is JSON only.
"""

import sys
import json
import re
import unicodedata


def log_error(msg):
    """Log error to stderr."""
    print(f"[CFI-RESOLVER] ERROR: {msg}", file=sys.stderr)


def log_info(msg):
    """Log info to stderr."""
    print(f"[CFI-RESOLVER] {msg}", file=sys.stderr)


def output_result(result):
    """Output JSON result to stdout."""
    print(json.dumps(result))


def output_error(error_msg, details=None):
    """Output error as JSON to stdout."""
    result = {"error": error_msg}
    if details:
        result["details"] = details
    print(json.dumps(result))
    sys.exit(1)


# Block-level elements for canonicalization (must match canonicalize.py)
BLOCK_TAGS = frozenset({
    'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 
    'li', 'blockquote', 'section', 'article', 'header', 
    'footer', 'aside', 'figure', 'figcaption', 'pre'
})

REMOVE_TAGS = frozenset({
    'script', 'style', 'nav', 'meta', 'link', 'head'
})


def strip_namespace(tag):
    """Remove XML namespace from tag name."""
    if '}' in tag:
        return tag.split('}', 1)[1]
    return tag


def normalize_whitespace(text):
    """Collapse multiple whitespace to single space."""
    return re.sub(r'\s+', ' ', text)


def parse_cfi_string(epubcfi):
    """
    Parse EPUBCFI string to extract spine_index and steps.
    
    Example: epubcfi(/8/2/4/140/1:5)
    - First step /8 -> spine selector (8//2 - 1 = 3)
    - Remaining /2/4/140/1:5 -> content path with text offset
    
    Returns:
        {
            'spine_index': int,
            'content_steps': list of step dicts,
            'text_offset': int or None
        }
    """
    # Strip wrapper
    if epubcfi.startswith('epubcfi(') and epubcfi.endswith(')'):
        raw_path = epubcfi[8:-1]
    else:
        raw_path = epubcfi
    
    # Extract text offset (:N) from the ORIGINAL path first
    text_offset = None
    if ':' in raw_path:
        raw_path_no_offset, offset_str = raw_path.rsplit(':', 1)
        # Handle temporal/spatial offsets like ~1.5 or @100:200
        offset_str = offset_str.split('~')[0].split('@')[0]
        try:
            text_offset = int(offset_str)
        except ValueError:
            text_offset = 0
        raw_path = raw_path_no_offset
    
    # Split by ! if present (book-level vs content-level)
    if '!' in raw_path:
        book_part, content_part = raw_path.split('!', 1)
    else:
        # No ! means entire path is content path after spine selector
        book_part = raw_path
        content_part = ""
    
    # Parse all steps from book_part
    # Format: /N[id]/M[id]... where first N is package/spine, rest are content path
    all_steps = re.findall(r'/(\d+)(?:\[([^\]]*)\])?', book_part)
    
    if len(all_steps) < 1:
        return None
    
    # First step number determines spine index
    # CFI uses 2-based indexing, so spine_index = (first_num // 2) - 1
    first_num = int(all_steps[0][0])
    
    # Validate even number for element step
    if first_num % 2 != 0:
        log_error(f"First CFI step {first_num} is odd (expected even for element)")
        return None
    
    spine_index = (first_num // 2) - 1
    
    # Parse content steps (everything after first step)
    content_steps = []
    
    # Content path from after ! separator, or from remaining steps
    if content_part:
        steps_to_parse = re.findall(r'/(\d+)(?:\[([^\]]*)\])?', content_part)
    else:
        steps_to_parse = all_steps[1:]  # Skip first step (spine selector)
    
    for step_num_str, step_id in steps_to_parse:
        step_num = int(step_num_str)
        content_steps.append({
            'num': step_num,
            'id': step_id if step_id else None,
            'is_element': (step_num % 2 == 0),
            'is_text': (step_num % 2 == 1)
        })
    
    return {
        'spine_index': spine_index,
        'content_steps': content_steps,
        'text_offset': text_offset
    }


def resolve_element_path(root, steps):
    """
    Resolve element steps in the CFI to reach the target element.
    
    Steps with even numbers select child elements (1-indexed into elements only).
    Steps with odd numbers select text nodes (handled separately).
    
    Returns the target element.
    """
    current = root
    
    for i, step in enumerate(steps):
        if step['is_text']:
            # Text step - return current element, caller handles text selection
            break
        
        # Element step: num/2 is 1-based element index
        elem_index = (step['num'] // 2) - 1
        
        # Get child elements (excluding text nodes)
        children = list(current)
        
        log_info(f"Step {i}: /{step['num']} -> elem_index={elem_index}, children={len(children)}, current_tag={strip_namespace(current.tag)}")
        
        # Try ID-based lookup first
        if step['id']:
            for child in children:
                child_id = child.get('id') or child.get('{http://www.w3.org/XML/1998/namespace}id')
                if child_id == step['id']:
                    current = child
                    log_info(f"  -> found by ID: {step['id']}")
                    break
            else:
                # ID not found, fall back to index
                if 0 <= elem_index < len(children):
                    current = children[elem_index]
                    log_info(f"  -> ID not found, using index {elem_index}")
                else:
                    log_error(f"Step {i}: Element index {elem_index} out of range (have {len(children)} children)")
                    log_error(f"  Children tags: {[strip_namespace(c.tag) for c in children[:10]]}...")
                    return None
        else:
            if 0 <= elem_index < len(children):
                current = children[elem_index]
                log_info(f"  -> selected child {elem_index}: {strip_namespace(current.tag)}")
            else:
                log_error(f"Step {i}: Element index {elem_index} out of range (have {len(children)} children)")
                log_error(f"  Children tags: {[strip_namespace(c.tag) for c in children[:10]]}...")
                return None
    
    return current


def get_text_slot_content(element, slot_num):
    """
    Get text content for a CFI text slot.
    
    Text slots (odd numbers):
    - 1: element.text (text before first child)
    - 3: element[0].tail (text after first child)
    - 5: element[1].tail (text after second child)
    - etc.
    
    Returns the text content (may be empty string).
    """
    if slot_num == 1:
        return element.text or ""
    
    # Slots 3, 5, 7... correspond to tail of children 0, 1, 2...
    child_index = (slot_num - 3) // 2
    children = list(element)
    
    if 0 <= child_index < len(children):
        return children[child_index].tail or ""
    
    return ""


def compute_canonical_offset(root, target_element, text_slot, char_offset_in_slot):
    """
    Compute the canonical character offset by traversing the document
    in canonical order until we reach the target text position.
    
    This is a diagnostic traversal used by the live CFI probe. Durable anchors
    should eventually be generated from the same Calibre-compatible coordinate
    model rather than from a separate generic EPUB text stream.
    
    Returns local_char_offset within the spine document's canonical text.
    """
    canonical_offset = [0]
    found_offset = [None]
    
    def traverse(elem, path=[]):
        if found_offset[0] is not None:
            return
        
        tag = strip_namespace(elem.tag).lower() if elem.tag else ''
        
        # Skip removed elements
        if tag in REMOVE_TAGS:
            return
        
        is_block = tag in BLOCK_TAGS
        
        # Add paragraph break before block element
        if is_block and canonical_offset[0] > 0:
            canonical_offset[0] += 2  # \n\n
        
        # Check if this is our target element
        is_target = (elem is target_element)
        
        # Element's text (slot 1)
        if elem.text:
            text = normalize_whitespace(elem.text)
            if text.strip():
                if is_target and text_slot == 1:
                    # Found our target slot
                    found_offset[0] = canonical_offset[0] + min(char_offset_in_slot, len(text))
                    return
                
                canonical_offset[0] += len(text) + 1  # +1 for space separator
        
        # Process children
        for i, child in enumerate(elem):
            traverse(child, path + [i])
            
            if found_offset[0] is not None:
                return
            
            # Child's tail (slot 3, 5, 7, ...)
            if child.tail:
                text = normalize_whitespace(child.tail)
                if text.strip():
                    expected_slot = 3 + 2 * i
                    
                    if is_target and text_slot == expected_slot:
                        # Found our target slot
                        found_offset[0] = canonical_offset[0] + min(char_offset_in_slot, len(text))
                        return
                    
                    canonical_offset[0] += len(text) + 1
        
        # Add paragraph break after block element
        if is_block:
            canonical_offset[0] += 2
    
    traverse(root)
    
    return found_offset[0]


def canonical_text_for_preview(root):
    """
    Build a preview text stream with the same traversal/counting strategy used
    by compute_canonical_offset(). This is diagnostic text, not a durable
    preprocessing format.
    """
    parts = []

    def traverse(elem):
        tag = strip_namespace(elem.tag).lower() if elem.tag else ''

        if tag in REMOVE_TAGS:
            return

        is_block = tag in BLOCK_TAGS

        if is_block and parts:
            parts.append('\n\n')

        if elem.text:
            text = normalize_whitespace(elem.text)
            if text.strip():
                parts.append(text)
                parts.append(' ')

        for child in elem:
            traverse(child)
            if child.tail:
                text = normalize_whitespace(child.tail)
                if text.strip():
                    parts.append(text)
                    parts.append(' ')

        if is_block:
            parts.append('\n\n')

    traverse(root)
    return ''.join(parts)


def preview_around_offset(text, offset, radius=220):
    """Return a compact text preview around an offset."""
    if offset is None:
        return ""
    start = max(0, offset - radius)
    end = min(len(text), offset + radius)
    preview = text[start:end]
    preview = re.sub(r'\s+', ' ', preview).strip()
    if start > 0:
        preview = '...' + preview
    if end < len(text):
        preview = preview + '...'
    return preview


def main():
    if len(sys.argv) < 3:
        output_error("Usage: calibre-debug --exec-file resolve_cfi_calibre.py -- <epub_path> <epubcfi>")
    
    epub_path = sys.argv[1]
    epubcfi = sys.argv[2]
    
    log_info(f"Resolving CFI: {epubcfi}")
    log_info(f"EPUB: {epub_path}")
    
    try:
        # Import Calibre modules (only available under calibre-debug)
        from calibre.ebooks.oeb.polish.container import get_container
        from lxml import etree
    except ImportError as e:
        output_error(f"Calibre modules not available. Run with calibre-debug.", str(e))
    
    try:
        # Parse CFI
        parsed = parse_cfi_string(epubcfi)
        
        if parsed is None:
            output_error(f"Failed to parse CFI: {epubcfi}")
        
        spine_index = parsed['spine_index']
        content_steps = parsed['content_steps']
        text_offset = parsed['text_offset'] or 0
        
        log_info(f"Parsed: spine_index={spine_index}, steps={len(content_steps)}, text_offset={text_offset}")
        
        # Open EPUB container
        container = get_container(epub_path)
        
        # Get spine (returns list of (href, is_linear) tuples)
        spine = list(container.spine_names)
        
        if spine_index < 0 or spine_index >= len(spine):
            output_error(f"Spine index {spine_index} out of range [0, {len(spine)-1}]")
        
        # Extract href from tuple (href, is_linear)
        href = spine[spine_index][0]
        log_info(f"Spine item: {href}")
        
        # Parse the spine document
        root = container.parsed(href)
        
        if root is None:
            output_error(f"Failed to parse spine document: {href}")
        
        # Resolve element path (element steps only)
        element_steps = [s for s in content_steps if s['is_element']]
        
        log_info(f"Element steps to resolve: {[s['num'] for s in element_steps]}")
        log_info(f"Root element: {root.tag}")
        
        # CFI paths in EPUB:
        # - /2 typically refers to the root html element
        # - /4 refers to body (2nd child of html: head=index 0, body=index 1)
        # Since lxml gives us html as root, the first /2 step selects root itself
        # So we skip it and start navigation from root
        
        # Check if first step is /2 (selecting html root)
        if element_steps and element_steps[0]['num'] == 2:
            # Skip the first step - root is already html
            steps_to_resolve = element_steps[1:]
            log_info(f"Skipping first /2 step (root is html), remaining: {[s['num'] for s in steps_to_resolve]}")
        else:
            steps_to_resolve = element_steps
        
        target_elem = resolve_element_path(root, steps_to_resolve)
        
        if target_elem is None:
            output_error(f"Failed to resolve element path")
        
        # Determine text slot
        text_steps = [s for s in content_steps if s['is_text']]
        text_slot = 1  # Default to element.text
        
        if text_steps:
            text_slot = text_steps[-1]['num']
        
        log_info(f"Target: element={target_elem.tag}, text_slot={text_slot}, char_offset={text_offset}")
        
        # For canonical offset, we need to traverse from body (not root)
        # since that's where our canonicalization starts
        body = root.find('.//{http://www.w3.org/1999/xhtml}body')
        if body is None:
            body = root.find('.//body')
        if body is None:
            body = root
        
        # Compute canonical offset
        local_char_offset = compute_canonical_offset(body, target_elem, text_slot, text_offset)
        
        if local_char_offset is None:
            # Fallback: return 0 with warning
            log_error("Could not compute exact canonical offset, returning 0")
            local_char_offset = 0
        
        log_info(f"Resolved: local_char_offset={local_char_offset}")

        preview_text = canonical_text_for_preview(body)
        preview = preview_around_offset(preview_text, local_char_offset)
        
        # Output result
        output_result({
            "spine_index": spine_index,
            "href": href,
            "local_char_offset": local_char_offset,
            "spine_text_len": len(preview_text),
            "text_preview": preview
        })
        
    except Exception as e:
        import traceback
        log_error(traceback.format_exc())
        output_error(f"Exception: {str(e)}")


if __name__ == '__main__':
    main()
