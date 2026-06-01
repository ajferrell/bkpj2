import json
import sqlite3
import uuid
from pathlib import Path

from src.calibre_native import (
    compute_annots_key,
    import_calibre_library,
    read_live_annotation_file,
)


def scratch_path() -> Path:
    path = Path(".test_tmp") / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def create_fake_library(root: Path) -> Path:
    db = root / "metadata.db"
    book_dir = root / "Author Name" / "Book Title (1)"
    book_dir.mkdir(parents=True)
    epub = book_dir / "Book Title - Author Name.epub"
    epub.write_bytes(b"fake epub")

    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE books (id INTEGER PRIMARY KEY, uuid TEXT, title TEXT, path TEXT);
            CREATE TABLE data (book INTEGER, format TEXT, name TEXT);
            CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INTEGER, author INTEGER);
            """
        )
        conn.execute(
            "INSERT INTO books (id, uuid, title, path) VALUES (1, 'uuid-1', 'Book Title', ?)",
            ("Author Name/Book Title (1)",),
        )
        conn.execute(
            "INSERT INTO data (book, format, name) VALUES (1, 'EPUB', 'Book Title - Author Name')"
        )
        conn.execute("INSERT INTO authors (id, name) VALUES (1, 'Author Name')")
        conn.execute("INSERT INTO books_authors_link (id, book, author) VALUES (1, 1, 1)")

    return epub


def test_import_calibre_library_builds_annots_key():
    tmp_path = scratch_path()
    epub = create_fake_library(tmp_path)
    books, manifest_path = import_calibre_library(tmp_path, data_dir=tmp_path / "data")

    assert manifest_path.exists()
    assert len(books) == 1
    assert books[0].preferred_epub_path == str(epub.resolve())
    assert books[0].annots_key == compute_annots_key(epub)


def test_read_live_annotation_file_handles_nested_annotations():
    tmp_path = scratch_path()
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
