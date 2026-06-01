"""
Calibre-native library discovery and annotation helpers.

This module is intentionally factual: it reads Calibre metadata, computes the
viewer annotation key for EPUB paths, and parses live viewer annotation files.
It does not preprocess text, classify scenes, or touch audio.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional


MANIFEST_VERSION = 1


@dataclass
class CalibreBook:
    calibre_book_id: int
    calibre_uuid: Optional[str]
    title: str
    authors: list[str]
    library_path: str
    calibre_path: str
    formats: dict[str, str]
    preferred_epub_path: Optional[str]
    annots_key: Optional[str]
    prepared: bool = False

    @property
    def display_author(self) -> str:
        return ", ".join(self.authors) if self.authors else "Unknown author"


@dataclass
class LiveAnnotation:
    annots_key: str
    annots_path: str
    epubcfi: str
    timestamp: float


def default_calibre_annots_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return Path("viewer") / "annots"
    return Path(appdata) / "calibre" / "viewer" / "annots"


def compute_annots_key(epub_path: str | Path) -> str:
    """
    Compute Calibre E-book Viewer's annotation filename for a book path.

    Calibre's viewer computes sha256(as_bytes(pathtoebook)).hexdigest() + ".json".
    For normal Windows paths, UTF-8 encoding of the absolute native path matches
    Calibre's as_bytes behavior.
    """
    path_text = str(Path(epub_path).resolve())
    return hashlib.sha256(path_text.encode("utf-8")).hexdigest() + ".json"


def manifest_path(data_dir: str | Path = "data") -> Path:
    return Path(data_dir) / "calibre_library_manifest.json"


def load_manifest(data_dir: str | Path = "data") -> dict[str, Any]:
    path = manifest_path(data_dir)
    if not path.exists():
        return {"schema_version": MANIFEST_VERSION, "libraries": [], "books": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_manifest(manifest: dict[str, Any], data_dir: str | Path = "data") -> Path:
    path = manifest_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["schema_version"] = MANIFEST_VERSION
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    return path


def import_calibre_library(library_path: str | Path, data_dir: str | Path = "data") -> tuple[list[CalibreBook], Path]:
    library = Path(library_path).expanduser().resolve()
    db_path = library / "metadata.db"
    if not db_path.exists():
        raise FileNotFoundError(f"Calibre metadata.db not found: {db_path}")

    books = _read_calibre_books(library)
    manifest = load_manifest(data_dir)
    manifest["libraries"] = _merge_libraries(manifest.get("libraries", []), str(library))

    existing = {
        (b.get("library_path"), b.get("calibre_book_id")): b
        for b in manifest.get("books", [])
    }
    for book in books:
        existing[(book.library_path, book.calibre_book_id)] = asdict(book)

    manifest["books"] = sorted(
        existing.values(),
        key=lambda b: ((b.get("title") or "").lower(), b.get("calibre_book_id") or 0),
    )
    path = save_manifest(manifest, data_dir)
    return books, path


def _merge_libraries(existing: list[Any], library_path: str) -> list[str]:
    values = [str(v) for v in existing if v]
    if library_path not in values:
        values.append(library_path)
    return sorted(values, key=str.lower)


def _read_calibre_books(library: Path) -> list[CalibreBook]:
    query = """
        SELECT
            books.id,
            books.uuid,
            books.title,
            books.path,
            data.format,
            data.name
        FROM books
        JOIN data ON data.book = books.id
        ORDER BY books.title COLLATE NOCASE, books.id
    """

    author_query = """
        SELECT authors.name
        FROM authors
        JOIN books_authors_link ON books_authors_link.author = authors.id
        WHERE books_authors_link.book = ?
        ORDER BY books_authors_link.id
    """

    grouped: dict[int, dict[str, Any]] = {}
    with sqlite3.connect(str(library / "metadata.db")) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute(query):
            book_id = int(row["id"])
            entry = grouped.setdefault(
                book_id,
                {
                    "calibre_book_id": book_id,
                    "calibre_uuid": row["uuid"],
                    "title": row["title"] or f"Book {book_id}",
                    "calibre_path": row["path"] or "",
                    "formats": {},
                },
            )

            fmt = (row["format"] or "").upper()
            name = row["name"] or ""
            if fmt and name:
                entry["formats"][fmt] = str(library / entry["calibre_path"] / f"{name}.{fmt.lower()}")

        for book_id, entry in grouped.items():
            entry["authors"] = [
                r["name"] for r in conn.execute(author_query, (book_id,))
            ]

    books: list[CalibreBook] = []
    for entry in grouped.values():
        preferred_epub = entry["formats"].get("EPUB")
        books.append(
            CalibreBook(
                calibre_book_id=entry["calibre_book_id"],
                calibre_uuid=entry["calibre_uuid"],
                title=entry["title"],
                authors=entry["authors"],
                library_path=str(library),
                calibre_path=entry["calibre_path"],
                formats=entry["formats"],
                preferred_epub_path=preferred_epub,
                annots_key=compute_annots_key(preferred_epub) if preferred_epub else None,
            )
        )
    return books


def iter_manifest_books(data_dir: str | Path = "data") -> Iterable[dict[str, Any]]:
    yield from load_manifest(data_dir).get("books", [])


def find_books(query: str, data_dir: str | Path = "data") -> list[dict[str, Any]]:
    q = query.casefold().strip()
    if not q:
        return []

    matches: list[dict[str, Any]] = []
    for book in iter_manifest_books(data_dir):
        fields = [
            str(book.get("calibre_book_id", "")),
            book.get("calibre_uuid") or "",
            book.get("title") or "",
            " ".join(book.get("authors") or []),
        ]
        if any(q in field.casefold() for field in fields):
            matches.append(book)
    return matches


def find_book(query: str, data_dir: str | Path = "data") -> dict[str, Any]:
    matches = find_books(query, data_dir)
    if not matches:
        raise LookupError(f"No imported Calibre book matched: {query}")
    if len(matches) > 1:
        exact = [b for b in matches if str(b.get("calibre_book_id")) == query]
        if len(exact) == 1:
            return exact[0]
        titles = "\n".join(
            f"  {b.get('calibre_book_id')}: {b.get('title')} - {', '.join(b.get('authors') or [])}"
            for b in matches[:10]
        )
        raise LookupError(f"Multiple books matched '{query}':\n{titles}")
    return matches[0]


def find_book_by_annots_key(annots_key: str, data_dir: str | Path = "data") -> Optional[dict[str, Any]]:
    key = Path(annots_key).name
    for book in iter_manifest_books(data_dir):
        if book.get("annots_key") == key:
            return book
    return None


def read_live_annotation_for_book(
    book: dict[str, Any],
    annots_dir: str | Path | None = None,
) -> Optional[LiveAnnotation]:
    annots_key = book.get("annots_key")
    if not annots_key:
        return None
    annots_root = Path(annots_dir) if annots_dir else default_calibre_annots_dir()
    return read_live_annotation_file(annots_root / annots_key)


def newest_live_annotation(
    annots_dir: str | Path | None = None,
) -> Optional[LiveAnnotation]:
    annots_root = Path(annots_dir) if annots_dir else default_calibre_annots_dir()
    if not annots_root.exists():
        return None
    files = sorted(annots_root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        live = read_live_annotation_file(path)
        if live:
            return live
    return None


def read_live_annotation_file(path: str | Path) -> Optional[LiveAnnotation]:
    annot_path = Path(path)
    if not annot_path.exists():
        return None

    data = _read_json_robustly(annot_path)
    if data is None:
        return None

    candidates = list(_walk_annotation_entries(data))
    candidates.sort(key=lambda x: x[1], reverse=True)
    if not candidates:
        return None

    epubcfi, timestamp = candidates[0]
    return LiveAnnotation(
        annots_key=annot_path.name,
        annots_path=str(annot_path),
        epubcfi=epubcfi,
        timestamp=timestamp,
    )


def _walk_annotation_entries(value: Any) -> Iterable[tuple[str, float]]:
    if isinstance(value, dict):
        if value.get("type") == "last-read" and value.get("pos_type") == "epubcfi" and value.get("pos"):
            yield str(value["pos"]), _timestamp_value(value.get("timestamp"))
        if value.get("cfi") and any(k in value for k in ("progress_frac", "file_progress_frac", "last_read_position")):
            yield str(value["cfi"]), _timestamp_value(value.get("timestamp"))
        for child in value.values():
            yield from _walk_annotation_entries(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_annotation_entries(child)


def _timestamp_value(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _read_json_robustly(path: Path) -> Optional[Any]:
    for attempt in range(3):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            if attempt < 2:
                time.sleep(0.1)
        except OSError:
            return None
    return None
