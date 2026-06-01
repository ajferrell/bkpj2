#!/usr/bin/env python
"""Extract Calibre-compatible spine text for anchor preparation.

This script runs under calibre-debug. It intentionally mirrors the diagnostic
text traversal used by cfi_helper.py so CFI local offsets can be compared
against prepared anchor ranges.
"""

import json
import re
import sys


BLOCK_TAGS = frozenset({
    'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'li', 'blockquote', 'section', 'article', 'header',
    'footer', 'aside', 'figure', 'figcaption', 'pre'
})

REMOVE_TAGS = frozenset({
    'script', 'style', 'nav', 'meta', 'link', 'head'
})


def output_result(result):
    print(json.dumps(result))


def output_error(error_msg, details=None):
    result = {"error": error_msg}
    if details:
        result["details"] = details
    print(json.dumps(result))
    sys.exit(1)


def strip_namespace(tag):
    if '}' in tag:
        return tag.split('}', 1)[1]
    return tag


def normalize_whitespace(text):
    return re.sub(r'\s+', ' ', text)


def canonical_text_for_preview(root):
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


def body_for_root(root):
    body = root.find('.//{http://www.w3.org/1999/xhtml}body')
    if body is None:
        body = root.find('.//body')
    return body if body is not None else root


def main():
    if len(sys.argv) < 2:
        output_error("Usage: calibre-debug --exec-file anchor_helper.py -- <epub_path>")

    epub_path = sys.argv[1]

    try:
        from calibre.ebooks.oeb.polish.container import get_container
    except ImportError as e:
        output_error("Calibre modules not available. Run with calibre-debug.", str(e))

    try:
        container = get_container(epub_path)
        spine_items = []
        for spine_index, spine_entry in enumerate(list(container.spine_names)):
            href = spine_entry[0]
            root = container.parsed(href)
            if root is None:
                text = ""
            else:
                text = canonical_text_for_preview(body_for_root(root))
            spine_items.append({
                "spine_index": spine_index,
                "href": href,
                "text": text,
                "text_len": len(text),
            })
        output_result({"spine": spine_items})
    except Exception as e:
        import traceback
        output_error(f"Exception: {str(e)}", traceback.format_exc())


if __name__ == '__main__':
    main()
