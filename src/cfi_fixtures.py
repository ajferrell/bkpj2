"""Capture and verify real Calibre-generated CFI fixtures."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from .calibre_native import (
    LiveAnnotation,
    compute_annots_key,
    find_book_by_annots_key,
    newest_live_annotation,
)


ProbeRunner = Callable[[Optional[str], str], dict[str, Any]]


def default_fixture_dir(data_dir: str | Path = "data") -> Path:
    return Path(data_dir) / "cfi_fixtures"


def run_calibre_cfi_probe(epub_path: Optional[str], epubcfi: str) -> dict[str, Any]:
    """Resolve a CFI by invoking the helper under calibre-debug."""
    if not epub_path:
        return {"error": "Book has no preferred EPUB path."}
    helper = Path(__file__).parent / "calibre" / "cfi_helper.py"
    cmd = [
        "calibre-debug",
        "--exec-file",
        str(helper),
        "--",
        str(epub_path),
        epubcfi,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return {"error": "calibre-debug not found on PATH."}
    except subprocess.TimeoutExpired:
        return {"error": "calibre-debug CFI probe timed out."}

    if result.stdout.strip():
        try:
            data = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            data = {"error": "Could not parse calibre-debug JSON output.", "stdout": result.stdout[:1000]}
    else:
        data = {"error": "calibre-debug produced no JSON output."}

    if result.stderr.strip():
        data["_stderr_tail"] = "\n".join(result.stderr.strip().splitlines()[-8:])
    if result.returncode != 0 and "error" not in data:
        data["error"] = f"calibre-debug exited with code {result.returncode}"
    return data


def current_live_probe(
    data_dir: str | Path = "data",
    annots_dir: str | Path | None = None,
    probe_runner: ProbeRunner = run_calibre_cfi_probe,
) -> tuple[Optional[LiveAnnotation], Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """Return newest live annotation, matching book, and resolved probe result."""
    live = newest_live_annotation(annots_dir)
    if not live:
        return None, None, None
    book = find_book_by_annots_key(live.annots_key, data_dir)
    if not book:
        return live, None, None
    result = probe_runner(book.get("preferred_epub_path"), live.epubcfi)
    return live, book, result


def capture_live_fixture(
    name: str,
    live: LiveAnnotation,
    book: dict[str, Any],
    resolved: dict[str, Any],
    fixture_dir: str | Path,
    confirmed: bool = False,
) -> Path:
    """Write a CFI fixture captured from a live Calibre viewer position."""
    if resolved.get("error"):
        raise ValueError(f"Cannot capture failed CFI probe: {resolved['error']}")

    fixture_root = Path(fixture_dir)
    fixture_root.mkdir(parents=True, exist_ok=True)
    fixture = build_fixture(name, live, book, resolved, confirmed=confirmed)
    path = fixture_root / f"{slugify(name)}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fixture, f, indent=2, ensure_ascii=False)
    return path


def build_fixture(
    name: str,
    live: LiveAnnotation,
    book: dict[str, Any],
    resolved: dict[str, Any],
    confirmed: bool = False,
) -> dict[str, Any]:
    epub_path = book.get("preferred_epub_path")
    preview = resolved.get("text_preview") or ""
    return {
        "schema_version": 1,
        "name": name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "confirmed_by_user": confirmed,
        "book": {
            "calibre_book_id": book.get("calibre_book_id"),
            "calibre_uuid": book.get("calibre_uuid"),
            "title": book.get("title"),
            "authors": book.get("authors") or [],
            "epub_path": epub_path,
            "epub_hash": file_sha256(epub_path) if epub_path else None,
            "annots_key": book.get("annots_key"),
        },
        "live": asdict(live),
        "expected": {
            "spine_index": resolved.get("spine_index"),
            "href": resolved.get("href"),
            "local_char_offset": resolved.get("local_char_offset"),
            "spine_text_len": resolved.get("spine_text_len"),
            "preview_contains": preview_snippet(preview),
        },
        "resolved_at_capture": {
            k: v for k, v in resolved.items()
            if k != "_stderr_tail"
        },
    }


def check_fixtures(
    fixture_dir: str | Path,
    probe_runner: ProbeRunner = run_calibre_cfi_probe,
    strict_hash: bool = False,
    check_anchors: bool = False,
    data_dir: str | Path = "data",
) -> list[dict[str, Any]]:
    """Run all fixture checks and return structured results."""
    results = []
    for path in sorted(Path(fixture_dir).glob("*.json")):
        results.append(check_fixture(
            path,
            probe_runner=probe_runner,
            strict_hash=strict_hash,
            check_anchors=check_anchors,
            data_dir=data_dir,
        ))
    return results


def check_fixture(
    path: str | Path,
    probe_runner: ProbeRunner = run_calibre_cfi_probe,
    strict_hash: bool = False,
    check_anchors: bool = False,
    data_dir: str | Path = "data",
) -> dict[str, Any]:
    fixture_path = Path(path)
    with open(fixture_path, "r", encoding="utf-8") as f:
        fixture = json.load(f)

    book = fixture.get("book", {})
    live = fixture.get("live", {})
    expected = fixture.get("expected", {})
    epub_path = book.get("epub_path")
    cfi = live.get("epubcfi")
    failures: list[str] = []
    warnings: list[str] = []
    anchor = None

    if not epub_path or not Path(epub_path).exists():
        failures.append("epub_missing")
        resolved = None
    else:
        current_key = compute_annots_key(epub_path)
        recorded_key = book.get("annots_key")
        live_key = live.get("annots_key")
        if recorded_key and current_key != recorded_key:
            warnings.append(f"annots_key_drift expected={recorded_key} actual={current_key}")
        if live_key and recorded_key and Path(live_key).name != Path(recorded_key).name:
            warnings.append(f"live_annots_key_mismatch book={recorded_key} live={live_key}")

        expected_hash = book.get("epub_hash")
        actual_hash = file_sha256(epub_path)
        if expected_hash and actual_hash != expected_hash:
            if strict_hash:
                failures.append(f"epub_hash_changed expected={expected_hash} actual={actual_hash}")
            else:
                warnings.append(f"epub_hash_changed expected={expected_hash} actual={actual_hash}")
        resolved = probe_runner(epub_path, cfi)
        if resolved.get("error"):
            failures.append(f"probe_error: {resolved['error']}")
        else:
            compare_field("spine_index", expected, resolved, failures)
            compare_field("href", expected, resolved, failures)
            compare_field("local_char_offset", expected, resolved, failures)
            compare_field("spine_text_len", expected, resolved, failures)
            snippet = expected.get("preview_contains")
            if snippet and snippet not in (resolved.get("text_preview") or ""):
                failures.append("preview_mismatch")
            if check_anchors:
                anchor = resolve_fixture_anchor(book, resolved, data_dir, failures)

    return {
        "path": str(fixture_path),
        "name": fixture.get("name") or fixture_path.stem,
        "ok": not failures,
        "failures": failures,
        "warnings": warnings,
        "resolved": resolved,
        "anchor": anchor,
    }


def resolve_fixture_anchor(
    book: dict[str, Any],
    resolved: dict[str, Any],
    data_dir: str | Path,
    failures: list[str],
) -> Optional[dict[str, Any]]:
    from .anchors import find_anchor_for_position, load_timeline, timeline_path

    book_id = book.get("calibre_book_id")
    path = timeline_path(data_dir, book_id)
    if not path.exists():
        failures.append("timeline_missing")
        return None
    timeline = load_timeline(data_dir, book_id)
    anchor = find_anchor_for_position(
        timeline,
        spine_index=resolved["spine_index"],
        local_char_offset=resolved["local_char_offset"],
    )
    if anchor is None:
        failures.append("anchor_not_found")
    return anchor


def compare_field(
    key: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
    failures: list[str],
) -> None:
    if expected.get(key) != actual.get(key):
        failures.append(f"{key}_mismatch expected={expected.get(key)!r} actual={actual.get(key)!r}")


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def preview_snippet(preview: str, words: int = 8) -> str:
    tokens = re.findall(r"\S+", preview)
    return " ".join(tokens[:words])


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    if not slug:
        slug = datetime.now().strftime("fixture-%Y%m%d-%H%M%S")
    return slug


def timestamp_name(prefix: str = "cfi") -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def sleep_until_next(interval: float) -> None:
    if interval > 0:
        time.sleep(interval)
