"""Subprocess boundary for music-retrieval-lab package runs."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RETRIEVAL_RUN_SCHEMA_VERSION = 1
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
    return record


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
