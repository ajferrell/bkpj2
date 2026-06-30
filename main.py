"""Calibre-native book-to-query command line."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.calibre_native import (
    annotation_file_diagnostics,
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
    inspect_position_chain,
    inspect_text_path,
    load_timeline,
    load_timeline_with_sidecars,
    prepare_book_timeline,
    remove_inspect_text,
    remove_timeline,
    source_units_path,
    timeline_drift_warnings,
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
from src.query_export import (
    FakeQueryGenerator,
    LocalCommandQueryGenerator,
    OllamaQueryGenerator,
    append_query_record,
    build_batch_query_spans,
    generate_query_records,
    build_query_record,
    build_query_span,
    drift_warnings_for_export,
    load_text_blocks_for_export,
    query_records_path,
)
from src.playback_preview import follow_live_audio, play_preview
from src.retrieval_run import (
    build_playback_plan,
    retrieval_runs_dir,
    run_retrieval_audio,
    write_retrieval_run_index,
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
        debug_text=args.debug_text,
    )
    timeline = load_timeline_with_sidecars(args.data_dir, book["calibre_book_id"])
    print(f"Prepared book: {book.get('title')}")
    print(f"Timeline: {path}")
    print(f"Spine items: {len(timeline.get('spine', []))}")
    print(f"Text blocks sidecar: {source_units_path(args.data_dir, book['calibre_book_id'])}")
    print(f"Anchors: {len(timeline.get('anchors', []))}")
    if args.debug_text:
        print(f"Inspect text: {inspect_text_path(args.data_dir, book['calibre_book_id'])}")
    return 0


def cmd_clean_book(args: argparse.Namespace) -> int:
    book = find_book(args.query, args.data_dir)
    requested = [args.timeline, args.inspect_text]
    if not any(requested):
        raise ValueError("Choose at least one cleanup flag: --timeline or --inspect-text")

    print(f"Cleaned book: {book.get('title')}")
    if args.timeline:
        removed = remove_timeline(args.data_dir, book["calibre_book_id"])
        status = "removed" if removed else "not found"
        print(f"  timeline: {status}")
    if args.inspect_text:
        removed = remove_inspect_text(args.data_dir, book["calibre_book_id"])
        status = "removed" if removed else "not found"
        print(f"  inspect_text: {status}")
    return 0


def cmd_inspect_book(args: argparse.Namespace) -> int:
    book = find_book(args.query, args.data_dir)
    _print_book(book)
    _print_timeline_drift(book, args.data_dir)

    if args.anchors:
        _print_anchor_summary(book, args.data_dir, limit=args.anchor_limit)

    if args.live:
        print("\nLive annotation:")
        live = read_live_annotation_for_book(book, annots_dir=args.annots_dir)
        if not live:
            print("  No live annotation file/position found for this book.")
            expected = (Path(args.annots_dir) if args.annots_dir else default_calibre_annots_dir()) / (book.get("annots_key") or "")
            print(f"  Expected: {expected}")
            _print_annotation_diagnostics(expected)
            return 0
        _print_live(live)

        if args.resolve_cfi:
            print("\nCalibre CFI probe:")
            result = run_calibre_cfi_probe(book.get("preferred_epub_path"), live.epubcfi)
            _print_probe_result(result)
            if args.anchors and not result.get("error"):
                _print_live_anchor(book, result, args.data_dir)
            if args.chain:
                print("\nCoordinate chain:")
                _print_position_chain(book, args.data_dir, live=live, resolved=result, as_json=args.json)
        elif args.chain:
            print("\nCoordinate chain:")
            _print_position_chain(book, args.data_dir, live=live, resolved=None, as_json=args.json)
    elif args.chain:
        print("\nCoordinate chain:")
        _print_position_chain(book, args.data_dir, live=None, resolved=None, as_json=args.json)

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
    else:
        result = None

    if args.chain:
        print("\nCoordinate chain:")
        _print_position_chain(book, args.data_dir, live=live, resolved=result, as_json=args.json)

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


def cmd_export_query(args: argparse.Namespace) -> int:
    book = find_book(args.query, args.data_dir)
    timeline = load_timeline_with_sidecars(args.data_dir, book["calibre_book_id"])
    warnings = drift_warnings_for_export(book, timeline)
    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    live = None
    if args.live:
        live = read_live_annotation_for_book(book, annots_dir=args.annots_dir)
        if not live:
            raise ValueError("No live annotation position found for this book")
        resolved = run_calibre_cfi_probe(book.get("preferred_epub_path"), live.epubcfi)
        if resolved.get("error"):
            raise ValueError(f"Could not resolve live CFI: {resolved['error']}")
        source_cfi = live.epubcfi
        selection_method = "live_cfi_expand_text_blocks_v1"
    else:
        if args.spine_index is None or args.local_char_offset is None:
            raise ValueError("Use --live or provide both --spine-index and --local-char-offset")
        href = _href_for_spine(timeline, args.spine_index)
        resolved = {
            "spine_index": args.spine_index,
            "href": href,
            "local_char_offset": args.local_char_offset,
            "resolver": "manual",
        }
        source_cfi = args.source_cfi
        selection_method = "manual_resolved_position_v1"

    query_text = _read_query_text(args)
    text_blocks = load_text_blocks_for_export(args.data_dir, book, timeline=timeline)
    span = build_query_span(
        book=book,
        timeline=timeline,
        text_blocks=text_blocks,
        spine_index=int(resolved["spine_index"]),
        local_char_offset=int(resolved["local_char_offset"]),
        source_cfi=source_cfi,
        resolved_position=resolved,
        selection_method=selection_method,
        target_words=args.target_words,
        min_words=args.min_words,
        max_words=args.max_words,
    )
    record = build_query_record(
        book=book,
        timeline=timeline,
        span=span,
        query_text=query_text,
        review_status=args.review_status,
    )
    output = Path(args.output) if args.output else query_records_path(args.data_dir, book["calibre_book_id"])
    append_query_record(output, record)

    print(f"Exported query record: {record['record_id']}")
    print(f"Span: {span['span_id']} words={span['word_count']} blocks={span['text_block_start']}-{span['text_block_end']}")
    print(f"Output: {output}")
    if live:
        print(f"Source CFI: {live.epubcfi}")
    return 0


def cmd_export_batch_spans(args: argparse.Namespace) -> int:
    book = find_book(args.query, args.data_dir)
    timeline = load_timeline_with_sidecars(args.data_dir, book["calibre_book_id"])
    warnings = drift_warnings_for_export(book, timeline)
    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    text_blocks = load_text_blocks_for_export(args.data_dir, book, timeline=timeline)
    spans = build_batch_query_spans(
        book=book,
        timeline=timeline,
        text_blocks=text_blocks,
        spine_index=args.spine_index,
        href=args.href,
        target_words=args.target_words,
        min_words=args.min_words,
        max_words=args.max_words,
        max_spans=args.max_spans,
    )
    output = Path(args.output) if args.output else query_records_path(args.data_dir, book["calibre_book_id"])

    for span in spans:
        record = build_query_record(
            book=book,
            timeline=timeline,
            span=span,
            query_text=args.placeholder_query_text or "",
            query_mode="needs_query",
            generation_method="batch_placeholder_v1",
            review_status="needs_query",
            allow_empty_query=True,
        )
        append_query_record(output, record)

    print(f"Exported batch query span records: {len(spans)}")
    print(f"Output: {output}")
    if spans:
        first = spans[0]
        last = spans[-1]
        print(
            "Blocks: "
            f"{first['text_block_start']}-{first['text_block_end']} "
            f"through {last['text_block_start']}-{last['text_block_end']}"
        )
    return 0


def cmd_generate_queries(args: argparse.Namespace) -> int:
    generator = _query_generator_from_args(args)
    output = Path(args.out) if args.out else _default_generated_output(args.input)
    summary = generate_query_records(
        input_path=args.input,
        output_path=output,
        generator=generator,
        prompt_version=args.prompt_version,
        generation_method=args.generation_method,
        cache_path=args.cache,
        errors_path=args.errors,
        overwrite=args.overwrite,
        limit=args.limit,
    )

    print(f"Generated query records: {summary['generated_count']}")
    print(f"Cache hits: {summary['cached_count']}")
    print(f"Failures: {summary['failed_count']}")
    print(f"Output: {summary['output_path']}")
    print(f"Cache: {summary['cache_path']}")
    if summary["failed_count"]:
        print(f"Errors: {summary['errors_path']}", file=sys.stderr)
        return 1
    return 0


def cmd_retrieve_audio(args: argparse.Namespace) -> int:
    record = run_retrieval_audio(
        query_records_path=args.query_records,
        retrieval_profile=args.retrieval_profile,
        profile_config_path=args.profile_config,
        output_dir=args.out,
        lab_project=args.lab_project,
        lab_executable=args.lab_executable,
        lab_python=args.lab_python,
        mode=args.mode,
        limit=args.limit,
        candidate_strategy=args.candidate_strategy,
        run_record_path=args.run_record,
    )

    print(f"Retrieval run record: {record['retrieval_run_record_path']}")
    print(f"Exit status: {record['exit_status']}")
    print(f"Stdout: {record['stdout_path']}")
    print(f"Stderr: {record['stderr_path']}")
    print(f"Results: {record['retrieval_package_path']}")
    print(f"Summary: {record['retrieval_summary_path']}")
    print(f"Top candidates: {len(record['top_candidates'])}")
    if args.verbose:
        _print_retrieval_verbose(record)
    return int(record["exit_status"])


def cmd_list_retrieval_runs(args: argparse.Namespace) -> int:
    book = find_book(args.query, args.data_dir)
    runs_dir = retrieval_runs_dir(args.data_dir, book["calibre_book_id"])
    index = write_retrieval_run_index(runs_dir, calibre_book_id=book["calibre_book_id"])
    if args.json:
        print(json.dumps(index, indent=2, ensure_ascii=False))
        return 0

    authors = ", ".join(book.get("authors") or []) or "Unknown author"
    print(f"Retrieval runs for: {book.get('title')} - {authors}")
    print(f"Runs dir: {runs_dir}")
    print(f"Index: {index['retrieval_run_index_path']}")
    runs = index.get("runs", [])
    if not runs:
        print("No retrieval runs found.")
        return 0

    for run in runs:
        coverage = run.get("top_candidate_coverage") or {}
        missing = run.get("missing_files") or []
        print(
            f"{run.get('run_id')} | exit={run.get('exit_status')} | "
            f"profile={run.get('retrieval_profile')} | created={run.get('created_at')} | "
            f"strategy={run.get('candidate_strategy')} | "
            f"top={coverage.get('with_candidate', 0)}/{coverage.get('total', 0)} | "
            f"missing={len(missing)}"
        )
        if missing:
            print(f"  missing files: {', '.join(missing)}")
        if args.verbose:
            print(f"  query records: {run.get('query_records_path')}")
            print(f"  results: {run.get('retrieval_package_path')}")
            print(f"  summary: {run.get('retrieval_summary_path')}")
            if run.get("review_report_html"):
                print(f"  review HTML: {run.get('review_report_html')}")
            for status in run.get("span_candidate_status") or []:
                marker = "yes" if status.get("has_top_candidate") else "no"
                print(
                    f"    span={status.get('span_id')} "
                    f"query={status.get('query_record_id')} "
                    f"status={status.get('status')} top_candidate={marker}"
                )
    return 0


def cmd_build_playback_plan(args: argparse.Namespace) -> int:
    plan = build_playback_plan(args.retrieval_run, output_path=args.out)
    if args.json:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0

    summary = plan["summary"]
    print(f"Playback plan: {plan['playback_plan_path']}")
    print(f"Retrieval run: {plan['retrieval_run_record_path']}")
    print(
        f"Entries: {summary['total']} | playable={summary['playable']} | "
        f"missing_master_path={summary['missing_master_path']} | "
        f"missing_master_file={summary['missing_master_file']}"
    )
    if args.verbose:
        for entry in plan["entries"]:
            print(
                f"  {entry.get('sequence')}: span={entry.get('span_id')} "
                f"query={entry.get('query_record_id')} status={entry.get('status')} "
                f"master={entry.get('master_audio_path') or '(none)'} "
                f"chunk={entry.get('chunk_path') or '(none)'}"
            )
    return 0


def cmd_play_preview(args: argparse.Namespace) -> int:
    result = play_preview(
        args.playback_plan,
        max_spans=args.max_spans,
        start_at=args.start_at,
        dwell_seconds=args.dwell_seconds,
        crossfade_seconds=args.crossfade_seconds,
        gain=args.gain,
        dry_run=args.dry_run,
    )

    schedule = result["schedule"]
    if not schedule:
        print("No playable entries found.")
        return 1
    print(f"Preview entries: {len(schedule)}")
    for item in schedule:
        print(
            f"  {item.get('sequence')}: status={item.get('status')} "
            f"span={item.get('span_id')} asset={item.get('asset_id')} "
            f"start={item.get('start_seconds')} master={item.get('master_audio_path')}"
        )
    if result["played"]:
        print(f"Played preview: {result['duration_seconds']:.1f}s at {result['sample_rate']} Hz")
    else:
        print(f"Did not play audio: {result['reason']}")
    return 0 if schedule else 1


def cmd_follow_live_audio(args: argparse.Namespace) -> int:
    book = find_book(args.query, args.data_dir)
    if (args.spine_index is None) != (args.local_char_offset is None):
        raise ValueError("Use both --spine-index and --local-char-offset for manual dry-run coordinates")
    if (args.spine_index is not None or args.local_char_offset is not None) and not args.dry_run:
        raise ValueError("Manual --spine-index/--local-char-offset follow mode is dry-run only")

    def read_position() -> dict[str, Any]:
        if args.spine_index is not None and args.local_char_offset is not None:
            return {
                "spine_index": args.spine_index,
                "href": _href_for_spine(load_timeline(args.data_dir, book["calibre_book_id"]), args.spine_index),
                "local_char_offset": args.local_char_offset,
                "resolver": "manual",
            }
        live = read_live_annotation_for_book(book, annots_dir=args.annots_dir)
        if not live:
            return {"error": "live_annotation_missing"}
        result = run_calibre_cfi_probe(book.get("preferred_epub_path"), live.epubcfi)
        if result.get("error"):
            return result
        result["source_cfi"] = live.epubcfi
        return result

    max_polls = 1 if args.once else args.max_polls
    print("Following live audio. Press Ctrl+C to stop.")
    try:
        events = follow_live_audio(
            playback_plan_path=args.playback_plan,
            position_reader=read_position,
            poll_interval=args.poll_interval,
            min_stable_polls=args.min_stable_polls,
            missing_grace_polls=args.missing_grace_polls,
            dwell_seconds=args.dwell_seconds,
            crossfade_seconds=args.crossfade_seconds,
            gain=args.gain,
            dry_run=args.dry_run,
            max_polls=max_polls,
            on_event=_follow_event_printer(verbose=args.verbose),
        )
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    return 0 if events else 1


def cmd_run_book_audio_pipeline(args: argparse.Namespace) -> int:
    book = find_book(args.query, args.data_dir)
    book_id = book["calibre_book_id"]
    book_dir = Path(args.data_dir) / "books" / str(book_id)

    timeline_file = timeline_path(args.data_dir, book_id)
    if args.overwrite_timeline or not timeline_file.exists():
        prepared_path = prepare_book_timeline(
            book,
            data_dir=args.data_dir,
            target_words=args.prepare_target_words,
            min_words=args.prepare_min_words,
            debug_text=args.debug_text,
        )
        timeline_status = "written"
    else:
        prepared_path = timeline_file
        timeline_status = "reused"

    timeline = load_timeline_with_sidecars(args.data_dir, book_id)
    for warning in drift_warnings_for_export(book, timeline):
        print(f"Warning: {warning}", file=sys.stderr)

    needs_query_path = (
        Path(args.needs_query_path)
        if args.needs_query_path
        else book_dir / "query_records.needs_query.jsonl"
    )
    if needs_query_path.exists() and not args.overwrite_needs_query:
        needs_query_status = "reused"
    else:
        needs_query_path.parent.mkdir(parents=True, exist_ok=True)
        if args.overwrite_needs_query:
            needs_query_path.write_text("", encoding="utf-8")
        text_blocks = load_text_blocks_for_export(args.data_dir, book, timeline=timeline)
        spans = build_batch_query_spans(
            book=book,
            timeline=timeline,
            text_blocks=text_blocks,
            spine_index=args.spine_index,
            href=args.href,
            target_words=args.target_words,
            min_words=args.min_words,
            max_words=args.max_words,
            max_spans=args.max_spans,
        )
        for span in spans:
            record = build_query_record(
                book=book,
                timeline=timeline,
                span=span,
                query_text="",
                query_mode="needs_query",
                generation_method="batch_placeholder_v1",
                review_status="needs_query",
                allow_empty_query=True,
            )
            append_query_record(needs_query_path, record)
        needs_query_status = f"written ({len(spans)} records)"

    generator = _query_generator_from_args(args)
    generated_path = (
        Path(args.generated_query_path)
        if args.generated_query_path
        else book_dir
        / (
            "query_records.generated."
            f"{_safe_artifact_token(generator.provider)}."
            f"{_safe_artifact_token(generator.model_id)}.jsonl"
        )
    )
    if generated_path.exists() and not args.overwrite_generated:
        generated_status = "reused"
    else:
        summary = generate_query_records(
            input_path=needs_query_path,
            output_path=generated_path,
            generator=generator,
            prompt_version=args.prompt_version,
            generation_method=args.generation_method,
            cache_path=args.cache,
            errors_path=args.errors,
            overwrite=args.overwrite_generated,
            limit=args.max_spans,
        )
        generated_status = (
            f"written ({summary['generated_count']} records, "
            f"{summary['cached_count']} cache hits, {summary['failed_count']} failures)"
        )
        if summary["failed_count"]:
            print(f"Generation errors: {summary['errors_path']}", file=sys.stderr)
            return 1

    run_id = args.run_id or _timestamp_run_id()
    run_dir = Path(args.run_dir) if args.run_dir else retrieval_runs_dir(args.data_dir, book_id) / run_id
    retrieval_run_record = run_dir / "retrieval_run.json"
    if run_dir.exists():
        if not args.reuse_retrieval_run:
            raise FileExistsError(f"Retrieval run directory already exists: {run_dir}")
        if not retrieval_run_record.exists():
            raise FileNotFoundError(f"Retrieval run record does not exist: {retrieval_run_record}")
        retrieval_status = "reused"
    else:
        record = run_retrieval_audio(
            query_records_path=generated_path,
            retrieval_profile=args.retrieval_profile,
            profile_config_path=args.profile_config,
            output_dir=run_dir,
            lab_project=args.lab_project,
            lab_executable=args.lab_executable,
            lab_python=args.lab_python,
            mode=args.mode,
            limit=args.retrieval_limit,
            candidate_strategy=args.candidate_strategy,
        )
        retrieval_run_record = Path(record["retrieval_run_record_path"])
        retrieval_status = f"written (exit {record['exit_status']})"
        if args.verbose:
            _print_retrieval_verbose(record)
        if record["exit_status"] != 0:
            print(f"Retrieval failed: {retrieval_run_record}", file=sys.stderr)
            return int(record["exit_status"])

    playback_plan_file = Path(args.playback_plan_path) if args.playback_plan_path else run_dir / "playback_plan.json"
    if playback_plan_file.exists() and not args.overwrite_playback_plan:
        playback_status = "reused"
    else:
        plan = build_playback_plan(retrieval_run_record, output_path=playback_plan_file)
        playback_plan_file = Path(plan["playback_plan_path"])
        playback_status = (
            f"written ({plan['summary']['playable']}/{plan['summary']['total']} playable)"
        )

    follow_command = _follow_live_audio_command(args.data_dir, book_id, playback_plan_file)
    print("Book audio pipeline complete")
    print(f"Book: {book.get('title')} ({book_id})")
    print(f"Timeline: {prepared_path} [{timeline_status}]")
    print(f"Needs-query records: {needs_query_path} [{needs_query_status}]")
    print(f"Generated query records: {generated_path} [{generated_status}]")
    print(f"Retrieval run record: {retrieval_run_record} [{retrieval_status}]")
    print(f"Playback plan: {playback_plan_file} [{playback_status}]")
    print(f"Follow live: {follow_command}")
    return 0


def _print_retrieval_verbose(record: dict[str, Any]) -> None:
    print("\nLab command:")
    print(record["lab_command"])
    _print_file_section("Lab stdout", record["stdout_path"])
    _print_file_section("Lab stderr", record["stderr_path"])


def _print_file_section(title: str, path_value: str) -> None:
    path = Path(path_value)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    print(f"\n{title}:")
    print(text.rstrip() if text.strip() else "(empty)")


def _follow_event_printer(*, verbose: bool = False):
    last_signature: tuple[Any, ...] | None = None

    def print_event(event: dict[str, Any]) -> None:
        nonlocal last_signature
        decision = event.get("decision") or {}
        entry = event.get("entry")
        action = event.get("action")
        status = decision.get("status")
        signature = (
            action,
            status,
            entry.get("span_id") if isinstance(entry, dict) else None,
            decision.get("error"),
            event.get("stable_polls") if action == "pending" else None,
        )
        if signature == last_signature:
            return
        last_signature = signature

        if isinstance(entry, dict):
            print(
                f"follow-live-audio: action={action} status={status} "
                f"span={entry.get('span_id')} sequence={entry.get('sequence')} "
                f"asset={entry.get('asset_id')} start={entry.get('start_seconds')}"
            )
            if verbose:
                span = entry.get("span") if isinstance(entry.get("span"), dict) else {}
                if span.get("query_text"):
                    print(f"  query: {span['query_text']}")
                if span.get("excerpt_preview"):
                    print(f"  text: {span['excerpt_preview']}")
                print(f"  master: {entry.get('master_audio_path')}")
                print(f"  chunk: {entry.get('chunk_path') or '(none)'}")
        else:
            print(
                f"follow-live-audio: action={action} status={status} "
                f"error={decision.get('error') or '(none)'}"
            )

    return print_event


def _no_probe(epub_path: str | None, epubcfi: str) -> dict[str, Any]:
    return {"cfi": epubcfi, "probe_skipped": True}


def _read_query_text(args: argparse.Namespace) -> str:
    if args.query_text and args.query_file:
        raise ValueError("Use either --query-text or --query-file, not both")
    if args.query_file:
        return Path(args.query_file).read_text(encoding="utf-8").strip()
    if args.query_text:
        return args.query_text.strip()
    raise ValueError("Provide manual query text with --query-text or --query-file")


def _query_generator_from_args(args: argparse.Namespace) -> FakeQueryGenerator | LocalCommandQueryGenerator | OllamaQueryGenerator:
    if args.provider == "fake":
        if args.model:
            generator = FakeQueryGenerator()
            generator.model_id = args.model
            return generator
        return FakeQueryGenerator()
    if args.provider == "local-command":
        if not args.command:
            raise ValueError("--command is required when --provider local-command")
        return LocalCommandQueryGenerator(
            command=args.command,
            args=args.command_arg,
            model_id=args.model,
        )
    if args.provider == "ollama":
        return OllamaQueryGenerator(
            base_url=args.ollama_url,
            model_id=args.model or "qwen3:4b-instruct",
            timeout_seconds=args.timeout,
            temperature=args.temperature,
            num_predict=args.num_predict,
            keep_alive=args.keep_alive,
        )
    raise ValueError(f"Unsupported query generator provider: {args.provider}")


def _default_generated_output(input_path: str | Path) -> Path:
    path = Path(input_path)
    if path.name == "query_records.jsonl":
        return path.with_name("query_records.generated.jsonl")
    if path.name == "query_records.needs_query.jsonl":
        return path.with_name("query_records.generated.jsonl")
    return path.with_suffix(path.suffix + ".generated.jsonl")


def _timestamp_run_id() -> str:
    return "run_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_artifact_token(value: str | None) -> str:
    token = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "-"
        for ch in (value or "unknown").lower()
    )
    return "-".join(part for part in token.split("-") if part) or "unknown"


def _follow_live_audio_command(data_dir: str, book_id: int | str, playback_plan_file: Path) -> str:
    return " ".join(
        [
            ".\\.venv\\Scripts\\python.exe",
            "main.py",
            "--data-dir",
            _quote_cli_arg(str(data_dir)),
            "follow-live-audio",
            str(book_id),
            _quote_cli_arg(str(playback_plan_file)),
            "--dry-run",
            "--once",
            "--verbose",
        ]
    )


def _quote_cli_arg(value: str) -> str:
    if not value or any(ch.isspace() for ch in value):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def _href_for_spine(timeline: dict[str, Any], spine_index: int) -> str | None:
    for item in timeline.get("spine", []):
        if item.get("spine_index") == spine_index:
            return item.get("href")
    return None


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


def _print_annotation_diagnostics(path: Path) -> None:
    diagnostics = annotation_file_diagnostics(path)
    print("  Annotation diagnostics:")
    print(f"    exists: {diagnostics['exists']}")
    print(f"    json_ok: {diagnostics['json_ok']}")
    print(f"    candidate_count: {diagnostics['candidate_count']}")
    if diagnostics.get("error"):
        print(f"    error: {diagnostics['error']}")


def _load_timeline_if_present(book: dict[str, Any], data_dir: str) -> dict[str, Any] | None:
    path = timeline_path(data_dir, book["calibre_book_id"])
    if not path.exists():
        return None
    return load_timeline(data_dir, book["calibre_book_id"])


def _print_timeline_drift(book: dict[str, Any], data_dir: str) -> None:
    timeline = _load_timeline_if_present(book, data_dir)
    if not timeline:
        return
    warnings = timeline_drift_warnings(book, timeline)
    if warnings:
        print("\nTimeline drift warnings:")
        for warning in warnings:
            print(f"  - {warning}")


def _print_position_chain(
    book: dict[str, Any],
    data_dir: str,
    live: Any | None,
    resolved: dict[str, Any] | None,
    as_json: bool = False,
) -> None:
    timeline = _load_timeline_if_present(book, data_dir)
    chain = inspect_position_chain(book, timeline=timeline, live=live, resolved=resolved)
    if as_json:
        print(json.dumps(chain, indent=2, ensure_ascii=False))
        return

    print(f"  book.epub_path: {chain['book'].get('epub_path')}")
    print(f"  book.annots_key: {chain['book'].get('annots_key')}")
    if chain.get("live"):
        print(f"  live.epubcfi: {chain['live'].get('epubcfi')}")
    else:
        print("  live: not available")
    if chain.get("resolved"):
        resolved_view = chain["resolved"]
        if resolved_view.get("error"):
            print(f"  resolved.error: {resolved_view['error']}")
        else:
            print(
                "  resolved: "
                f"spine={resolved_view.get('spine_index')} "
                f"href={resolved_view.get('href')} "
                f"offset={resolved_view.get('local_char_offset')}"
            )
    else:
        print("  resolved: not available")
    if chain.get("anchor"):
        anchor = chain["anchor"]
        print(
            "  anchor: "
            f"#{anchor.get('anchor_id')} "
            f"spine={anchor.get('spine_index')} "
            f"offsets={anchor.get('start_local_offset')}-{anchor.get('end_local_offset')}"
        )
        print(f"  anchor.preview: {anchor.get('preview')}")
    else:
        print("  anchor: not available")
    for warning in chain.get("warnings", []):
        print(f"  warning: {warning}")


def _print_anchor_summary(book: dict[str, Any], data_dir: str, limit: int = 5) -> None:
    path = timeline_path(data_dir, book["calibre_book_id"])
    print("\nAnchor timeline:")
    if not path.exists():
        print(f"  No timeline found. Run: python main.py prepare-book \"{book.get('title')}\"")
        return
    timeline = load_timeline_with_sidecars(data_dir, book["calibre_book_id"])
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

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calibre-native book-to-query tooling",
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
    p.add_argument("--debug-text", action="store_true", help="Write inspect_text.json with full debug text")
    p.set_defaults(func=cmd_prepare_book)

    p = sub.add_parser("clean-book", help="Remove generated cache artifacts for one imported book")
    p.add_argument("query", help="Title, author, Calibre id, or UUID fragment")
    p.add_argument("--timeline", action="store_true", help="Remove timeline.json")
    p.add_argument("--inspect-text", action="store_true", help="Remove inspect_text.json")
    p.set_defaults(func=cmd_clean_book)

    p = sub.add_parser("inspect-book", help="Inspect an imported book")
    p.add_argument("query", help="Title, author, Calibre id, or UUID fragment")
    p.add_argument("--live", action="store_true", help="Read this book's live viewer annots file")
    p.add_argument("--resolve-cfi", action="store_true", help="Resolve live CFI with calibre-debug helper")
    p.add_argument("--anchors", action="store_true", help="Show prepared anchor timeline info")
    p.add_argument("--anchor-limit", type=int, default=5)
    p.add_argument("--annots-dir", help="Override Calibre viewer annots directory")
    p.add_argument("--chain", action="store_true", help="Show the full coordinate chain in one structured view")
    p.add_argument("--json", action="store_true", help="Print --chain as JSON")
    p.set_defaults(func=cmd_inspect_book)

    p = sub.add_parser("inspect-live", help="Inspect newest live Calibre viewer position")
    p.add_argument("--resolve-cfi", action="store_true", help="Resolve live CFI with calibre-debug helper")
    p.add_argument("--annots-dir", help="Override Calibre viewer annots directory")
    p.add_argument("--chain", action="store_true", help="Show the full coordinate chain in one structured view")
    p.add_argument("--json", action="store_true", help="Print --chain as JSON")
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

    p = sub.add_parser("export-query", help="Export one manual audio-intent query JSONL record")
    p.add_argument("query", help="Title, author, Calibre id, or UUID fragment")
    p.add_argument("--live", action="store_true", help="Use this book's current live Calibre CFI position")
    p.add_argument("--annots-dir", help="Override Calibre viewer annots directory")
    p.add_argument("--spine-index", type=int, help="Resolved spine index for manual coordinate export")
    p.add_argument("--local-char-offset", type=int, help="Resolved local character offset for manual coordinate export")
    p.add_argument("--source-cfi", help="Optional source CFI to store with a manual resolved coordinate")
    p.add_argument("--query-text", help="Manual compact audio-intent query text")
    p.add_argument("--query-file", help="File containing manual compact audio-intent query text")
    p.add_argument("--output", help="JSONL output path; defaults to the prepared book data directory")
    p.add_argument("--target-words", type=int, default=800, help="Preferred query span size")
    p.add_argument("--min-words", type=int, default=500, help="Minimum query span size before stopping at max")
    p.add_argument("--max-words", type=int, default=1200, help="Maximum query span size")
    p.add_argument(
        "--review-status",
        default="unreviewed",
        choices=["unreviewed", "approved", "rejected"],
        help="Initial review status stored in the record",
    )
    p.set_defaults(func=cmd_export_query)

    p = sub.add_parser("export-batch-spans", help="Export deterministic query-span candidates for a prepared book")
    p.add_argument("query", help="Title, author, Calibre id, or UUID fragment")
    p.add_argument("--spine-index", type=int, help="Only export spans from one prepared spine index")
    p.add_argument("--href", help="Only export spans from one prepared spine href/chapter")
    p.add_argument("--max-spans", type=int, help="Maximum number of span records to write")
    p.add_argument("--output", help="JSONL output path; defaults to the prepared book data directory")
    p.add_argument("--target-words", type=int, default=800, help="Preferred query span size")
    p.add_argument("--min-words", type=int, default=500, help="Minimum query span size before stopping at max")
    p.add_argument("--max-words", type=int, default=1200, help="Maximum query span size")
    p.add_argument("--placeholder-query-text", default="", help="Optional placeholder query text for each record")
    p.set_defaults(func=cmd_export_batch_spans)

    p = sub.add_parser("generate-queries", help="Generate audio-intent query records from needs_query records")
    p.add_argument("--input", required=True, help="Input JSONL with needs_query span records")
    p.add_argument("--out", help="Generated JSONL output path")
    p.add_argument(
        "--provider",
        choices=["fake", "local-command", "ollama"],
        default="local-command",
        help="Generation adapter to use",
    )
    p.add_argument("--command", help="Local command executable; reads prompt on stdin and writes query text")
    p.add_argument("--command-arg", action="append", default=[], help="Argument passed to --command")
    p.add_argument(
        "--model",
        help="Model/provider id to store in generated query records; Ollama defaults to qwen3:4b-instruct",
    )
    p.add_argument("--ollama-url", default="http://localhost:11434", help="Ollama base URL for --provider ollama")
    p.add_argument("--timeout", type=float, default=60.0, help="Per-record provider timeout in seconds")
    p.add_argument("--temperature", type=float, default=0.2, help="Ollama generation temperature")
    p.add_argument("--num-predict", type=int, default=48, help="Ollama maximum generated tokens")
    p.add_argument("--keep-alive", help="Optional Ollama keep_alive duration")
    p.add_argument("--prompt-version", default="audio_intent_v1")
    p.add_argument("--generation-method", default="local_model_audio_intent_v1")
    p.add_argument("--cache", help="Generation cache JSON path")
    p.add_argument("--errors", help="Generation error JSONL sidecar path")
    p.add_argument("--overwrite", action="store_true", help="Overwrite the generated output if it exists")
    p.add_argument("--limit", type=int, help="Maximum number of needs_query records to process")
    p.set_defaults(func=cmd_generate_queries)

    p = sub.add_parser("retrieve-audio", help="Run music-retrieval-lab retrieval for query records")
    p.add_argument("--query-records", required=True, help="Generated or manual query-record JSONL input")
    p.add_argument("--retrieval-profile", required=True, help="Lab retrieval profile name")
    p.add_argument("--profile-config", help="Lab retrieval profile YAML path")
    p.add_argument("--out", required=True, help="Output directory for the lab package and retrieval-run record")
    p.add_argument("--lab-project", help="music-retrieval-lab checkout to use as the subprocess working directory")
    p.add_argument("--lab-executable", default="music-lab", help="music-lab executable to run")
    p.add_argument("--lab-python", help="Python executable for `-m music_retrieval_lab.cli`")
    p.add_argument(
        "--mode",
        choices=["package-only", "review-html"],
        default="package-only",
        help="Lab output mode",
    )
    p.add_argument("--limit", type=int, help="Review HTML row limit when --mode review-html is used")
    p.add_argument(
        "--candidate-strategy",
        choices=["top_ranked"],
        default="top_ranked",
        help="How bkpj2 materializes candidates from the lab package",
    )
    p.add_argument("--run-record", help="Retrieval-run JSON path; defaults to <out>/retrieval_run.json")
    p.add_argument("--verbose", action="store_true", help="Print captured lab command, stdout, and stderr")
    p.set_defaults(func=cmd_retrieve_audio)

    p = sub.add_parser("list-retrieval-runs", help="List retrieval-run packages for one imported book")
    p.add_argument("query", help="Title, author, Calibre id, or UUID fragment")
    p.add_argument("--json", action="store_true", help="Print the refreshed run index as JSON")
    p.add_argument("--verbose", action="store_true", help="Print package paths and per-span top-candidate status")
    p.set_defaults(func=cmd_list_retrieval_runs)

    p = sub.add_parser("build-playback-plan", help="Build a playback plan from one retrieval-run record")
    p.add_argument("retrieval_run", help="Path to a retrieval_run.json file")
    p.add_argument("--out", help="Output playback_plan.json path; defaults next to retrieval_run.json")
    p.add_argument("--json", action="store_true", help="Print the written playback plan as JSON")
    p.add_argument("--verbose", action="store_true", help="Print per-span playback plan status")
    p.set_defaults(func=cmd_build_playback_plan)

    p = sub.add_parser("play-preview", help="Play a fixed-dwell audio preview from a playback plan")
    p.add_argument("playback_plan", help="Path to playback_plan.json")
    p.add_argument("--max-spans", type=int, default=5, help="Maximum playable entries to preview")
    p.add_argument("--start-at", type=int, default=1, help="1-based playable-entry offset")
    p.add_argument("--dwell-seconds", type=float, default=20.0, help="Seconds to play each entry")
    p.add_argument("--crossfade-seconds", type=float, default=4.0, help="Overlap between entries")
    p.add_argument("--gain", type=float, default=0.8, help="Linear output gain")
    p.add_argument("--dry-run", action="store_true", help="Print the preview schedule without opening audio")
    p.set_defaults(func=cmd_play_preview)

    p = sub.add_parser("follow-live-audio", help="Follow live Calibre position with a playback plan")
    p.add_argument("query", help="Title, author, Calibre id, or UUID fragment")
    p.add_argument("playback_plan", help="Path to playback_plan.json")
    p.add_argument("--annots-dir", help="Override Calibre viewer annots directory")
    p.add_argument("--poll-interval", type=float, default=1.0, help="Seconds between live CFI polls")
    p.add_argument("--min-stable-polls", type=int, default=2, help="Polls required before switching entries")
    p.add_argument("--missing-grace-polls", type=int, default=3, help="Failed polls to tolerate before stopping audio")
    p.add_argument("--dwell-seconds", type=float, default=20.0, help="Compatibility validation value; live audio streams until the active span changes")
    p.add_argument("--crossfade-seconds", type=float, default=4.0, help="Transition overlap between entries")
    p.add_argument("--gain", type=float, default=0.8, help="Linear output gain")
    p.add_argument("--dry-run", action="store_true", help="Print live decisions without opening audio")
    p.add_argument("--verbose", action="store_true", help="Print query text, text preview, and audio paths on changes")
    p.add_argument("--once", action="store_true", help="Poll once and exit")
    p.add_argument("--max-polls", type=int, help="Maximum polls before exiting")
    p.add_argument("--spine-index", type=int, help="Manual resolved spine index for dry-run smoke tests")
    p.add_argument("--local-char-offset", type=int, help="Manual resolved local character offset for dry-run smoke tests")
    p.set_defaults(func=cmd_follow_live_audio)

    p = sub.add_parser("run-book-audio-pipeline", help="Run or reuse the standard book-to-audio artifact chain")
    p.add_argument("query", help="Title, author, Calibre id, or UUID fragment")
    p.add_argument("--run-id", help="Retrieval run id; defaults to a UTC timestamp")
    p.add_argument("--run-dir", help="Explicit retrieval run directory")
    p.add_argument("--needs-query-path", help="Existing or output needs-query JSONL path")
    p.add_argument("--generated-query-path", help="Existing or output generated-query JSONL path")
    p.add_argument("--playback-plan-path", help="Playback-plan output path")
    p.add_argument("--overwrite-timeline", action="store_true", help="Rebuild timeline/source-unit artifacts")
    p.add_argument(
        "--overwrite-needs-query",
        action="store_true",
        help="Overwrite needs-query records instead of reusing them",
    )
    p.add_argument(
        "--overwrite-generated",
        action="store_true",
        help="Overwrite generated query records instead of reusing them",
    )
    p.add_argument(
        "--overwrite-playback-plan",
        action="store_true",
        help="Overwrite playback_plan.json instead of reusing it",
    )
    p.add_argument("--reuse-retrieval-run", action="store_true", help="Reuse an existing retrieval run directory")
    p.add_argument("--debug-text", action="store_true", help="Write inspect_text.json when preparing the book")
    p.add_argument("--prepare-target-words", type=int, default=350, help="prepare-book target words")
    p.add_argument("--prepare-min-words", type=int, default=180, help="prepare-book minimum words")
    p.add_argument("--spine-index", type=int, help="Only export spans from one prepared spine index")
    p.add_argument("--href", help="Only export spans from one prepared spine href/chapter")
    p.add_argument("--max-spans", type=int, help="Limit exported/generated query records for smoke runs")
    p.add_argument("--target-words", type=int, default=800, help="Preferred query span size")
    p.add_argument("--min-words", type=int, default=500, help="Minimum query span size before stopping at max")
    p.add_argument("--max-words", type=int, default=1200, help="Maximum query span size")
    p.add_argument(
        "--provider",
        choices=["fake", "local-command", "ollama"],
        default="local-command",
        help="Generation adapter to use",
    )
    p.add_argument("--command", help="Local command executable; reads prompt on stdin and writes query text")
    p.add_argument("--command-arg", action="append", default=[], help="Argument passed to --command")
    p.add_argument("--model", help="Model/provider id to store in generated query records")
    p.add_argument("--ollama-url", default="http://localhost:11434", help="Ollama base URL for --provider ollama")
    p.add_argument("--timeout", type=float, default=60.0, help="Per-record provider timeout in seconds")
    p.add_argument("--temperature", type=float, default=0.2, help="Ollama generation temperature")
    p.add_argument("--num-predict", type=int, default=48, help="Ollama maximum generated tokens")
    p.add_argument("--keep-alive", help="Optional Ollama keep_alive duration")
    p.add_argument("--prompt-version", default="audio_intent_v1")
    p.add_argument("--generation-method", default="local_model_audio_intent_v1")
    p.add_argument("--cache", help="Generation cache JSON path")
    p.add_argument("--errors", help="Generation error JSONL sidecar path")
    p.add_argument("--retrieval-profile", required=True, help="Lab retrieval profile name")
    p.add_argument("--profile-config", help="Lab retrieval profile YAML path")
    p.add_argument("--lab-project", help="music-retrieval-lab checkout to use as the subprocess working directory")
    p.add_argument("--lab-executable", default="music-lab", help="music-lab executable to run")
    p.add_argument("--lab-python", help="Python executable for `-m music_retrieval_lab.cli`")
    p.add_argument(
        "--mode",
        choices=["package-only", "review-html"],
        default="package-only",
        help="Lab output mode",
    )
    p.add_argument("--retrieval-limit", type=int, help="Review HTML row limit when --mode review-html is used")
    p.add_argument(
        "--candidate-strategy",
        choices=["top_ranked"],
        default="top_ranked",
        help="How bkpj2 materializes candidates from the lab package",
    )
    p.add_argument("--verbose", action="store_true", help="Print captured lab command, stdout, and stderr")
    p.set_defaults(func=cmd_run_book_audio_pipeline)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except (FileExistsError, FileNotFoundError, LookupError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
