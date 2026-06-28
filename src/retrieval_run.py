"""Subprocess boundary for music-retrieval-lab package runs."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RETRIEVAL_RUN_SCHEMA_VERSION = 1
RETRIEVAL_RUN_INDEX_SCHEMA_VERSION = 1
DEFAULT_CANDIDATE_STRATEGY = "top_ranked"


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
            }
        )
    return materialized


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
