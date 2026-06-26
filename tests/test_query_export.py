import json

from main import build_parser
from src.anchors import (
    inspect_text_path,
    load_timeline,
    prepare_book_timeline,
    source_units_path,
)
from src.calibre_native import save_manifest
from src.query_export import (
    build_query_record,
    build_query_span,
    drift_warnings_for_export,
    load_text_blocks_for_export,
    query_records_path,
    validate_query_record,
)


def fake_book(tmp_path, calibre_book_id=42):
    epub = tmp_path / f"book-{calibre_book_id}.epub"
    epub.write_bytes(b"fake epub")
    return {
        "calibre_book_id": calibre_book_id,
        "calibre_uuid": f"uuid-{calibre_book_id}",
        "title": "Book",
        "authors": ["Author"],
        "library_path": str(tmp_path),
        "calibre_path": "Author/Book",
        "formats": {"EPUB": str(epub)},
        "preferred_epub_path": str(epub),
        "annots_key": "abc.json",
        "prepared": True,
    }


def fake_spine():
    return [
        {
            "spine_index": 0,
            "href": "chapter.xhtml",
            "text": "\n\n".join(
                [
                    "A cold quiet room waits beneath the house.",
                    "The family whispers while distant thunder grows.",
                    "A locked door rattles once and then falls still.",
                    "Someone descends the stairs with a covered lamp.",
                ]
            ),
        },
        {
            "spine_index": 1,
            "href": "next.xhtml",
            "text": "Bright morning returns.",
        },
    ]


def prepare_fake_book(tmp_path, debug_text=True):
    book = fake_book(tmp_path)

    def extractor(epub_path):
        assert epub_path == book["preferred_epub_path"]
        return fake_spine()

    data_dir = tmp_path / "data"
    prepare_book_timeline(
        book,
        data_dir=data_dir,
        target_words=8,
        min_words=4,
        debug_text=debug_text,
        spine_extractor=extractor,
    )
    return book, data_dir


def test_load_text_blocks_uses_debug_text_sidecar(tmp_path):
    book, data_dir = prepare_fake_book(tmp_path, debug_text=True)
    timeline = load_timeline(data_dir, book["calibre_book_id"])

    blocks = load_text_blocks_for_export(data_dir, book, timeline=timeline)

    assert blocks[0]["unit_id"] == 0
    assert blocks[0]["_text"] == "A cold quiet room waits beneath the house."
    assert source_units_path(data_dir, book["calibre_book_id"]).exists()
    assert inspect_text_path(data_dir, book["calibre_book_id"]).exists()


def test_load_text_blocks_can_rehydrate_from_epub_extraction(tmp_path):
    book, data_dir = prepare_fake_book(tmp_path, debug_text=False)
    timeline = load_timeline(data_dir, book["calibre_book_id"])

    blocks = load_text_blocks_for_export(
        data_dir,
        book,
        timeline=timeline,
        spine_extractor=lambda epub_path: fake_spine(),
    )

    assert blocks[1]["_text"] == "The family whispers while distant thunder grows."


def test_build_query_span_expands_adjacent_text_blocks_within_spine(tmp_path):
    book, data_dir = prepare_fake_book(tmp_path)
    timeline = load_timeline(data_dir, book["calibre_book_id"])
    blocks = load_text_blocks_for_export(data_dir, book, timeline=timeline)

    span = build_query_span(
        book=book,
        timeline=timeline,
        text_blocks=blocks,
        spine_index=0,
        local_char_offset=45,
        source_cfi="epubcfi(/6/2:45)",
        target_words=18,
        min_words=12,
        max_words=25,
    )

    assert span["span_id"] == "book-42-spine-0-tb-0-3"
    assert span["text_block_start"] == 0
    assert span["text_block_end"] == 3
    assert "distant thunder" in span["excerpt"]
    assert span["source_cfi"] == "epubcfi(/6/2:45)"
    assert span["boundary_out"]["text_block_id"] == 3


def test_query_record_is_schema_valid_and_stable(tmp_path):
    book, data_dir = prepare_fake_book(tmp_path)
    timeline = load_timeline(data_dir, book["calibre_book_id"])
    blocks = load_text_blocks_for_export(data_dir, book, timeline=timeline)
    span = build_query_span(
        book=book,
        timeline=timeline,
        text_blocks=blocks,
        spine_index=0,
        local_char_offset=10,
        target_words=12,
        min_words=8,
        max_words=20,
    )

    record = build_query_record(
        book=book,
        timeline=timeline,
        span=span,
        query_text="dark quiet instrumental suspense; low strings; no vocals",
        negative_text="lyrics, comedy",
    )
    again = build_query_record(
        book=book,
        timeline=timeline,
        span=span,
        query_text="dark quiet instrumental suspense; low strings; no vocals",
        negative_text="lyrics, comedy",
    )

    assert validate_query_record(record) == []
    assert record == again
    assert record["handoff"]["target"] == "music-retrieval-lab"
    assert record["query"]["generation_method"] == "manual_v1"


def test_drift_warnings_are_exposed_for_export(tmp_path):
    book, data_dir = prepare_fake_book(tmp_path)
    timeline = load_timeline(data_dir, book["calibre_book_id"])
    book["annots_key"] = "changed.json"

    warnings = drift_warnings_for_export(book, timeline)

    assert any(w.startswith("annots_key_changed") for w in warnings)


def test_export_query_cli_writes_one_jsonl_record(tmp_path, capsys):
    book, data_dir = prepare_fake_book(tmp_path)
    manifest = {
        "libraries": [str(tmp_path)],
        "books": [book],
    }
    save_manifest(manifest, data_dir=data_dir)
    output = tmp_path / "queries.jsonl"
    parser = build_parser()
    args = parser.parse_args(
        [
            "--data-dir",
            str(data_dir),
            "export-query",
            "42",
            "--spine-index",
            "0",
            "--local-char-offset",
            "20",
            "--query-text",
            "subdued ominous instrumental tension; no vocals",
            "--target-words",
            "12",
            "--min-words",
            "8",
            "--max-words",
            "18",
            "--output",
            str(output),
        ]
    )

    assert args.func(args) == 0

    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["schema_version"] == 1
    assert record["book"]["calibre_book_id"] == 42
    assert record["span"]["selection_method"] == "manual_resolved_position_v1"
    assert record["query"]["text"] == "subdued ominous instrumental tension; no vocals"
    assert query_records_path(data_dir, 42).parent.exists()
    captured = capsys.readouterr()
    assert "Exported query record:" in captured.out
