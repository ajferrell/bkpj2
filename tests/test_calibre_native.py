import json
from pathlib import Path

from src import calibre_native
from src.calibre_native import (
    annotation_file_diagnostics,
    CalibreBook,
    compute_annots_key,
    import_calibre_library,
    read_live_annotation_file,
)


def create_fake_library_files(root: Path) -> Path:
    (root / "metadata.db").write_bytes(b"fake calibre db")
    book_dir = root / "Author Name" / "Book Title (1)"
    book_dir.mkdir(parents=True)
    epub = book_dir / "Book Title - Author Name.epub"
    epub.write_bytes(b"fake epub")
    return epub


def test_import_calibre_library_builds_annots_key(tmp_path, monkeypatch):
    epub = create_fake_library_files(tmp_path)

    def fake_read_calibre_books(library: Path):
        assert library == tmp_path.resolve()
        return [
            CalibreBook(
                calibre_book_id=1,
                calibre_uuid="uuid-1",
                title="Book Title",
                authors=["Author Name"],
                library_path=str(library),
                calibre_path="Author Name/Book Title (1)",
                formats={"EPUB": str(epub.resolve())},
                preferred_epub_path=str(epub.resolve()),
                annots_key=compute_annots_key(epub),
            )
        ]

    monkeypatch.setattr(calibre_native, "_read_calibre_books", fake_read_calibre_books)

    books, manifest_path = import_calibre_library(tmp_path, data_dir=tmp_path / "data")

    assert manifest_path.exists()
    assert len(books) == 1
    assert books[0].preferred_epub_path == str(epub.resolve())
    assert books[0].annots_key == compute_annots_key(epub)


def test_read_live_annotation_file_handles_nested_annotations(tmp_path):
    path = tmp_path / "annots.json"
    path.write_text(
        json.dumps(
            {
                "annotations": [
                    {
                        "type": "last-read",
                        "pos_type": "epubcfi",
                        "pos": "epubcfi(/6/2/4/1:2)",
                        "timestamp": 123,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    live = read_live_annotation_file(path)
    assert live is not None
    assert live.annots_key == "annots.json"
    assert live.epubcfi == "epubcfi(/6/2/4/1:2)"
    assert live.timestamp == 123


def test_annotation_file_diagnostics_reports_missing_and_candidates(tmp_path):
    missing = annotation_file_diagnostics(tmp_path / "missing.json")
    assert missing["error"] == "annotation_file_missing"

    path = tmp_path / "annots.json"
    path.write_text(
        json.dumps(
            {
                "annotations": [
                    {
                        "type": "last-read",
                        "pos_type": "epubcfi",
                        "pos": "epubcfi(/6/2/4/1:2)",
                        "timestamp": 123,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    diagnostics = annotation_file_diagnostics(path)

    assert diagnostics["json_ok"] is True
    assert diagnostics["candidate_count"] == 1
    assert diagnostics["latest"] == ("epubcfi(/6/2/4/1:2)", 123.0)
