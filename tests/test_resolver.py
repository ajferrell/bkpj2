"""
Test script for CFI resolution and chunk lookup.

Usage:
    python tests/test_resolver.py <epub_path> [cfi1] [cfi2] ...
    
If no CFIs provided, attempts to read from Calibre annots.
"""

import sys
import json
import logging
from pathlib import Path

# Add parent directory to path for src imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.preprocessor import preprocess_epub
from src.resolver_calibre import CalibreCFIResolver
from src.chunk_index import ChunkIndex


def setup_logging():
    """Configure logging."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )


def find_calibre_annots():
    """Find Calibre annotation files."""
    import os
    appdata = os.environ.get('APPDATA', '')
    annots_dir = Path(appdata) / 'calibre' / 'viewer' / 'annots'
    
    if not annots_dir.exists():
        return []
    
    return sorted(annots_dir.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)


def extract_cfis_from_annots(annots_path):
    """Extract CFI strings from Calibre annotation file."""
    with open(annots_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    cfis = []
    
    # Look for last-read annotations
    for annot in data.get('annotations', []):
        if annot.get('type') == 'last-read' and annot.get('pos_type') == 'epubcfi':
            cfis.append(annot.get('pos'))
    
    return cfis


def test_preprocessor(epub_path):
    """Test preprocessing and show timeline summary."""
    print("\n" + "="*60)
    print("PREPROCESSING")
    print("="*60)
    
    timeline_path = preprocess_epub(epub_path)
    print(f"\nTimeline saved to: {timeline_path}")
    
    # Load and summarize
    with open(timeline_path, 'r', encoding='utf-8') as f:
        timeline = json.load(f)
    
    print(f"\nBook ID: {timeline['book_id']}")
    print(f"Total chars: {timeline['total_chars']}")
    print(f"Spine entries: {len(timeline['spine'])}")
    print(f"Chunks: {len(timeline['chunks'])}")
    
    print("\nSpine summary:")
    for i, entry in enumerate(timeline['spine'][:5]):
        print(f"  {i}: {entry['href'][:40]:<40} "
              f"start={entry['global_start_char']:>8}, len={entry['canonical_len']:>6}")
    if len(timeline['spine']) > 5:
        print(f"  ... and {len(timeline['spine']) - 5} more")
    
    print("\nChunk summary:")
    for chunk in timeline['chunks'][:5]:
        print(f"  {chunk['chunk_id']:>3}: [{chunk['start_char_global']:>8}, {chunk['end_char_global']:>8}) "
              f"spine={chunk.get('start_doc_spine_index', '?')}, {chunk.get('word_count', '?')} words")
    if len(timeline['chunks']) > 5:
        print(f"  ... and {len(timeline['chunks']) - 5} more")
    
    return timeline_path


def test_chunk_index(timeline_path):
    """Test chunk index lookups."""
    print("\n" + "="*60)
    print("CHUNK INDEX TEST")
    print("="*60)
    
    index = ChunkIndex(timeline_path)
    
    print(f"\nBook ID: {index.book_id}")
    print(f"Spine entries: {index.get_spine_count()}")
    print(f"Chunks: {index.get_chunk_count()}")
    print(f"Total length: {index.get_total_length()} chars")
    
    # Test some lookups
    test_offsets = [0, 1000, 5000, 10000, index.get_total_length() // 2, index.get_total_length() - 100]
    
    print("\nLookup tests:")
    for offset in test_offsets:
        if offset < 0 or offset >= index.get_total_length():
            continue
        chunk = index.lookup_chunk(offset)
        if chunk:
            print(f"  offset {offset:>8} -> chunk {chunk.chunk_id:>3} "
                  f"[{chunk.start_char_global}, {chunk.end_char_global})")
        else:
            print(f"  offset {offset:>8} -> NOT FOUND")


def test_cfi_resolution(epub_path, timeline_path, cfis):
    """Test CFI resolution."""
    print("\n" + "="*60)
    print("CFI RESOLUTION TEST")
    print("="*60)
    
    resolver = CalibreCFIResolver(epub_path, timeline_path)
    
    for cfi in cfis:
        print(f"\nCFI: {cfi}")
        
        result = resolver.resolve(cfi)
        
        if result:
            print(f"  spine_index: {result.spine_index}")
            print(f"  href: {result.href}")
            print(f"  local_char_offset: {result.local_char_offset}")
            print(f"  global_char_offset: {result.global_char_offset}")
            print(f"  chunk_id: {result.chunk_id}")
        else:
            print("  FAILED to resolve")


def main():
    setup_logging()
    
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_resolver.py <epub_path> [cfi1] [cfi2] ...")
        print("\nIf no CFIs provided, will look for them in Calibre annots.")
        sys.exit(1)
    
    epub_path = sys.argv[1]
    cfis = sys.argv[2:] if len(sys.argv) > 2 else []
    
    # If no CFIs provided, try to get from Calibre annots
    if not cfis:
        annots_files = find_calibre_annots()
        if annots_files:
            print(f"\nFound {len(annots_files)} Calibre annotation files")
            for annots_path in annots_files[:3]:  # Check first 3
                found_cfis = extract_cfis_from_annots(annots_path)
                if found_cfis:
                    print(f"  {annots_path.name}: {len(found_cfis)} CFIs")
                    cfis.extend(found_cfis)
        
        if not cfis:
            print("\nNo CFIs found in Calibre annots. Using test CFIs.")
            # Generate some test CFIs for different spine positions
            cfis = [
                "epubcfi(/6/2!/4/2:100)",
                "epubcfi(/8/2/4/1:0)",
                "epubcfi(/10/2/4/2/1:50)",
            ]
    
    print(f"\nEPUB: {epub_path}")
    print(f"CFIs to test: {len(cfis)}")
    for cfi in cfis[:5]:
        print(f"  {cfi}")
    if len(cfis) > 5:
        print(f"  ... and {len(cfis) - 5} more")
    
    # Step 1: Preprocess
    timeline_path = test_preprocessor(epub_path)
    
    # Step 2: Test chunk index
    test_chunk_index(timeline_path)
    
    # Step 3: Test CFI resolution
    if cfis:
        test_cfi_resolution(epub_path, timeline_path, cfis[:10])  # Test first 10
    
    print("\n" + "="*60)
    print("DONE")
    print("="*60)


if __name__ == '__main__':
    main()
