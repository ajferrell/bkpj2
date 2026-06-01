"""
Calibre-native adaptive ambience command line.

The rebuilt entry point starts with the coordinate spine:
Calibre library import, deterministic viewer annotation mapping, and live CFI
inspection. Semantic regions and audio scoring build on this foundation later.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from src.calibre_native import (
    compute_annots_key,
    default_calibre_annots_dir,
    find_book,
    find_book_by_annots_key,
    import_calibre_library,
    iter_manifest_books,
    newest_live_annotation,
    read_live_annotation_for_book,
)
from src.anchors import (
    find_anchor_for_position,
    find_region_for_anchor,
    load_timeline,
    prepare_book_timeline,
    timeline_path,
)
from src.cfi_fixtures import (
    capture_live_fixture,
    check_fixtures,
    current_live_probe,
    default_fixture_dir,
    run_calibre_cfi_probe,
    timestamp_name,
)


def cmd_import_calibre(args: argparse.Namespace) -> int:
    books, path = import_calibre_library(args.library_path, data_dir=args.data_dir)
    epub_books = [b for b in books if b.preferred_epub_path]
    print(f"Imported Calibre library: {Path(args.library_path).resolve()}")
    print(f"Books with EPUB: {len(epub_books)}")
    print(f"Manifest: {path}")
    if epub_books:
        print("\nSample:")
        for book in epub_books[: args.limit]:
            print(f"  {book.calibre_book_id}: {book.title} - {book.display_author}")
            print(f"     EPUB: {book.preferred_epub_path}")
            print(f"     Annots: {book.annots_key}")
    return 0


def cmd_list_books(args: argparse.Namespace) -> int:
    books = list(iter_manifest_books(args.data_dir))
    if args.epub_only:
        books = [b for b in books if b.get("preferred_epub_path")]
    if args.query:
        q = args.query.casefold()
        books = [
            b for b in books
            if q in (b.get("title") or "").casefold()
            or q in " ".join(b.get("authors") or []).casefold()
            or q == str(b.get("calibre_book_id"))
        ]

    if not books:
        print("No imported books found. Run import-calibre first.")
        return 0

    for book in books[: args.limit]:
        authors = ", ".join(book.get("authors") or []) or "Unknown author"
        status = "EPUB" if book.get("preferred_epub_path") else "no EPUB"
        print(f"{book.get('calibre_book_id')}: {book.get('title')} - {authors} [{status}]")
        if args.verbose:
            print(f"  UUID: {book.get('calibre_uuid')}")
            print(f"  EPUB: {book.get('preferred_epub_path')}")
            print(f"  Annots: {book.get('annots_key')}")
    if len(books) > args.limit:
        print(f"\n... {len(books) - args.limit} more")
    return 0


def cmd_prepare_book(args: argparse.Namespace) -> int:
    book = find_book(args.query, args.data_dir)
    path = prepare_book_timeline(
        book,
        data_dir=args.data_dir,
        target_words=args.target_words,
        min_words=args.min_words,
        regions=args.regions,
    )
    timeline = load_timeline(args.data_dir, book["calibre_book_id"])
    print(f"Prepared book: {book.get('title')}")
    print(f"Timeline: {path}")
    print(f"Spine items: {len(timeline.get('spine', []))}")
    print(f"Source units: {len(timeline.get('source_units', []))}")
    print(f"Anchors: {len(timeline.get('anchors', []))}")
    if args.regions:
        print(f"Regions: {len(timeline.get('regions', []))}")
    return 0


def cmd_inspect_book(args: argparse.Namespace) -> int:
    book = find_book(args.query, args.data_dir)
    _print_book(book)

    if args.anchors:
        _print_anchor_summary(book, args.data_dir, limit=args.anchor_limit)

    if args.regions:
        _print_region_summary(book, args.data_dir, limit=args.region_limit)

    if args.live:
        print("\nLive annotation:")
        live = read_live_annotation_for_book(book, annots_dir=args.annots_dir)
        if not live:
            print("  No live annotation file/position found for this book.")
            print(f"  Expected: {(Path(args.annots_dir) if args.annots_dir else default_calibre_annots_dir()) / (book.get('annots_key') or '')}")
            return 0
        _print_live(live)

        if args.resolve_cfi:
            print("\nCalibre CFI probe:")
            result = run_calibre_cfi_probe(book.get("preferred_epub_path"), live.epubcfi)
            _print_probe_result(result)
            if args.anchors and not result.get("error"):
                _print_live_anchor(book, result, args.data_dir)
            if args.regions and not result.get("error"):
                _print_live_region(book, result, args.data_dir)

    return 0


def cmd_inspect_live(args: argparse.Namespace) -> int:
    live = newest_live_annotation(args.annots_dir)
    if not live:
        print("No live Calibre annotation position found.")
        print(f"Looked in: {Path(args.annots_dir) if args.annots_dir else default_calibre_annots_dir()}")
        return 0

    print("Newest live annotation:")
    _print_live(live)

    book = find_book_by_annots_key(live.annots_key, args.data_dir)
    if not book:
        print("\nNo imported book matches this annots key.")
        print("Run import-calibre for the library containing the opened book.")
        return 0

    print("\nMatched book:")
    _print_book(book)

    if args.resolve_cfi:
        print("\nCalibre CFI probe:")
        result = run_calibre_cfi_probe(book.get("preferred_epub_path"), live.epubcfi)
        _print_probe_result(result)

    return 0


def cmd_watch_live(args: argparse.Namespace) -> int:
    fixture_dir = Path(args.fixture_dir) if args.fixture_dir else default_fixture_dir(args.data_dir)
    last_signature = None
    print("Watching newest Calibre viewer position. Press Ctrl+C to stop.")
    if args.capture:
        print("Prompt controls: [c]apture, [enter] refresh, [q]uit")

    try:
        while True:
            live, book, result = current_live_probe(
                data_dir=args.data_dir,
                annots_dir=args.annots_dir,
                probe_runner=run_calibre_cfi_probe if args.resolve_cfi else _no_probe,
            )
            signature = (
                live.annots_key if live else None,
                live.epubcfi if live else None,
                result.get("href") if result else None,
                result.get("local_char_offset") if result else None,
            )
            if args.always or signature != last_signature:
                print("\n" + "-" * 72)
                if not live:
                    print("No live Calibre annotation position found.")
                else:
                    _print_live(live)
                    if book:
                        print("\nMatched book:")
                        _print_book(book)
                    else:
                        print("\nNo imported book matches this annots key.")
                    if result:
                        print("\nCalibre CFI probe:")
                        _print_probe_result(result)
                last_signature = signature

            if args.capture:
                choice = input("\n[c]apture [enter] refresh [q]uit > ").strip().lower()
                if choice == "q":
                    return 0
                if choice == "c":
                    if not live or not book or not result:
                        print("Nothing capturable yet.")
                    elif result.get("error"):
                        print(f"Cannot capture failed probe: {result['error']}")
                    else:
                        name = input("Fixture name (blank for timestamp): ").strip() or timestamp_name("cfi")
                        path = capture_live_fixture(
                            name=name,
                            live=live,
                            book=book,
                            resolved=result,
                            fixture_dir=fixture_dir,
                            confirmed=args.confirmed,
                        )
                        print(f"Captured fixture: {path}")
            else:
                from src.cfi_fixtures import sleep_until_next

                sleep_until_next(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


def cmd_cfi_fixtures(args: argparse.Namespace) -> int:
    fixture_dir = Path(args.fixture_dir) if args.fixture_dir else default_fixture_dir(args.data_dir)
    if args.fixture_command == "list":
        paths = sorted(fixture_dir.glob("*.json"))
        if not paths:
            print(f"No fixtures found in {fixture_dir}")
            return 0
        for path in paths:
            print(path)
        return 0

    if args.fixture_command == "check":
        results = check_fixtures(
            fixture_dir,
            strict_hash=args.strict_hash,
            check_anchors=args.anchors,
            data_dir=args.data_dir,
        )
        if not results:
            print(f"No fixtures found in {fixture_dir}")
            return 0
        failures = 0
        for result in results:
            status = "PASS" if result["ok"] else "FAIL"
            print(f"{status} {result['name']} ({result['path']})")
            for warning in result.get("warnings", []):
                print(f"  - warning: {warning}")
            for failure in result["failures"]:
                print(f"  - {failure}")
            if args.anchors and result.get("anchor"):
                anchor = result["anchor"]
                pos = anchor["position"]
                print(
                    f"  anchor: {anchor['anchor_id']} "
                    f"spine={pos['spine_index']} "
                    f"offsets={pos['start_local_offset']}-{pos['end_local_offset']}"
                )
            if not result["ok"]:
                failures += 1
        print(f"\n{len(results) - failures}/{len(results)} fixtures passed")
        return 1 if failures else 0

    raise ValueError(f"Unknown cfi-fixtures command: {args.fixture_command}")


def cmd_annots_key(args: argparse.Namespace) -> int:
    print(compute_annots_key(args.epub_path))
    return 0


def _no_probe(epub_path: str | None, epubcfi: str) -> dict[str, Any]:
    return {"cfi": epubcfi, "probe_skipped": True}


def _print_book(book: dict[str, Any]) -> None:
    authors = ", ".join(book.get("authors") or []) or "Unknown author"
    print(f"Book: {book.get('title')} - {authors}")
    print(f"  Calibre id: {book.get('calibre_book_id')}")
    print(f"  UUID: {book.get('calibre_uuid')}")
    print(f"  Library: {book.get('library_path')}")
    print(f"  EPUB: {book.get('preferred_epub_path')}")
    print(f"  Annots key: {book.get('annots_key')}")


def _print_live(live: Any) -> None:
    print(f"  Annots file: {live.annots_path}")
    print(f"  Annots key: {live.annots_key}")
    print(f"  CFI: {live.epubcfi}")
    print(f"  Timestamp: {live.timestamp}")


def _print_probe_result(result: dict[str, Any]) -> None:
    if result.get("error"):
        print(f"  Error: {result['error']}")
    for key in ("spine_index", "href", "local_char_offset", "spine_text_len"):
        if key in result:
            print(f"  {key}: {result[key]}")
    if result.get("text_preview"):
        print(f"  text_preview: {result['text_preview']}")
    if result.get("_stderr_tail"):
        print("  Helper log tail:")
        for line in result["_stderr_tail"].splitlines():
            print(f"    {line}")


def _print_anchor_summary(book: dict[str, Any], data_dir: str, limit: int = 5) -> None:
    path = timeline_path(data_dir, book["calibre_book_id"])
    print("\nAnchor timeline:")
    if not path.exists():
        print(f"  No timeline found. Run: python main.py prepare-book \"{book.get('title')}\"")
        return
    timeline = load_timeline(data_dir, book["calibre_book_id"])
    print(f"  Path: {path}")
    print(f"  Spine items: {len(timeline.get('spine', []))}")
    print(f"  Anchors: {len(timeline.get('anchors', []))}")
    for anchor in timeline.get("anchors", [])[:limit]:
        pos = anchor["position"]
        text = anchor["text"]
        print(
            f"  #{anchor['anchor_id']} spine={pos['spine_index']} "
            f"{pos['start_local_offset']}-{pos['end_local_offset']} "
            f"words={text['word_count']} {text['preview']}"
        )


def _print_region_summary(book: dict[str, Any], data_dir: str, limit: int = 5) -> None:
    path = timeline_path(data_dir, book["calibre_book_id"])
    print("\nRegions:")
    if not path.exists():
        print(f"  No timeline found. Run: python main.py prepare-book \"{book.get('title')}\" --regions")
        return
    timeline = load_timeline(data_dir, book["calibre_book_id"])
    anchors = timeline.get("anchors", [])
    regions = timeline.get("regions", [])
    if anchors and not regions:
        print("  Warning: timeline has anchors but no regions. Run prepare-book with --regions.")
        return
    print(f"  Count: {len(regions)}")
    for region in regions[:limit]:
        boundary_in = ", ".join(region.get("boundary_in", {}).get("reasons", [])) or "none"
        boundary_out = ", ".join(region.get("boundary_out", {}).get("reasons", [])) or "none"
        stats = region.get("stats", {})
        print(
            f"  #{region['region_id']} anchors={region['anchor_start']}-{region['anchor_end']} "
            f"source_units={region['source_unit_start']}-{region['source_unit_end']} "
            f"anchors_count={stats.get('anchor_count')} words={stats.get('word_count')}"
        )
        print(f"     in: {boundary_in}")
        print(f"     out: {boundary_out}")
        print(f"     preview: {region.get('preview', '')}")


def _print_live_anchor(book: dict[str, Any], result: dict[str, Any], data_dir: str) -> None:
    path = timeline_path(data_dir, book["calibre_book_id"])
    print("\nActive anchor:")
    if not path.exists():
        print("  No prepared timeline.")
        return
    timeline = load_timeline(data_dir, book["calibre_book_id"])
    anchor = find_anchor_for_position(timeline, result["spine_index"], result["local_char_offset"])
    if not anchor:
        print("  No anchor found for resolved CFI position.")
        return
    pos = anchor["position"]
    text = anchor["text"]
    print(
        f"  #{anchor['anchor_id']} spine={pos['spine_index']} "
        f"offsets={pos['start_local_offset']}-{pos['end_local_offset']} "
        f"words={text['word_count']}"
    )
    print(f"  preview: {text['preview']}")


def _print_live_region(book: dict[str, Any], result: dict[str, Any], data_dir: str) -> None:
    path = timeline_path(data_dir, book["calibre_book_id"])
    print("\nActive region:")
    if not path.exists():
        print("  No prepared timeline.")
        return
    timeline = load_timeline(data_dir, book["calibre_book_id"])
    anchor = find_anchor_for_position(timeline, result["spine_index"], result["local_char_offset"])
    if not anchor:
        print("  No anchor found for resolved CFI position.")
        return
    if not timeline.get("regions"):
        print("  Warning: active anchor found, but timeline has no regions.")
        return
    region = find_region_for_anchor(timeline, anchor["anchor_id"])
    if not region:
        print("  No region found for active anchor.")
        return
    boundary_in = ", ".join(region.get("boundary_in", {}).get("reasons", [])) or "none"
    boundary_out = ", ".join(region.get("boundary_out", {}).get("reasons", [])) or "none"
    print(
        f"  #{region['region_id']} anchors={region['anchor_start']}-{region['anchor_end']} "
        f"active_anchor={anchor['anchor_id']}"
    )
    print(f"  in: {boundary_in}")
    print(f"  out: {boundary_out}")
    print(f"  preview: {region.get('preview', '')}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calibre-native adaptive ambience tooling",
    )
    parser.add_argument("--data-dir", default="data", help="Cache/data directory")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import-calibre", help="Import a Calibre library manifest")
    p.add_argument("library_path")
    p.add_argument("--limit", type=int, default=5, help="Sample rows to print")
    p.set_defaults(func=cmd_import_calibre)

    p = sub.add_parser("list-books", help="List imported Calibre books")
    p.add_argument("query", nargs="?")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--epub-only", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_list_books)

    p = sub.add_parser("prepare-book", help="Prepare anchor timeline for one imported book")
    p.add_argument("query", help="Title, author, Calibre id, or UUID fragment")
    p.add_argument("--target-words", type=int, default=350)
    p.add_argument("--min-words", type=int, default=180)
    p.add_argument("--regions", action="store_true", help="Build deterministic region records")
    p.set_defaults(func=cmd_prepare_book)

    p = sub.add_parser("inspect-book", help="Inspect an imported book")
    p.add_argument("query", help="Title, author, Calibre id, or UUID fragment")
    p.add_argument("--live", action="store_true", help="Read this book's live viewer annots file")
    p.add_argument("--resolve-cfi", action="store_true", help="Resolve live CFI with calibre-debug helper")
    p.add_argument("--anchors", action="store_true", help="Show prepared anchor timeline info")
    p.add_argument("--regions", action="store_true", help="Show prepared region timeline info")
    p.add_argument("--anchor-limit", type=int, default=5)
    p.add_argument("--region-limit", type=int, default=5)
    p.add_argument("--annots-dir", help="Override Calibre viewer annots directory")
    p.set_defaults(func=cmd_inspect_book)

    p = sub.add_parser("inspect-live", help="Inspect newest live Calibre viewer position")
    p.add_argument("--resolve-cfi", action="store_true", help="Resolve live CFI with calibre-debug helper")
    p.add_argument("--annots-dir", help="Override Calibre viewer annots directory")
    p.set_defaults(func=cmd_inspect_live)

    p = sub.add_parser("watch-live", help="Watch live Calibre viewer position")
    p.add_argument("--resolve-cfi", action="store_true", help="Resolve live CFI with calibre-debug helper")
    p.add_argument("--capture", action="store_true", help="Prompt to capture CFI fixtures")
    p.add_argument("--confirmed", action="store_true", help="Mark captured fixtures as visually confirmed")
    p.add_argument("--fixture-dir", help="Directory for captured CFI fixtures")
    p.add_argument("--annots-dir", help="Override Calibre viewer annots directory")
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument("--always", action="store_true", help="Print every poll instead of only changes")
    p.set_defaults(func=cmd_watch_live)

    p = sub.add_parser("cfi-fixtures", help="Manage captured CFI fixtures")
    p.add_argument("fixture_command", choices=["list", "check"])
    p.add_argument("--fixture-dir", help="Directory containing CFI fixtures")
    p.add_argument("--strict-hash", action="store_true", help="Fail checks when EPUB file hash changed")
    p.add_argument("--anchors", action="store_true", help="Also require fixtures to map to prepared anchors")
    p.set_defaults(func=cmd_cfi_fixtures)

    p = sub.add_parser("annots-key", help="Compute Calibre viewer annots filename for an EPUB path")
    p.add_argument("epub_path")
    p.set_defaults(func=cmd_annots_key)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except (FileNotFoundError, LookupError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
