import json
from pathlib import Path

from src.anchors import (
    build_anchors_from_spine,
    extract_source_units,
    find_anchor_for_position,
    inspect_position_chain,
    inspect_text_path,
    load_timeline,
    paragraph_spans,
    prepare_book_timeline,
    remove_inspect_text,
    remove_timeline,
    source_units_path,
    timeline_drift_warnings,
    timeline_path,
)
from src.calibre_native import LiveAnnotation


def fake_book(tmp_path: Path, calibre_book_id: int = 42) -> dict:
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"fake epub")
    return {
        "calibre_book_id": calibre_book_id,
        "calibre_uuid": f"uuid-{calibre_book_id}",
        "title": "Book",
        "authors": ["Author"],
        "preferred_epub_path": str(epub),
        "annots_key": "abc.json",
    }


def test_paragraph_spans_preserve_offsets():
    text = "One two.\n\nThree four five.\n\nSix."
    spans = paragraph_spans(text)

    assert spans[0]["start"] == 0
    assert spans[0]["end"] == len("One two.")
    assert spans[1]["text"] == "Three four five."
    assert spans[1]["word_count"] == 3


def test_source_units_preserve_order_and_offsets():
    spine = [
        {
            "spine_index": 0,
            "href": "one.xhtml",
            "text": "One two.\n\nThree four five.",
        },
        {
            "spine_index": 1,
            "href": "two.xhtml",
            "text": "Six seven.",
        },
    ]

    units = extract_source_units(spine)

    assert [unit["unit_id"] for unit in units] == [0, 1, 2]
    assert [unit["href"] for unit in units] == ["one.xhtml", "one.xhtml", "two.xhtml"]
    assert units[0]["start_local_offset"] == 0
    assert units[0]["end_local_offset"] == len("One two.")
    assert units[2]["start_local_offset"] == 0
    assert units[2]["kind"] == "paragraph"


def test_build_anchors_groups_paragraphs_and_finds_offset():
    spine = [
        {
            "spine_index": 2,
            "href": "chapter.xhtml",
            "text": "one two three.\n\nfour five six.\n\nseven eight nine.\n\nlast bit.",
            "text_len": 62,
        }
    ]

    anchors = build_anchors_from_spine(spine, target_words=6, min_words=3)
    assert len(anchors) == 2
    assert anchors[0]["position"]["spine_index"] == 2
    assert anchors[0]["text"]["word_count"] == 6

    timeline = {"anchors": anchors}
    anchor = find_anchor_for_position(timeline, spine_index=2, local_char_offset=spine[0]["text"].index("seven"))
    assert anchor is not None
    assert anchor["anchor_id"] == 1


def test_anchors_cover_contiguous_source_units_without_gaps():
    spine = [
        {
            "spine_index": 0,
            "href": "chapter.xhtml",
            "text": "one two.\n\nthree four.\n\nfive six.\n\nseven eight.",
        }
    ]

    anchors = build_anchors_from_spine(spine, target_words=2, min_words=1)

    assert [(a["source_unit_start"], a["source_unit_end"]) for a in anchors] == [
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 4),
    ]


def test_prepare_book_timeline_with_fake_extractor(tmp_path):
    book = fake_book(tmp_path, 42)

    def fake_extractor(epub_path):
        assert epub_path == book["preferred_epub_path"]
        return [
            {
                "spine_index": 0,
                "href": "chapter.xhtml",
                "text": "Alpha beta gamma.\n\nDelta epsilon zeta.",
                "text_len": 39,
            }
        ]

    path = prepare_book_timeline(
        book,
        data_dir=tmp_path / "data",
        target_words=6,
        min_words=3,
        spine_extractor=fake_extractor,
    )
    timeline = load_timeline(tmp_path / "data", 42)

    assert path.exists()
    assert timeline["schema_version"] == 5
    assert timeline["book"]["title"] == "Book"
    assert "source_units" not in timeline
    assert len(timeline["anchors"]) == 1
    assert timeline["anchors"][0]["source_unit_start"] == 0
    assert timeline["anchors"][0]["source_unit_end"] == 2
    assert timeline["anchors"][0]["text"]["word_count"] == 6
    assert "plain" not in timeline["anchors"][0]["text"]
    source_sidecar = json.loads(source_units_path(tmp_path / "data", 42).read_text(encoding="utf-8"))
    assert len(source_sidecar["source_units"]) == 2
    assert "_text" not in source_sidecar["source_units"][0]
    assert not inspect_text_path(tmp_path / "data", 42).exists()


def test_prepare_book_timeline_debug_text_writes_sidecar(tmp_path):
    book = fake_book(tmp_path, 44)

    def fake_extractor(epub_path):
        return [
            {
                "spine_index": 0,
                "href": "chapter.xhtml",
                "text": "Alpha beta gamma.\n\nDelta epsilon zeta.",
            }
        ]

    prepare_book_timeline(
        book,
        data_dir=tmp_path / "data",
        target_words=6,
        min_words=3,
        debug_text=True,
        spine_extractor=fake_extractor,
    )
    timeline = load_timeline(tmp_path / "data", 44)
    sidecar = inspect_text_path(tmp_path / "data", 44)

    assert sidecar.exists()
    assert "plain" not in timeline["anchors"][0]["text"]
    assert "source_units" not in timeline
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["anchors"]["0"]["plain"] == "Alpha beta gamma.\n\nDelta epsilon zeta."
    assert data["source_units"]["0"]["text"] == "Alpha beta gamma."


def test_inspect_position_chain_reports_book_to_anchor_chain():
    spine = [
        {
            "spine_index": 0,
            "href": "chapter.xhtml",
            "text": "\n\n".join(f"Paragraph {i} forest." for i in range(6)),
        }
    ]
    source_units = extract_source_units(spine)
    anchors = build_anchors_from_spine(spine, target_words=3, min_words=1)
    book = {
        "calibre_book_id": 1,
        "calibre_uuid": "uuid-1",
        "title": "Book",
        "authors": ["Author"],
        "preferred_epub_path": "book.epub",
        "annots_key": "abc.json",
    }
    live = LiveAnnotation("abc.json", "annots/abc.json", "epubcfi(/6/2:1)", 10.0)
    resolved = {
        "spine_index": 0,
        "href": "chapter.xhtml",
        "local_char_offset": spine[0]["text"].index("Paragraph 2"),
        "spine_text_len": len(spine[0]["text"]),
    }
    timeline = {
        "schema_version": 5,
        "book": {"epub_path": "book.epub", "annots_key": "abc.json"},
        "spine": spine,
        "source_units": source_units,
        "anchors": anchors,
    }

    chain = inspect_position_chain(book, timeline=timeline, live=live, resolved=resolved)

    assert chain["book"]["annots_key"] == "abc.json"
    assert chain["resolved"]["href"] == "chapter.xhtml"
    assert chain["anchor"]["anchor_id"] is not None


def test_timeline_drift_warnings_report_manifest_changes(tmp_path):
    old_epub = tmp_path / "old.epub"
    new_epub = tmp_path / "new.epub"
    old_epub.write_bytes(b"old")
    new_epub.write_bytes(b"new")
    book = {
        "preferred_epub_path": str(new_epub),
        "annots_key": "new.json",
    }
    timeline = {
        "book": {
            "epub_path": str(old_epub),
            "epub_hash": "not-current",
            "annots_key": "old.json",
        }
    }

    warnings = timeline_drift_warnings(book, timeline)

    assert any(w.startswith("epub_path_changed") for w in warnings)
    assert any(w.startswith("epub_hash_changed") for w in warnings)
    assert any(w.startswith("annots_key_changed") for w in warnings)


def test_clean_timeline_removes_only_timeline(tmp_path, monkeypatch):
    book = fake_book(tmp_path, 46)
    data_dir = tmp_path / "data"
    book_dir = data_dir / "books" / "46"
    book_dir.mkdir(parents=True)
    target = timeline_path(data_dir, 46)
    target.write_text("{}", encoding="utf-8")
    inspect_text_path(data_dir, 46).write_text("{}", encoding="utf-8")
    unlinked = []

    def fake_unlink(path):
        unlinked.append(path)

    monkeypatch.setattr(Path, "unlink", fake_unlink)

    assert remove_timeline(data_dir, book["calibre_book_id"]) is True

    assert unlinked == [target]
    assert inspect_text_path(data_dir, 46).exists()


def test_clean_inspect_text_removes_only_sidecar(tmp_path, monkeypatch):
    book = fake_book(tmp_path, 48)
    data_dir = tmp_path / "data"
    book_dir = data_dir / "books" / "48"
    book_dir.mkdir(parents=True)
    timeline_path(data_dir, 48).write_text("{}", encoding="utf-8")
    target = inspect_text_path(data_dir, 48)
    target.write_text("{}", encoding="utf-8")
    unlinked = []

    def fake_unlink(path):
        unlinked.append(path)

    monkeypatch.setattr(Path, "unlink", fake_unlink)

    assert remove_inspect_text(data_dir, book["calibre_book_id"]) is True

    assert timeline_path(data_dir, 48).exists()
    assert unlinked == [target]
