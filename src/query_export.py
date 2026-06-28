"""Query JSONL export from prepared book text blocks."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Protocol

from .anchors import (
    extract_source_units,
    extract_spine_texts_with_calibre,
    inspect_text_path,
    load_timeline_with_sidecars,
    source_units_path,
    timeline_drift_warnings,
)


QUERY_RECORD_SCHEMA_VERSION = 1
DEFAULT_RETRIEVAL_PROFILE = "local_audio_text_query"
DEFAULT_EXCERPT_CHAR_LIMIT = 2200
ALLOWED_QUERY_MODES = {"manual", "generated", "needs_query"}
ALLOWED_REVIEW_STATUSES = {"needs_query", "unreviewed", "approved", "rejected"}
FORBIDDEN_QUERY_RECORD_KEYS = {
    "candidate_rankings",
    "candidates",
    "playback",
    "playback_policy",
    "retrieval_candidates",
    "retrieval_results",
    "runtime",
    "selected_asset",
    "selected_assets",
}

SpineExtractor = Callable[[str | Path], list[dict[str, Any]]]


class QueryGenerator(Protocol):
    provider: str
    model_id: str

    def generate(self, record: dict[str, Any], prompt: str) -> str:
        """Generate compact audio-intent query text for one source record."""


class FakeQueryGenerator:
    """Deterministic generator used for tests and local smoke runs."""

    provider = "fake"
    model_id = "fake-audio-intent-v1"

    def generate(self, record: dict[str, Any], prompt: str) -> str:
        excerpt = (record.get("span") or {}).get("excerpt") or ""
        words = [
            word.strip(".,;:!?()[]{}\"'").casefold()
            for word in excerpt.split()
        ]
        useful = [word for word in words if len(word) >= 4]
        chosen = []
        for word in useful:
            if word not in chosen:
                chosen.append(word)
            if len(chosen) == 6:
                break
        if not chosen:
            chosen = ["quiet", "instrumental", "underscore"]
        return "; ".join(["instrumental audio intent", " ".join(chosen), "no vocals"])


class LocalCommandQueryGenerator:
    """Adapter for a local command that reads a prompt on stdin and prints one query."""

    provider = "local-command"

    def __init__(self, command: str, args: list[str] | None = None, model_id: str | None = None) -> None:
        self.command = command
        self.args = list(args or [])
        self.model_id = model_id or Path(command).name

    def generate(self, record: dict[str, Any], prompt: str) -> str:
        result = subprocess.run(
            [self.command, *self.args],
            input=prompt,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise RuntimeError(stderr or f"local command exited with status {result.returncode}")
        query_text = result.stdout.strip()
        if not query_text:
            raise RuntimeError("local command produced empty query text")
        return query_text


def query_records_path(data_dir: str | Path, calibre_book_id: int | str) -> Path:
    return Path(data_dir) / "books" / str(calibre_book_id) / "query_records.jsonl"


def default_generated_query_records_path(data_dir: str | Path, calibre_book_id: int | str) -> Path:
    return Path(data_dir) / "books" / str(calibre_book_id) / "query_records.generated.jsonl"


def load_text_blocks_for_export(
    data_dir: str | Path,
    book: dict[str, Any],
    timeline: dict[str, Any] | None = None,
    spine_extractor: SpineExtractor | None = None,
) -> list[dict[str, Any]]:
    """Load prepared text blocks and attach full text for excerpt export."""
    calibre_book_id = book["calibre_book_id"]
    timeline = timeline or load_timeline_with_sidecars(data_dir, calibre_book_id)
    source_units = list(timeline.get("source_units") or _read_source_units(data_dir, calibre_book_id))
    if not source_units:
        raise ValueError(f"No text blocks found. Run prepare-book first for: {book.get('title')}")

    debug_text = _read_inspect_text(data_dir, calibre_book_id)
    if debug_text:
        return [_attach_text_from_debug(unit, debug_text) for unit in source_units]

    epub_path = timeline.get("book", {}).get("epub_path") or book.get("preferred_epub_path")
    if not epub_path:
        raise ValueError("Cannot rehydrate text blocks: no EPUB path is available")
    extractor = spine_extractor or extract_spine_texts_with_calibre
    extracted_units = extract_source_units(extractor(epub_path))
    return _merge_extracted_text(source_units, extracted_units)


def build_query_span(
    *,
    book: dict[str, Any],
    timeline: dict[str, Any],
    text_blocks: list[dict[str, Any]],
    spine_index: int,
    local_char_offset: int,
    source_cfi: str | None = None,
    resolved_position: dict[str, Any] | None = None,
    selection_method: str = "manual_resolved_position_v1",
    target_words: int = 800,
    min_words: int = 500,
    max_words: int = 1200,
    excerpt_char_limit: int = DEFAULT_EXCERPT_CHAR_LIMIT,
) -> dict[str, Any]:
    if target_words <= 0 or min_words <= 0 or max_words <= 0:
        raise ValueError("Span word limits must be positive")
    if min_words > max_words:
        raise ValueError("--min-words cannot exceed --max-words")

    blocks = sorted(text_blocks, key=lambda unit: unit["unit_id"])
    spine_blocks = [unit for unit in blocks if unit.get("spine_index") == spine_index]
    if not spine_blocks:
        raise ValueError(f"No text blocks found for spine_index={spine_index}")

    focus_pos = _find_focus_index(spine_blocks, local_char_offset)
    start_pos = focus_pos
    end_pos = focus_pos
    word_count = int(spine_blocks[focus_pos].get("word_count") or 0)

    while word_count < target_words:
        before = start_pos - 1 if start_pos > 0 else None
        after = end_pos + 1 if end_pos + 1 < len(spine_blocks) else None
        if before is None and after is None:
            break

        choice = _choose_expansion(
            spine_blocks,
            before=before,
            after=after,
            local_char_offset=local_char_offset,
        )
        candidate_words = int(spine_blocks[choice].get("word_count") or 0)
        if word_count >= min_words and word_count + candidate_words > max_words:
            break

        if choice == before:
            start_pos = choice
        else:
            end_pos = choice
        word_count += candidate_words

    resolved = resolved_position or {
        "spine_index": spine_index,
        "href": spine_blocks[focus_pos].get("href"),
        "local_char_offset": local_char_offset,
        "resolver": "manual",
    }
    return _build_span_from_block_range(
        book=book,
        spine_blocks=spine_blocks,
        start_pos=start_pos,
        end_pos=end_pos,
        source_cfi=source_cfi,
        resolved_position=resolved,
        selection_method=selection_method,
        excerpt_char_limit=excerpt_char_limit,
    )


def build_batch_query_spans(
    *,
    book: dict[str, Any],
    timeline: dict[str, Any],
    text_blocks: list[dict[str, Any]],
    spine_index: int | None = None,
    href: str | None = None,
    target_words: int = 800,
    min_words: int = 500,
    max_words: int = 1200,
    max_spans: int | None = None,
    excerpt_char_limit: int = DEFAULT_EXCERPT_CHAR_LIMIT,
) -> list[dict[str, Any]]:
    """Build deterministic, non-overlapping query span candidates."""
    if target_words <= 0 or min_words <= 0 or max_words <= 0:
        raise ValueError("Span word limits must be positive")
    if min_words > max_words:
        raise ValueError("--min-words cannot exceed --max-words")
    if max_spans is not None and max_spans <= 0:
        raise ValueError("--max-spans must be positive")

    blocks = sorted(text_blocks, key=lambda unit: (unit.get("spine_index"), unit.get("unit_id")))
    if spine_index is not None:
        blocks = [unit for unit in blocks if unit.get("spine_index") == spine_index]
    if href is not None:
        blocks = [unit for unit in blocks if unit.get("href") == href]
    if not blocks:
        raise ValueError("No text blocks matched the batch export filters")

    spans: list[dict[str, Any]] = []
    for spine_blocks in _blocks_by_spine_href(blocks):
        for start_pos, end_pos in _batch_ranges_for_spine(
            spine_blocks,
            target_words=target_words,
            min_words=min_words,
            max_words=max_words,
        ):
            first = spine_blocks[start_pos]
            span = _build_span_from_block_range(
                book=book,
                spine_blocks=spine_blocks,
                start_pos=start_pos,
                end_pos=end_pos,
                source_cfi=None,
                resolved_position={
                    "spine_index": first.get("spine_index"),
                    "href": first.get("href"),
                    "local_char_offset": first.get("start_local_offset"),
                    "resolver": "batch_text_blocks_v1",
                },
                selection_method="batch_text_blocks_v1",
                excerpt_char_limit=excerpt_char_limit,
            )
            spans.append(span)
            if max_spans is not None and len(spans) >= max_spans:
                return spans
    return spans

    return {
        "span_id": span_id,
        "spine_index": spine_index,
        "href": first.get("href"),
        "start_local_offset": first.get("start_local_offset"),
        "end_local_offset": last.get("end_local_offset"),
        "text_block_start": first["unit_id"],
        "text_block_end": last["unit_id"] + 1,
        "source_cfi": source_cfi,
        "resolved_position": {
            key: resolved.get(key)
            for key in ("spine_index", "href", "local_char_offset", "resolver", "spine_text_len", "text_preview")
            if key in resolved
        },
        "selection_method": selection_method,
        "word_count": sum(int(unit.get("word_count") or 0) for unit in selected),
        "excerpt": excerpt,
        "excerpt_hash": "sha256:" + hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
        "boundary_in": _boundary_evidence(spine_blocks, start_pos - 1, "previous_text_block"),
        "boundary_out": _boundary_evidence(spine_blocks, end_pos + 1, "next_text_block"),
    }


def build_query_record(
    *,
    book: dict[str, Any],
    timeline: dict[str, Any],
    span: dict[str, Any],
    query_text: str,
    query_mode: str = "manual",
    generation_method: str = "manual_v1",
    review_status: str = "unreviewed",
    allow_empty_query: bool = False,
) -> dict[str, Any]:
    if query_mode not in ALLOWED_QUERY_MODES:
        raise ValueError(f"Unsupported query mode: {query_mode}")
    if review_status not in ALLOWED_REVIEW_STATUSES:
        raise ValueError(f"Unsupported review status: {review_status}")
    if query_mode == "needs_query" and review_status != "needs_query":
        raise ValueError("needs_query records must use review_status=needs_query")
    if review_status == "needs_query" and query_mode != "needs_query":
        raise ValueError("review_status=needs_query requires query_mode=needs_query")

    query = query_text.strip()
    if not query and not allow_empty_query:
        raise ValueError("Manual query text cannot be empty")

    timeline_book = timeline.get("book", {})
    book_id = timeline_book.get("calibre_book_id") or book.get("calibre_book_id")
    query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:12] if query else "needs-query"
    record_id = f"book-{book_id}-{span['span_id']}-q-{query_hash}"
    return {
        "schema_version": QUERY_RECORD_SCHEMA_VERSION,
        "record_id": record_id,
        "book": {
            "calibre_book_id": book_id,
            "calibre_uuid": timeline_book.get("calibre_uuid") or book.get("calibre_uuid"),
            "title": timeline_book.get("title") or book.get("title"),
            "authors": timeline_book.get("authors") or book.get("authors") or [],
            "epub_hash": timeline_book.get("epub_hash"),
            "annots_key": timeline_book.get("annots_key") or book.get("annots_key"),
        },
        "span": span,
        "query": {
            "mode": query_mode,
            "text": query,
            "generation_method": generation_method,
            "model": None,
            "source": _query_source(query_mode),
        },
        "handoff": {
            "target": "music-retrieval-lab",
            "retrieval_profile": DEFAULT_RETRIEVAL_PROFILE,
            "contract_note": "experimental",
        },
        "review": {
            "status": review_status,
            "notes": "",
        },
    }


def build_generated_query_record(
    *,
    source_record: dict[str, Any],
    query_text: str,
    generation_method: str,
    model: str,
    prompt_version: str,
    provider: str,
) -> dict[str, Any]:
    query = query_text.strip()
    if not query:
        raise ValueError("Generated query text cannot be empty")
    source_errors = validate_query_record(source_record)
    if source_errors:
        raise ValueError(f"Invalid source query record: {', '.join(source_errors)}")
    if source_record.get("query", {}).get("mode") != "needs_query":
        raise ValueError("Generated queries can only be built from needs_query records")

    record = copy.deepcopy(source_record)
    book_id = record.get("book", {}).get("calibre_book_id")
    span = record.get("span") or {}
    query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]
    record["record_id"] = f"book-{book_id}-{span['span_id']}-q-{query_hash}"
    record["query"] = {
        "mode": "generated",
        "text": query,
        "generation_method": generation_method,
        "model": model,
        "source": "span_excerpt",
        "provider": provider,
        "prompt_version": prompt_version,
        "input_excerpt_hash": span.get("excerpt_hash"),
    }
    record["review"] = {
        "status": "unreviewed",
        "notes": "",
    }
    return record


def read_query_records(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at line {line_number}: {exc}") from exc
    return records


def build_generation_prompt(record: dict[str, Any], prompt_version: str) -> str:
    span = record.get("span") or {}
    book = record.get("book") or {}
    return "\n".join(
        [
            f"Prompt version: {prompt_version}",
            "Write one compact music retrieval query for the excerpt.",
            "Focus on mood, instrumentation, energy, tempo, and vocal policy.",
            "Return only the query text.",
            f"Book: {book.get('title') or ''}",
            f"Span id: {span.get('span_id') or ''}",
            f"Excerpt hash: {span.get('excerpt_hash') or ''}",
            "",
            "Excerpt:",
            span.get("excerpt") or "",
        ]
    )


def generate_query_records(
    *,
    input_path: str | Path,
    output_path: str | Path,
    generator: QueryGenerator,
    prompt_version: str,
    generation_method: str = "local_model_audio_intent_v1",
    cache_path: str | Path | None = None,
    errors_path: str | Path | None = None,
    overwrite: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    if limit is not None and limit <= 0:
        raise ValueError("--limit must be positive")

    output = Path(output_path)
    if output.exists() and not overwrite:
        raise ValueError(f"Output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    if overwrite:
        output.write_text("", encoding="utf-8")

    cache_file = Path(cache_path) if cache_path else output.with_suffix(output.suffix + ".cache.json")
    error_file = Path(errors_path) if errors_path else output.with_suffix(output.suffix + ".errors.jsonl")
    cache = _read_generation_cache(cache_file)
    if overwrite and error_file.exists():
        error_file.write_text("", encoding="utf-8")

    records = read_query_records(input_path)
    source_records = [record for record in records if record.get("query", {}).get("mode") == "needs_query"]
    if limit is not None:
        source_records = source_records[:limit]

    generated = 0
    cached = 0
    failed = 0
    for record in source_records:
        key = _generation_cache_key(
            record,
            prompt_version=prompt_version,
            model_id=generator.model_id,
        )
        try:
            query_text = cache.get(key)
            if query_text:
                cached += 1
            else:
                query_text = generator.generate(record, build_generation_prompt(record, prompt_version))
                cache[key] = query_text

            generated_record = build_generated_query_record(
                source_record=record,
                query_text=query_text,
                generation_method=generation_method,
                model=generator.model_id,
                prompt_version=prompt_version,
                provider=generator.provider,
            )
            append_query_record(output, generated_record)
            generated += 1
        except Exception as exc:
            failed += 1
            _append_generation_error(
                error_file,
                record=record,
                prompt_version=prompt_version,
                model_id=generator.model_id,
                error=str(exc),
            )

    _write_generation_cache(cache_file, cache)
    return {
        "input_path": str(input_path),
        "output_path": str(output),
        "cache_path": str(cache_file),
        "errors_path": str(error_file),
        "source_record_count": len(source_records),
        "generated_count": generated,
        "cached_count": cached,
        "failed_count": failed,
    }


def validate_query_record(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if record.get("schema_version") != QUERY_RECORD_SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    for key in ("record_id", "book", "span", "query", "handoff", "review"):
        if key not in record:
            errors.append(f"missing_{key}")
    span = record.get("span") or {}
    for key in (
        "span_id",
        "spine_index",
        "href",
        "start_local_offset",
        "end_local_offset",
        "text_block_start",
        "text_block_end",
        "selection_method",
        "excerpt",
        "excerpt_hash",
        "word_count",
    ):
        if key not in span:
            errors.append(f"missing_span_{key}")
    query = record.get("query") or {}
    review = record.get("review") or {}
    query_mode = query.get("mode")
    review_status = review.get("status")
    if query_mode not in ALLOWED_QUERY_MODES:
        errors.append("query_mode_invalid")
    if review_status not in ALLOWED_REVIEW_STATUSES:
        errors.append("review_status_invalid")
    if query_mode == "needs_query" and review_status != "needs_query":
        errors.append("needs_query_review_status_invalid")
    if review_status == "needs_query" and query_mode != "needs_query":
        errors.append("needs_query_mode_invalid")
    if not (query.get("text") or "").strip() and (query_mode != "needs_query" or review_status != "needs_query"):
        errors.append("query_text_empty")
    if record.get("handoff", {}).get("target") != "music-retrieval-lab":
        errors.append("handoff_target_invalid")
    for path in _forbidden_contract_paths(record):
        errors.append(f"forbidden_query_record_field:{path}")
    return errors


def append_query_record(path: str | Path, record: dict[str, Any]) -> Path:
    errors = validate_query_record(record)
    if errors:
        raise ValueError(f"Invalid query record: {', '.join(errors)}")
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return out


def drift_warnings_for_export(book: dict[str, Any], timeline: dict[str, Any]) -> list[str]:
    return timeline_drift_warnings(book, timeline)


def _read_source_units(data_dir: str | Path, calibre_book_id: int | str) -> list[dict[str, Any]]:
    path = source_units_path(data_dir, calibre_book_id)
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("source_units", [])


def _read_inspect_text(data_dir: str | Path, calibre_book_id: int | str) -> dict[str, Any]:
    path = inspect_text_path(data_dir, calibre_book_id)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("source_units", {})


def _attach_text_from_debug(unit: dict[str, Any], debug_text: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(unit)
    text_entry = debug_text.get(str(unit["unit_id"])) or {}
    if "text" not in text_entry:
        raise ValueError(f"inspect_text.json is missing text for text block {unit['unit_id']}")
    enriched["_text"] = text_entry["text"]
    return enriched


def _merge_extracted_text(
    source_units: list[dict[str, Any]],
    extracted_units: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if len(source_units) != len(extracted_units):
        raise ValueError("Extracted text blocks no longer match the prepared source_units count")
    merged: list[dict[str, Any]] = []
    for stored, extracted in zip(source_units, extracted_units):
        for key in ("unit_id", "spine_index", "href", "start_local_offset", "end_local_offset"):
            if stored.get(key) != extracted.get(key):
                raise ValueError(f"Extracted text block mismatch at unit_id={stored.get('unit_id')}: {key}")
        enriched = dict(stored)
        enriched["_text"] = extracted.get("_text", "")
        merged.append(enriched)
    return merged


def _build_span_from_block_range(
    *,
    book: dict[str, Any],
    spine_blocks: list[dict[str, Any]],
    start_pos: int,
    end_pos: int,
    source_cfi: str | None,
    resolved_position: dict[str, Any],
    selection_method: str,
    excerpt_char_limit: int,
) -> dict[str, Any]:
    selected = spine_blocks[start_pos : end_pos + 1]
    first = selected[0]
    last = selected[-1]
    plain_text = "\n\n".join((unit.get("_text") or unit.get("preview") or "").strip() for unit in selected).strip()
    excerpt = _cap_text(plain_text, excerpt_char_limit)
    span_id = (
        f"book-{book.get('calibre_book_id')}-spine-{first.get('spine_index')}-"
        f"tb-{first['unit_id']}-{last['unit_id'] + 1}"
    )

    return {
        "span_id": span_id,
        "spine_index": first.get("spine_index"),
        "href": first.get("href"),
        "start_local_offset": first.get("start_local_offset"),
        "end_local_offset": last.get("end_local_offset"),
        "text_block_start": first["unit_id"],
        "text_block_end": last["unit_id"] + 1,
        "source_cfi": source_cfi,
        "resolved_position": {
            key: resolved_position.get(key)
            for key in ("spine_index", "href", "local_char_offset", "resolver", "spine_text_len", "text_preview")
            if key in resolved_position
        },
        "selection_method": selection_method,
        "word_count": sum(int(unit.get("word_count") or 0) for unit in selected),
        "excerpt": excerpt,
        "excerpt_hash": "sha256:" + hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
        "boundary_in": _boundary_evidence(spine_blocks, start_pos - 1, "previous_text_block"),
        "boundary_out": _boundary_evidence(spine_blocks, end_pos + 1, "next_text_block"),
    }


def _blocks_by_spine_href(blocks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    for unit in blocks:
        if not groups:
            groups.append([unit])
            continue
        previous = groups[-1][-1]
        if unit.get("spine_index") == previous.get("spine_index") and unit.get("href") == previous.get("href"):
            groups[-1].append(unit)
        else:
            groups.append([unit])
    return groups


def _batch_ranges_for_spine(
    spine_blocks: list[dict[str, Any]],
    *,
    target_words: int,
    min_words: int,
    max_words: int,
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    current_start: int | None = None
    current_words = 0

    for idx, unit in enumerate(spine_blocks):
        unit_words = int(unit.get("word_count") or 0)
        if current_start is not None and current_words >= min_words and current_words + unit_words > max_words:
            ranges.append((current_start, idx - 1))
            current_start = idx
            current_words = unit_words
        else:
            if current_start is None:
                current_start = idx
            current_words += unit_words

        if current_words >= target_words:
            ranges.append((current_start, idx))
            current_start = None
            current_words = 0

    if current_start is not None:
        tail = (current_start, len(spine_blocks) - 1)
        if ranges:
            prev_start, prev_end = ranges[-1]
            prev_words = _range_word_count(spine_blocks, prev_start, prev_end)
            tail_words = _range_word_count(spine_blocks, tail[0], tail[1])
            if tail_words < min_words and prev_words + tail_words <= max_words:
                ranges[-1] = (prev_start, tail[1])
            else:
                ranges.append(tail)
        else:
            ranges.append(tail)
    return ranges


def _range_word_count(spine_blocks: list[dict[str, Any]], start_pos: int, end_pos: int) -> int:
    return sum(int(unit.get("word_count") or 0) for unit in spine_blocks[start_pos : end_pos + 1])


def _find_focus_index(spine_blocks: list[dict[str, Any]], local_char_offset: int) -> int:
    for idx, unit in enumerate(spine_blocks):
        if unit["start_local_offset"] <= local_char_offset < unit["end_local_offset"]:
            return idx
    before = [idx for idx, unit in enumerate(spine_blocks) if unit["end_local_offset"] <= local_char_offset]
    after = [idx for idx, unit in enumerate(spine_blocks) if unit["start_local_offset"] > local_char_offset]
    if before and not after:
        return before[-1]
    if after and not before:
        return after[0]
    if before and after:
        prev_idx = before[-1]
        next_idx = after[0]
        prev_dist = abs(local_char_offset - spine_blocks[prev_idx]["end_local_offset"])
        next_dist = abs(local_char_offset - spine_blocks[next_idx]["start_local_offset"])
        return prev_idx if prev_dist <= next_dist else next_idx
    raise ValueError("Could not locate a focus text block")


def _choose_expansion(
    spine_blocks: list[dict[str, Any]],
    *,
    before: int | None,
    after: int | None,
    local_char_offset: int,
) -> int:
    if before is None:
        return after  # type: ignore[return-value]
    if after is None:
        return before
    before_center = (spine_blocks[before]["start_local_offset"] + spine_blocks[before]["end_local_offset"]) / 2
    after_center = (spine_blocks[after]["start_local_offset"] + spine_blocks[after]["end_local_offset"]) / 2
    before_distance = abs(local_char_offset - before_center)
    after_distance = abs(local_char_offset - after_center)
    return before if before_distance <= after_distance else after


def _cap_text(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "..."


def _query_source(query_mode: str) -> str:
    if query_mode == "manual":
        return "user"
    return "span_excerpt"


def _read_generation_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"Generation cache must be a JSON object: {path}")
    return {str(key): str(value) for key, value in raw.items()}


def _write_generation_cache(path: Path, cache: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)


def _generation_cache_key(record: dict[str, Any], *, prompt_version: str, model_id: str) -> str:
    span = record.get("span") or {}
    basis = {
        "span_id": span.get("span_id"),
        "excerpt_hash": span.get("excerpt_hash"),
        "prompt_version": prompt_version,
        "model_id": model_id,
    }
    return hashlib.sha256(json.dumps(basis, sort_keys=True).encode("utf-8")).hexdigest()


def _append_generation_error(
    path: Path,
    *,
    record: dict[str, Any],
    prompt_version: str,
    model_id: str,
    error: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    error_record = {
        "record_id": record.get("record_id"),
        "span_id": (record.get("span") or {}).get("span_id"),
        "prompt_version": prompt_version,
        "model": model_id,
        "error": error,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(error_record, ensure_ascii=False, sort_keys=True) + "\n")


def _forbidden_contract_paths(value: Any, path: str = "$") -> list[str]:
    if isinstance(value, dict):
        found: list[str] = []
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in FORBIDDEN_QUERY_RECORD_KEYS:
                found.append(child_path)
            found.extend(_forbidden_contract_paths(child, child_path))
        return found
    if isinstance(value, list):
        found = []
        for idx, child in enumerate(value):
            found.extend(_forbidden_contract_paths(child, f"{path}[{idx}]"))
        return found
    return []


def _boundary_evidence(spine_blocks: list[dict[str, Any]], idx: int, kind: str) -> dict[str, Any] | None:
    if idx < 0:
        return {"kind": "spine_start"}
    if idx >= len(spine_blocks):
        return {"kind": "spine_end"}
    unit = spine_blocks[idx]
    return {
        "kind": kind,
        "text_block_id": unit.get("unit_id"),
        "preview": unit.get("preview", ""),
    }
