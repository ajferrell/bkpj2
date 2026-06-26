"""Book anchor preparation and lookup."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .cfi_fixtures import file_sha256


TIMELINE_SCHEMA_VERSION = 5
SOURCE_UNITS_SCHEMA_VERSION = 1


def timeline_dir(data_dir: str | Path, calibre_book_id: int | str) -> Path:
    return Path(data_dir) / "books" / str(calibre_book_id)


def timeline_path(data_dir: str | Path, calibre_book_id: int | str) -> Path:
    return timeline_dir(data_dir, calibre_book_id) / "timeline.json"


def inspect_text_path(data_dir: str | Path, calibre_book_id: int | str) -> Path:
    return timeline_dir(data_dir, calibre_book_id) / "inspect_text.json"


def source_units_path(data_dir: str | Path, calibre_book_id: int | str) -> Path:
    return timeline_dir(data_dir, calibre_book_id) / "source_units.json"


def prepare_book_timeline(
    book: dict[str, Any],
    data_dir: str | Path = "data",
    target_words: int = 350,
    min_words: int = 180,
    debug_text: bool = False,
    spine_extractor= None,
) -> Path:
    epub_path = book.get("preferred_epub_path")
    if not epub_path:
        raise ValueError(f"Book has no EPUB path: {book.get('title')}")

    extractor = spine_extractor or extract_spine_texts_with_calibre
    spine = extractor(epub_path)
    source_units = extract_source_units(spine)
    anchors = build_anchors(source_units, target_words=target_words, min_words=min_words)
    timeline = {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "builder": {
            "name": "anchor_builder",
            "target_words": target_words,
            "min_words": min_words,
        },
        "book": {
            "calibre_book_id": book.get("calibre_book_id"),
            "calibre_uuid": book.get("calibre_uuid"),
            "title": book.get("title"),
            "authors": book.get("authors") or [],
            "epub_path": epub_path,
            "epub_hash": file_sha256(epub_path),
            "annots_key": book.get("annots_key"),
        },
        "spine": [
            {
                "spine_index": item["spine_index"],
                "href": item["href"],
                "text_len": item.get("text_len", len(item.get("text", ""))),
            }
            for item in spine
        ],
        "anchors": anchors,
    }
    out_dir = timeline_dir(data_dir, book["calibre_book_id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "timeline.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(slim_timeline(timeline), f, indent=2, ensure_ascii=False)
    with open(source_units_path(data_dir, book["calibre_book_id"]), "w", encoding="utf-8") as f:
        json.dump(source_units_sidecar(timeline, source_units), f, indent=2, ensure_ascii=False)
    sidecar_path = inspect_text_path(data_dir, book["calibre_book_id"])
    if debug_text:
        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(
                inspect_text_sidecar(source_units, anchors),
                f,
                indent=2,
                ensure_ascii=False,
            )
    elif sidecar_path.exists():
        sidecar_path.unlink()
    return path


def load_timeline(data_dir: str | Path, calibre_book_id: int | str) -> dict[str, Any]:
    path = timeline_path(data_dir, calibre_book_id)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_timeline_with_sidecars(data_dir: str | Path, calibre_book_id: int | str) -> dict[str, Any]:
    timeline = load_timeline(data_dir, calibre_book_id)
    source_path = source_units_path(data_dir, calibre_book_id)
    if "source_units" not in timeline and source_path.exists():
        with open(source_path, "r", encoding="utf-8") as f:
            timeline["source_units"] = json.load(f).get("source_units", [])
    return timeline


def timeline_drift_warnings(book: dict[str, Any], timeline: dict[str, Any]) -> list[str]:
    """Return warnings when the manifest/book identity diverges from a timeline."""
    warnings: list[str] = []
    timeline_book = timeline.get("book", {})
    manifest_epub = book.get("preferred_epub_path")
    timeline_epub = timeline_book.get("epub_path")
    if manifest_epub and timeline_epub and str(manifest_epub) != str(timeline_epub):
        warnings.append(f"epub_path_changed manifest={manifest_epub} timeline={timeline_epub}")
    if manifest_epub and Path(manifest_epub).exists():
        current_hash = file_sha256(manifest_epub)
        timeline_hash = timeline_book.get("epub_hash")
        if timeline_hash and current_hash != timeline_hash:
            warnings.append(f"epub_hash_changed timeline={timeline_hash} actual={current_hash}")
    elif manifest_epub:
        warnings.append(f"epub_missing path={manifest_epub}")
    manifest_key = book.get("annots_key")
    timeline_key = timeline_book.get("annots_key")
    if manifest_key and timeline_key and manifest_key != timeline_key:
        warnings.append(f"annots_key_changed manifest={manifest_key} timeline={timeline_key}")
    return warnings


def inspect_position_chain(
    book: dict[str, Any],
    timeline: dict[str, Any] | None = None,
    live: Any | None = None,
    resolved: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one structured coordinate chain view for CLI inspection."""
    chain: dict[str, Any] = {
        "book": {
            "calibre_book_id": book.get("calibre_book_id"),
            "calibre_uuid": book.get("calibre_uuid"),
            "title": book.get("title"),
            "authors": book.get("authors") or [],
            "epub_path": book.get("preferred_epub_path"),
            "annots_key": book.get("annots_key"),
        },
        "timeline": None,
        "live": None,
        "resolved": None,
        "anchor": None,
        "warnings": [],
    }
    if timeline is not None:
        chain["timeline"] = {
            "schema_version": timeline.get("schema_version"),
            "created_at": timeline.get("created_at"),
            "epub_path": timeline.get("book", {}).get("epub_path"),
            "epub_hash": timeline.get("book", {}).get("epub_hash"),
            "spine_count": len(timeline.get("spine", [])),
            "source_unit_count": len(timeline.get("source_units", [])),
            "anchor_count": len(timeline.get("anchors", [])),
        }
        chain["warnings"].extend(timeline_drift_warnings(book, timeline))
    if live is not None:
        chain["live"] = {
            "annots_path": getattr(live, "annots_path", None),
            "annots_key": getattr(live, "annots_key", None),
            "epubcfi": getattr(live, "epubcfi", None),
            "timestamp": getattr(live, "timestamp", None),
        }
        if book.get("annots_key") and getattr(live, "annots_key", None) != book.get("annots_key"):
            chain["warnings"].append(
                f"live_annots_key_mismatch book={book.get('annots_key')} live={getattr(live, 'annots_key', None)}"
            )
    if resolved is not None:
        chain["resolved"] = {
            key: resolved.get(key)
            for key in ("spine_index", "href", "local_char_offset", "spine_text_len", "text_preview", "error")
            if key in resolved
        }
    if timeline is not None and resolved and not resolved.get("error"):
        anchor = find_anchor_for_position(
            timeline,
            spine_index=resolved["spine_index"],
            local_char_offset=resolved["local_char_offset"],
        )
        if anchor is not None:
            pos = anchor["position"]
            text = anchor["text"]
            chain["anchor"] = {
                "anchor_id": anchor["anchor_id"],
                "spine_index": pos["spine_index"],
                "href": pos["href"],
                "start_local_offset": pos["start_local_offset"],
                "end_local_offset": pos["end_local_offset"],
                "word_count": text.get("word_count"),
                "preview": text.get("preview"),
            }
        else:
            chain["warnings"].append("anchor_not_found")
    return chain


def remove_timeline(data_dir: str | Path, calibre_book_id: int | str) -> bool:
    path = timeline_path(data_dir, calibre_book_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def remove_inspect_text(data_dir: str | Path, calibre_book_id: int | str) -> bool:
    path = inspect_text_path(data_dir, calibre_book_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def extract_spine_texts_with_calibre(epub_path: str | Path) -> list[dict[str, Any]]:
    helper = Path(__file__).parent / "calibre" / "anchor_helper.py"
    cmd = [
        "calibre-debug",
        "--exec-file",
        str(helper),
        "--",
        str(epub_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError as e:
        raise RuntimeError("calibre-debug not found on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("calibre-debug anchor extraction timed out") from e

    if not result.stdout.strip():
        raise RuntimeError(f"calibre-debug produced no anchor JSON. stderr={result.stderr[-1000:]}")
    try:
        data = json.loads(result.stdout.strip())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Could not parse anchor JSON: {result.stdout[:1000]}") from e
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data.get("spine", [])


def build_anchors_from_spine(
    spine: list[dict[str, Any]],
    target_words: int = 350,
    min_words: int = 180,
) -> list[dict[str, Any]]:
    return build_anchors(extract_source_units(spine), target_words=target_words, min_words=min_words)


def extract_source_units(spine: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_units: list[dict[str, Any]] = []
    unit_id = 0
    for item in spine:
        text = item.get("text") or ""
        for para in paragraph_spans(text):
            source_units.append({
                "unit_id": unit_id,
                "spine_index": item["spine_index"],
                "href": item["href"],
                "start_local_offset": para["start"],
                "end_local_offset": para["end"],
                "kind": "paragraph",
                "word_count": para["word_count"],
                "preview": preview_text(para["text"]),
                "_text": para["text"],
            })
            unit_id += 1
    return source_units


def serialize_source_unit(unit: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in unit.items() if not key.startswith("_")}


def slim_timeline(timeline: dict[str, Any]) -> dict[str, Any]:
    slim = dict(timeline)
    slim.pop("source_units", None)
    slim["anchors"] = [
        slim_anchor(anchor)
        for anchor in timeline.get("anchors", [])
    ]
    return slim


def source_units_sidecar(timeline: dict[str, Any], source_units: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": SOURCE_UNITS_SCHEMA_VERSION,
        "timeline": {
            "schema_version": timeline.get("schema_version"),
            "created_at": timeline.get("created_at"),
            "calibre_book_id": timeline.get("book", {}).get("calibre_book_id"),
        },
        "source_units": [serialize_source_unit(unit) for unit in source_units],
    }


def slim_anchor(anchor: dict[str, Any]) -> dict[str, Any]:
    slim = dict(anchor)
    text = dict(anchor.get("text", {}))
    text.pop("plain", None)
    slim["text"] = text
    return slim


def inspect_text_sidecar(
    source_units: list[dict[str, Any]],
    anchors: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "anchors": {
            str(anchor["anchor_id"]): {
                "plain": anchor.get("text", {}).get("plain", ""),
            }
            for anchor in anchors
        },
        "source_units": {
            str(unit["unit_id"]): {
                "text": unit.get("_text", ""),
            }
            for unit in source_units
        },
    }


def build_anchors(
    source_units: list[dict[str, Any]],
    target_words: int = 350,
    min_words: int = 180,
) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    anchor_id = 0
    by_spine: list[list[dict[str, Any]]] = []
    for unit in source_units:
        if not by_spine or by_spine[-1][0]["spine_index"] != unit["spine_index"]:
            by_spine.append([])
        by_spine[-1].append(unit)

    for units in by_spine:
        current: list[dict[str, Any]] = []
        current_words = 0

        for unit in units:
            if current and current_words >= min_words and current_words + unit["word_count"] > target_words:
                anchors.append(make_anchor(anchor_id, current))
                anchor_id += 1
                current = []
                current_words = 0

            current.append(unit)
            current_words += unit["word_count"]

        if current:
            if anchors and anchors[-1]["position"]["spine_index"] == current[0]["spine_index"] and current_words < min_words:
                merge_anchor_tail(anchors[-1], current)
            else:
                anchors.append(make_anchor(anchor_id, current))
                anchor_id += 1
    return anchors


def paragraph_spans(text: str) -> list[dict[str, Any]]:
    spans = []
    for match in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|$)", text):
        raw = match.group(0)
        normalized = re.sub(r"\s+", " ", raw).strip()
        if not normalized:
            continue
        spans.append({
            "start": match.start(),
            "end": match.end(),
            "text": normalized,
            "word_count": len(normalized.split()),
        })
    return spans


def make_anchor(anchor_id: int, source_units: list[dict[str, Any]]) -> dict[str, Any]:
    start = source_units[0]["start_local_offset"]
    end = source_units[-1]["end_local_offset"]
    plain = "\n\n".join(p["_text"] for p in source_units)
    return {
        "anchor_id": anchor_id,
        "source_unit_start": source_units[0]["unit_id"],
        "source_unit_end": source_units[-1]["unit_id"] + 1,
        "position": {
            "spine_index": source_units[0]["spine_index"],
            "href": source_units[0]["href"],
            "start_local_offset": start,
            "end_local_offset": end,
        },
        "text": {
            "plain": plain,
            "preview": preview_text(plain),
            "word_count": sum(p["word_count"] for p in source_units),
            "char_count": len(plain),
            "paragraph_count": len(source_units),
        },
    }


def merge_anchor_tail(anchor: dict[str, Any], source_units: list[dict[str, Any]]) -> None:
    plain = anchor["text"]["plain"] + "\n\n" + "\n\n".join(p["_text"] for p in source_units)
    anchor["source_unit_end"] = source_units[-1]["unit_id"] + 1
    anchor["position"]["end_local_offset"] = source_units[-1]["end_local_offset"]
    anchor["text"]["plain"] = plain
    anchor["text"]["preview"] = preview_text(plain)
    anchor["text"]["word_count"] += sum(p["word_count"] for p in source_units)
    anchor["text"]["char_count"] = len(plain)
    anchor["text"]["paragraph_count"] += len(source_units)


def preview_text(text: str, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "..."


def find_anchor_for_position(
    timeline: dict[str, Any],
    spine_index: int,
    local_char_offset: int,
) -> Optional[dict[str, Any]]:
    candidates = [
        a for a in timeline.get("anchors", [])
        if a.get("position", {}).get("spine_index") == spine_index
    ]
    if not candidates:
        return None
    for anchor in candidates:
        pos = anchor["position"]
        if pos["start_local_offset"] <= local_char_offset < pos["end_local_offset"]:
            return anchor
    before = [a for a in candidates if a["position"]["end_local_offset"] <= local_char_offset]
    after = [a for a in candidates if a["position"]["start_local_offset"] > local_char_offset]
    if before and not after:
        return before[-1]
    if after and not before:
        return after[0]
    if before and after:
        prev_dist = abs(local_char_offset - before[-1]["position"]["end_local_offset"])
        next_dist = abs(local_char_offset - after[0]["position"]["start_local_offset"])
        return before[-1] if prev_dist <= next_dist else after[0]
    return None
