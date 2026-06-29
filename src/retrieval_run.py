"""Subprocess boundary for music-retrieval-lab package runs."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RETRIEVAL_RUN_SCHEMA_VERSION = 1
RETRIEVAL_RUN_INDEX_SCHEMA_VERSION = 1
PLAYBACK_PLAN_SCHEMA_VERSION = 1
DEFAULT_CANDIDATE_STRATEGY = "top_ranked"
MASTER_AUDIO_PATH_FIELDS = (
    "master_audio_path",
    "runtime_audio_path",
    "normalized_master_path",
    "normalized_audio_path",
    "playback_master_path",
)
SOUNDS_V1_CORPUS_NAME = "sounds_v1"
CHUNK_INDEX_SUFFIX_RE = re.compile(r"__(\d{4,})$")


def run_retrieval_audio(
    *,
    query_records_path: str | Path,
    retrieval_profile: str,
    output_dir: str | Path,
    profile_config_path: str | Path | None = None,
    lab_project: str | Path | None = None,
    lab_executable: str = "music-lab",
    lab_python: str | Path | None = None,
    mode: str = "package-only",
    limit: int | None = None,
    candidate_strategy: str = DEFAULT_CANDIDATE_STRATEGY,
    run_record_path: str | Path | None = None,
) -> dict[str, Any]:
    if mode not in {"package-only", "review-html"}:
        raise ValueError("--mode must be package-only or review-html")
    if limit is not None and limit < 1:
        raise ValueError("--limit must be positive")
    if candidate_strategy != DEFAULT_CANDIDATE_STRATEGY:
        raise ValueError(f"Unsupported candidate strategy: {candidate_strategy}")

    query_records = _resolve_existing_path(query_records_path, "query records")
    profile_config = (
        _resolve_existing_path(profile_config_path, "profile config")
        if profile_config_path is not None
        else None
    )
    out = Path(output_dir).resolve()
    if out.exists():
        raise FileExistsError(f"Output already exists: {out}")

    command = _build_lab_command(
        query_records_path=query_records,
        retrieval_profile=retrieval_profile,
        profile_config_path=profile_config,
        output_dir=out,
        lab_executable=lab_executable,
        lab_python=lab_python,
        mode=mode,
        limit=limit,
    )
    cwd = Path(lab_project).resolve() if lab_project else None
    if cwd is not None and not cwd.exists():
        raise FileNotFoundError(f"Lab project does not exist: {cwd}")

    created_at = datetime.now(timezone.utc).isoformat()
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        result = subprocess.CompletedProcess(command, 127, "", str(exc))

    out.mkdir(parents=True, exist_ok=True)
    stdout_path = out / "stdout.txt"
    stderr_path = out / "stderr.txt"
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")

    results_path = out / "retrieval_results.jsonl"
    summary_path = out / "retrieval_summary.json"
    review_path = out / "review_report.html"
    top_candidates = (
        materialize_top_ranked_candidates(results_path)
        if result.returncode == 0 and results_path.exists()
        else []
    )
    record = {
        "schema_version": RETRIEVAL_RUN_SCHEMA_VERSION,
        "run_id": out.name,
        "query_records_path": str(query_records),
        "retrieval_profile": retrieval_profile,
        "profile_config_path": str(profile_config) if profile_config else None,
        "lab_project": str(cwd) if cwd else None,
        "lab_command": subprocess.list2cmdline([str(part) for part in command]),
        "exit_status": result.returncode,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "retrieval_package_path": str(results_path),
        "retrieval_summary_path": str(summary_path),
        "review_report_html": str(review_path) if review_path.exists() else None,
        "mode": mode,
        "candidate_strategy": candidate_strategy,
        "top_candidates": top_candidates,
        "created_at": created_at,
    }
    record_path = Path(run_record_path).resolve() if run_record_path else out / "retrieval_run.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    record["retrieval_run_record_path"] = str(record_path)
    write_retrieval_run_index(out.parent)
    return record


def retrieval_runs_dir(data_dir: str | Path, calibre_book_id: int | str) -> Path:
    return Path(data_dir) / "books" / str(calibre_book_id) / "retrieval_runs"


def retrieval_run_index_path(runs_dir: str | Path) -> Path:
    return Path(runs_dir) / "retrieval_run_index.json"


def playback_plan_path(retrieval_run_record_path: str | Path) -> Path:
    return Path(retrieval_run_record_path).resolve().parent / "playback_plan.json"


def build_playback_plan(
    retrieval_run_record_path: str | Path,
    *,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    record_path = _resolve_existing_path(retrieval_run_record_path, "retrieval run record")
    record = _read_json(record_path)
    top_candidates = record.get("top_candidates")
    if not isinstance(top_candidates, list):
        top_candidates = []
    span_lookup = _load_query_span_lookup(record.get("query_records_path"), record_path=record_path)

    entries = [
        _build_playback_plan_entry(
            candidate,
            sequence=index,
            record_path=record_path,
            span_lookup=span_lookup,
        )
        for index, candidate in enumerate(top_candidates, start=1)
    ]
    summary = _playback_plan_summary(entries)
    plan = {
        "schema_version": PLAYBACK_PLAN_SCHEMA_VERSION,
        "retrieval_run_record_path": str(record_path),
        "run_id": record.get("run_id") or record_path.parent.name,
        "retrieval_profile": record.get("retrieval_profile"),
        "candidate_strategy": record.get("candidate_strategy"),
        "source_top_candidate_count": len(top_candidates),
        "summary": summary,
        "entries": entries,
    }
    destination = Path(output_path).resolve() if output_path else playback_plan_path(record_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    plan["playback_plan_path"] = str(destination)
    return plan


def write_retrieval_run_index(
    runs_dir: str | Path,
    *,
    calibre_book_id: int | str | None = None,
) -> dict[str, Any]:
    index = build_retrieval_run_index(runs_dir, calibre_book_id=calibre_book_id)
    path = retrieval_run_index_path(runs_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    index["retrieval_run_index_path"] = str(path.resolve())
    return index


def build_retrieval_run_index(
    runs_dir: str | Path,
    *,
    calibre_book_id: int | str | None = None,
) -> dict[str, Any]:
    root = Path(runs_dir).resolve()
    run_records = sorted(
        path
        for path in root.glob("*/retrieval_run.json")
        if path.parent.name and path.parent != root
    )
    runs = [_summarize_retrieval_run(path, root) for path in run_records]
    return {
        "schema_version": RETRIEVAL_RUN_INDEX_SCHEMA_VERSION,
        "calibre_book_id": str(calibre_book_id) if calibre_book_id is not None else None,
        "retrieval_runs_dir": str(root),
        "run_count": len(runs),
        "runs": runs,
    }


def materialize_top_ranked_candidates(results_path: str | Path) -> list[dict[str, Any]]:
    rows = _read_jsonl(results_path)
    materialized: list[dict[str, Any]] = []
    for row in rows:
        candidates = row.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            materialized.append(
                {
                    "query_record_id": row.get("query_record_id"),
                    "span_id": row.get("span_id"),
                    "status": row.get("status") or "no_candidate",
                }
            )
            continue
        candidate = _first_ranked_candidate(candidates)
        audio_chunk = candidate.get("audio_chunk") if isinstance(candidate.get("audio_chunk"), dict) else {}
        playback_asset = (
            candidate.get("playback_asset")
            if isinstance(candidate.get("playback_asset"), dict)
            else {}
        )
        materialized.append(
            {
                "query_record_id": row.get("query_record_id"),
                "span_id": row.get("span_id"),
                "status": row.get("status"),
                "asset_id": candidate.get("asset_id"),
                "chunk_id": audio_chunk.get("chunk_id"),
                "rank": candidate.get("rank"),
                "score": candidate.get("score"),
                "asset_path": playback_asset.get("path") or audio_chunk.get("path"),
                "audio_chunk_path": audio_chunk.get("path"),
                "start_seconds": playback_asset.get("start_seconds", audio_chunk.get("start_seconds")),
                "duration_seconds": audio_chunk.get("duration_seconds"),
                "start_policy": playback_asset.get("start_policy"),
                "loop_policy": playback_asset.get("loop_policy"),
                **_materialized_master_paths(playback_asset),
            }
        )
    return materialized


def _materialized_master_paths(playback_asset: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in MASTER_AUDIO_PATH_FIELDS:
        if playback_asset.get(field):
            values[field] = playback_asset[field]
    if playback_asset.get("master_path"):
        values["master_audio_path"] = playback_asset["master_path"]
    if playback_asset.get("runtime_path"):
        values["runtime_audio_path"] = playback_asset["runtime_path"]
    return values


def _build_playback_plan_entry(
    candidate: Any,
    *,
    sequence: int,
    record_path: Path,
    span_lookup: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        return {
            "sequence": sequence,
            "query_record_id": None,
            "span_id": None,
            "status": "invalid_candidate_summary",
            "playable": False,
        }

    chunk_path = candidate.get("audio_chunk_path")
    master_path, source_field = _candidate_master_path(candidate)
    entry = {
        "sequence": sequence,
        "query_record_id": candidate.get("query_record_id"),
        "span_id": candidate.get("span_id"),
        "status": "playable",
        "playable": True,
        "asset_id": candidate.get("asset_id"),
        "master_audio_path": master_path,
        "master_audio_path_source": source_field,
        "start_seconds": candidate.get("start_seconds"),
        "start_policy": candidate.get("start_policy"),
        "loop_policy": candidate.get("loop_policy"),
        "chunk_id": candidate.get("chunk_id"),
        "chunk_path": chunk_path,
        "rank": candidate.get("rank"),
        "score": candidate.get("score"),
    }
    span = _candidate_source_span(candidate, span_lookup or {})
    if span:
        entry["span"] = span
    if not candidate.get("asset_id"):
        entry["status"] = candidate.get("status") or "no_top_candidate"
        entry["playable"] = False
    elif not master_path:
        entry["status"] = "missing_master_path"
        entry["playable"] = False
    else:
        resolved_master = _resolve_record_path(master_path, record_path)
        entry["master_audio_path"] = str(resolved_master)
        if not resolved_master.exists():
            entry["status"] = "missing_master_file"
            entry["playable"] = False
    return entry


def _load_query_span_lookup(
    query_records_path: Any,
    *,
    record_path: Path,
) -> dict[str, dict[str, Any]]:
    if not query_records_path:
        return {}
    path = _resolve_record_path(query_records_path, record_path)
    if not path.exists():
        return {}

    lookup: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(path):
        span = row.get("span")
        if not isinstance(span, dict):
            continue
        slim = _slim_source_span(span, row)
        if not slim:
            continue
        record_id = row.get("record_id")
        span_id = span.get("span_id")
        if record_id:
            lookup[str(record_id)] = slim
        if span_id:
            lookup[str(span_id)] = slim
    return lookup


def _candidate_source_span(
    candidate: dict[str, Any],
    span_lookup: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    for key in (candidate.get("query_record_id"), candidate.get("span_id")):
        if key and str(key) in span_lookup:
            return dict(span_lookup[str(key)])
    return None


def _slim_source_span(span: dict[str, Any], record: dict[str, Any] | None = None) -> dict[str, Any]:
    keys = (
        "span_id",
        "spine_index",
        "href",
        "start_local_offset",
        "end_local_offset",
        "text_block_start",
        "text_block_end",
        "selection_method",
        "word_count",
    )
    slim = {key: span.get(key) for key in keys if key in span}
    if not all(key in slim for key in ("span_id", "spine_index", "start_local_offset", "end_local_offset")):
        return {}
    if span.get("excerpt"):
        slim["excerpt_preview"] = _compact_preview(str(span["excerpt"]))
    query = (record or {}).get("query") if isinstance((record or {}).get("query"), dict) else {}
    if query.get("text"):
        slim["query_text"] = str(query["text"])
    return slim


def _compact_preview(text: str, limit: int = 240) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "..."


def _candidate_master_path(candidate: dict[str, Any]) -> tuple[str | None, str | None]:
    chunk_path = candidate.get("audio_chunk_path")
    for field in MASTER_AUDIO_PATH_FIELDS:
        value = candidate.get(field)
        if value:
            return str(value), field
    asset_path = candidate.get("asset_path")
    if asset_path and not _is_chunk_path(asset_path, chunk_path, candidate.get("chunk_id")):
        return str(asset_path), "asset_path"
    derived = _derive_sounds_v1_master_path(chunk_path or asset_path)
    if derived:
        return str(derived), "sounds_v1_chunk_path"
    return None, None


def _derive_sounds_v1_master_path(path_value: Any) -> Path | None:
    if not path_value:
        return None
    path = Path(str(path_value))
    parts = path.parts
    lowered = [part.casefold() for part in parts]
    try:
        run_index = lowered.index(SOUNDS_V1_CORPUS_NAME)
        corpus_index = lowered.index("corpus", run_index + 1)
        chunks_index = lowered.index("chunks", corpus_index + 1)
    except ValueError:
        return None
    if chunks_index <= corpus_index:
        return None

    stem = CHUNK_INDEX_SUFFIX_RE.sub("", path.stem)
    if stem == path.stem:
        return None
    run_root = Path(*parts[: run_index + 1])
    return run_root / "prepared" / "masters" / f"{stem}{path.suffix}"


def _is_chunk_path(
    path_value: Any,
    chunk_path: Any = None,
    chunk_id: Any = None,
) -> bool:
    if not path_value:
        return False
    path_text = str(path_value)
    if chunk_path and Path(path_text) == Path(str(chunk_path)):
        return True
    parts = {part.casefold() for part in Path(path_text).parts}
    if "chunks" in parts:
        return True
    if chunk_id and Path(path_text).stem == str(chunk_id):
        return True
    return False


def _playback_plan_summary(entries: list[dict[str, Any]]) -> dict[str, int]:
    total = len(entries)
    playable = sum(1 for entry in entries if entry.get("playable"))
    missing_master_path = sum(1 for entry in entries if entry.get("status") == "missing_master_path")
    missing_master_file = sum(1 for entry in entries if entry.get("status") == "missing_master_file")
    no_top_candidate = sum(
        1
        for entry in entries
        if entry.get("status") not in {"playable", "missing_master_path", "missing_master_file"}
    )
    return {
        "total": total,
        "playable": playable,
        "missing_master_path": missing_master_path,
        "missing_master_file": missing_master_file,
        "no_top_candidate": no_top_candidate,
    }


def _summarize_retrieval_run(record_path: Path, runs_dir: Path) -> dict[str, Any]:
    record = _read_json(record_path)
    top_candidates = record.get("top_candidates")
    if not isinstance(top_candidates, list):
        top_candidates = []
    span_status = [_span_candidate_status(candidate) for candidate in top_candidates]
    with_candidate = sum(1 for status in span_status if status["has_top_candidate"])
    return {
        "run_id": record.get("run_id") or record_path.parent.name,
        "retrieval_run_record_path": _portable_path(record_path, runs_dir),
        "query_records_path": record.get("query_records_path"),
        "retrieval_profile": record.get("retrieval_profile"),
        "profile_config_path": record.get("profile_config_path"),
        "retrieval_package_path": record.get("retrieval_package_path"),
        "retrieval_summary_path": record.get("retrieval_summary_path"),
        "review_report_html": record.get("review_report_html"),
        "created_at": record.get("created_at"),
        "exit_status": record.get("exit_status"),
        "mode": record.get("mode"),
        "candidate_strategy": record.get("candidate_strategy"),
        "top_candidate_coverage": {
            "with_candidate": with_candidate,
            "total": len(span_status),
            "without_candidate": len(span_status) - with_candidate,
        },
        "span_candidate_status": span_status,
        "missing_files": _missing_retrieval_files(record, record_path),
    }


def _span_candidate_status(candidate: Any) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        return {
            "query_record_id": None,
            "span_id": None,
            "status": "invalid_candidate_summary",
            "has_top_candidate": False,
        }
    return {
        "query_record_id": candidate.get("query_record_id"),
        "span_id": candidate.get("span_id"),
        "status": candidate.get("status") or "unknown",
        "has_top_candidate": bool(candidate.get("asset_id")),
    }


def _missing_retrieval_files(record: dict[str, Any], record_path: Path) -> list[str]:
    fields = [
        "stdout_path",
        "stderr_path",
        "retrieval_package_path",
        "retrieval_summary_path",
        "review_report_html",
    ]
    missing: list[str] = []
    for field in fields:
        value = record.get(field)
        if not value:
            continue
        path = _resolve_record_path(value, record_path)
        if not path.exists():
            missing.append(field)
    return missing


def _resolve_record_path(value: str | Path, record_path: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    if path.exists():
        return path.resolve()
    return (record_path.parent / path).resolve()


def _portable_path(path: str | Path, base: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(base.resolve()))
    except ValueError:
        return str(resolved)


def _build_lab_command(
    *,
    query_records_path: Path,
    retrieval_profile: str,
    profile_config_path: Path | None,
    output_dir: Path,
    lab_executable: str,
    lab_python: str | Path | None,
    mode: str,
    limit: int | None,
) -> list[str]:
    if lab_python is not None:
        command = [str(Path(lab_python).resolve()), "-m", "music_retrieval_lab.cli"]
    else:
        command = [lab_executable]
    command.extend(
        [
            "retrieve-query-records",
            "--query-records",
            str(query_records_path),
            "--retrieval-profile",
            retrieval_profile,
        ]
    )
    if profile_config_path is not None:
        command.extend(["--profile-config", str(profile_config_path)])
    command.extend(["--out", str(output_dir), "--mode", mode])
    if limit is not None:
        command.extend(["--limit", str(limit)])
    return command


def _resolve_existing_path(path: str | Path, label: str) -> Path:
    resolved = Path(path).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"{label} does not exist: {resolved}")
    return resolved


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: expected object")
            rows.append(row)
    return rows


def _read_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Invalid JSON at {path}: expected object")
    return value


def _first_ranked_candidate(candidates: list[Any]) -> dict[str, Any]:
    usable = [candidate for candidate in candidates if isinstance(candidate, dict)]
    if not usable:
        return {}
    return sorted(
        usable,
        key=lambda candidate: (
            int(candidate.get("rank") or 10**9),
            usable.index(candidate),
        ),
    )[0]
