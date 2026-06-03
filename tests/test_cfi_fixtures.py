import json
from pathlib import Path

from src.calibre_native import LiveAnnotation
from src.cfi_fixtures import (
    build_fixture,
    capture_live_fixture,
    check_fixture,
    slugify,
)


def make_book(epub: Path) -> dict:
    return {
        "calibre_book_id": 8,
        "calibre_uuid": "uuid-8",
        "title": "Morning Star",
        "authors": ["Pierce Brown"],
        "preferred_epub_path": str(epub),
        "annots_key": "abc.json",
    }


def make_live() -> LiveAnnotation:
    return LiveAnnotation(
        annots_key="abc.json",
        annots_path="C:/annots/abc.json",
        epubcfi="epubcfi(/38/2/4/4/6/2:292)",
        timestamp=123.5,
    )


def make_resolved() -> dict:
    return {
        "spine_index": 18,
        "href": "text/part0010.html",
        "local_char_offset": 7,
        "spine_text_len": 18111,
        "text_preview": "Blood beads where buzzing metal pinches my scalp.",
    }


def test_build_fixture_stores_expected_probe_values(tmp_path):
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"fake epub")

    fixture = build_fixture("chapter 3 start", make_live(), make_book(epub), make_resolved())

    assert fixture["name"] == "chapter 3 start"
    assert fixture["book"]["epub_hash"]
    assert fixture["live"]["epubcfi"] == "epubcfi(/38/2/4/4/6/2:292)"
    assert fixture["expected"]["href"] == "text/part0010.html"
    assert fixture["expected"]["preview_contains"] == "Blood beads where buzzing metal pinches my scalp."


def test_capture_and_check_fixture_passes_with_same_probe(tmp_path):
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"fake epub")
    path = capture_live_fixture(
        name="chapter 3 start",
        live=make_live(),
        book=make_book(epub),
        resolved=make_resolved(),
        fixture_dir=tmp_path / "fixtures",
    )

    def fake_probe(epub_path, cfi):
        assert epub_path == str(epub)
        assert cfi == "epubcfi(/38/2/4/4/6/2:292)"
        return make_resolved()

    result = check_fixture(path, probe_runner=fake_probe)
    assert result["ok"]
    assert result["failures"] == []


def test_check_fixture_reports_mismatches(tmp_path):
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"fake epub")
    fixture = build_fixture("bad", make_live(), make_book(epub), make_resolved())
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")

    def fake_probe(epub_path, cfi):
        changed = make_resolved()
        changed["local_char_offset"] = 99
        return changed

    result = check_fixture(path, probe_runner=fake_probe)
    assert not result["ok"]
    assert any("local_char_offset_mismatch" in f for f in result["failures"])


def test_check_fixture_treats_hash_change_as_warning_by_default(tmp_path):
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"fake epub")
    fixture = build_fixture("hash warning", make_live(), make_book(epub), make_resolved())
    epub.write_bytes(b"changed epub")
    path = tmp_path / "hash-warning.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")

    result = check_fixture(path, probe_runner=lambda epub_path, cfi: make_resolved())
    assert result["ok"]
    assert any(w.startswith("epub_hash_changed") for w in result["warnings"])

    strict = check_fixture(path, probe_runner=lambda epub_path, cfi: make_resolved(), strict_hash=True)
    assert not strict["ok"]
    assert any(f.startswith("epub_hash_changed") for f in strict["failures"])


def test_check_fixture_warns_when_epub_path_changes_annots_key(tmp_path):
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"fake epub")
    fixture = build_fixture("path drift", make_live(), make_book(epub), make_resolved())
    moved = tmp_path / "moved.epub"
    moved.write_bytes(epub.read_bytes())
    fixture["book"]["epub_path"] = str(moved)
    path = tmp_path / "path-drift.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")

    result = check_fixture(path, probe_runner=lambda epub_path, cfi: make_resolved())

    assert result["ok"]
    assert any(w.startswith("annots_key_drift") for w in result["warnings"])


def test_slugify_has_timestamp_fallback_shape():
    assert slugify("Morning Star: Chapter 3") == "morning-star-chapter-3"
    assert slugify("!!!").startswith("fixture-")
