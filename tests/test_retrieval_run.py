import json
import sys

from main import build_parser
from src.retrieval_run import (
    build_playback_plan,
    materialize_top_ranked_candidates,
    retrieval_runs_dir,
    run_retrieval_audio,
    write_retrieval_run_index,
)


def test_materialize_top_ranked_candidates_extracts_one_candidate_per_row(tmp_path):
    results = tmp_path / "retrieval_results.jsonl"
    results.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "query_record_id": "book-1-span-a-q1",
                        "span_id": "span-a",
                        "status": "ok",
                        "candidates": [
                            {
                                "rank": 2,
                                "asset_id": "asset-later",
                                "score": 0.3,
                                "audio_chunk": {"chunk_id": "later", "path": "later.wav"},
                            },
                            {
                                "rank": 1,
                                "asset_id": "asset-top",
                                "score": 0.7,
                                "audio_chunk": {
                                    "chunk_id": "asset-top__0001",
                                    "path": "chunk.wav",
                                    "start_seconds": 12.5,
                                    "duration_seconds": 8.0,
                                },
                                "playback_asset": {
                                    "path": "asset.wav",
                                    "start_seconds": 12.5,
                                    "start_policy": "matched_chunk",
                                    "loop_policy": "asset_start",
                                },
                            },
                        ],
                    }
                ),
                json.dumps(
                    {
                        "query_record_id": "book-1-span-b-q1",
                        "span_id": "span-b",
                        "status": "empty",
                        "candidates": [],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    top = materialize_top_ranked_candidates(results)

    assert len(top) == 2
    assert top[0]["query_record_id"] == "book-1-span-a-q1"
    assert top[0]["asset_id"] == "asset-top"
    assert top[0]["chunk_id"] == "asset-top__0001"
    assert top[0]["rank"] == 1
    assert top[0]["asset_path"] == "asset.wav"
    assert top[0]["start_policy"] == "matched_chunk"
    assert top[1] == {
        "query_record_id": "book-1-span-b-q1",
        "span_id": "span-b",
        "status": "empty",
    }


def test_run_retrieval_audio_writes_run_record_and_logs(tmp_path):
    lab_project = _write_fake_lab_project(tmp_path)
    query_records = tmp_path / "query_records.generated.jsonl"
    profile = tmp_path / "retrieval_profile.yml"
    query_records.write_text(
        json.dumps(
            {
                "record_id": "book-1-span-a-q1",
                "span": {"span_id": "span-a"},
                "query": {"text": "cold chapel rain"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    profile.write_text("retrieval_profile: fake\n", encoding="utf-8")
    out = tmp_path / "run_001"

    record = run_retrieval_audio(
        query_records_path=query_records,
        retrieval_profile="fake",
        profile_config_path=profile,
        output_dir=out,
        lab_project=lab_project,
        lab_python=sys.executable,
    )

    run_record = json.loads((out / "retrieval_run.json").read_text(encoding="utf-8"))
    assert record["exit_status"] == 0
    assert run_record["exit_status"] == 0
    assert run_record["candidate_strategy"] == "top_ranked"
    assert run_record["top_candidates"][0]["asset_id"] == "asset-a"
    assert (out / "stdout.txt").read_text(encoding="utf-8").strip() == "fake lab ok"
    assert (out / "stderr.txt").read_text(encoding="utf-8") == ""
    assert (out / "retrieval_results.jsonl").exists()
    assert (out / "retrieval_summary.json").exists()
    assert (tmp_path / "retrieval_run_index.json").exists()


def test_run_retrieval_audio_records_missing_lab_executable(tmp_path):
    query_records = tmp_path / "query_records.generated.jsonl"
    query_records.write_text(
        json.dumps(
            {
                "record_id": "book-1-span-a-q1",
                "span": {"span_id": "span-a"},
                "query": {"text": "cold chapel rain"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "run_missing"

    record = run_retrieval_audio(
        query_records_path=query_records,
        retrieval_profile="fake",
        output_dir=out,
        lab_executable="definitely-not-a-real-music-lab-command",
    )

    assert record["exit_status"] == 127
    assert record["top_candidates"] == []
    assert "definitely-not-a-real-music-lab-command" in record["lab_command"]
    assert (out / "stderr.txt").read_text(encoding="utf-8")
    assert (out / "retrieval_run.json").exists()


def test_retrieval_run_index_lists_runs_and_missing_files(tmp_path):
    runs_dir = tmp_path / "retrieval_runs"
    run_dir = runs_dir / "run_001"
    run_dir.mkdir(parents=True)
    (run_dir / "stdout.txt").write_text("ok\n", encoding="utf-8")
    (run_dir / "stderr.txt").write_text("", encoding="utf-8")
    (run_dir / "retrieval_results.jsonl").write_text("", encoding="utf-8")
    _write_run_record(
        run_dir,
        top_candidates=[
            {
                "query_record_id": "book-1-span-a-q1",
                "span_id": "span-a",
                "status": "ok",
                "asset_id": "asset-a",
            },
            {
                "query_record_id": "book-1-span-b-q1",
                "span_id": "span-b",
                "status": "empty",
            },
        ],
    )

    index = write_retrieval_run_index(runs_dir, calibre_book_id=1)

    assert index["run_count"] == 1
    assert index["runs"][0]["top_candidate_coverage"] == {
        "with_candidate": 1,
        "total": 2,
        "without_candidate": 1,
    }
    assert index["runs"][0]["span_candidate_status"][1]["has_top_candidate"] is False
    assert index["runs"][0]["missing_files"] == ["retrieval_summary_path"]
    assert (runs_dir / "retrieval_run_index.json").exists()


def test_retrieve_audio_cli_runs_fake_lab(tmp_path, capsys):
    lab_project = _write_fake_lab_project(tmp_path)
    query_records = tmp_path / "query_records.generated.jsonl"
    profile = tmp_path / "retrieval_profile.yml"
    query_records.write_text(
        json.dumps(
            {
                "record_id": "book-1-span-a-q1",
                "span": {"span_id": "span-a"},
                "query": {"text": "cold chapel rain"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    profile.write_text("retrieval_profile: fake\n", encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(
        [
            "retrieve-audio",
            "--query-records",
            str(query_records),
            "--retrieval-profile",
            "fake",
            "--profile-config",
            str(profile),
            "--out",
            str(tmp_path / "run_cli"),
            "--lab-project",
            str(lab_project),
            "--lab-python",
            sys.executable,
            "--verbose",
        ]
    )

    assert args.func(args) == 0

    captured = capsys.readouterr()
    assert "Retrieval run record:" in captured.out
    assert "Top candidates: 1" in captured.out
    assert "Lab command:" in captured.out
    assert "Lab stdout:" in captured.out
    assert "fake lab ok" in captured.out
    assert "Lab stderr:" in captured.out


def test_list_retrieval_runs_cli_refreshes_index_and_reports_coverage(tmp_path, capsys):
    data_dir = tmp_path / "data"
    _write_manifest(data_dir)
    runs_dir = retrieval_runs_dir(data_dir, 42)
    run_dir = runs_dir / "run_001"
    run_dir.mkdir(parents=True)
    (run_dir / "stdout.txt").write_text("ok\n", encoding="utf-8")
    (run_dir / "stderr.txt").write_text("", encoding="utf-8")
    (run_dir / "retrieval_results.jsonl").write_text("", encoding="utf-8")
    (run_dir / "retrieval_summary.json").write_text("{}\n", encoding="utf-8")
    _write_run_record(
        run_dir,
        retrieval_profile="local_fused_v1",
        top_candidates=[
            {
                "query_record_id": "book-42-span-a-q1",
                "span_id": "span-a",
                "status": "ok",
                "asset_id": "asset-a",
            }
        ],
    )
    parser = build_parser()
    args = parser.parse_args(
        [
            "--data-dir",
            str(data_dir),
            "list-retrieval-runs",
            "42",
            "--verbose",
        ]
    )

    assert args.func(args) == 0

    captured = capsys.readouterr()
    assert "Retrieval runs for: Test Book - Test Author" in captured.out
    assert "run_001 | exit=0 | profile=local_fused_v1" in captured.out
    assert "top=1/1" in captured.out
    assert "span=span-a query=book-42-span-a-q1 status=ok top_candidate=yes" in captured.out
    assert (runs_dir / "retrieval_run_index.json").exists()


def test_build_playback_plan_uses_master_path_and_preserves_chunk_evidence(tmp_path):
    run_dir = tmp_path / "run_001"
    run_dir.mkdir()
    master = tmp_path / "masters" / "asset-a.wav"
    master.parent.mkdir()
    master.write_text("fake wav", encoding="utf-8")
    query_records = run_dir / "query_records.generated.jsonl"
    query_records.write_text(
        json.dumps(
            {
                "record_id": "book-1-span-a-q1",
                "span": {
                    "span_id": "span-a",
                    "spine_index": 0,
                    "href": "chapter.xhtml",
                    "start_local_offset": 10,
                    "end_local_offset": 40,
                    "text_block_start": 1,
                    "text_block_end": 3,
                    "excerpt": "Long source excerpt for the active passage.",
                },
                "query": {"text": "cold chapel rain"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _write_run_record(
        run_dir,
        top_candidates=[
            {
                "query_record_id": "book-1-span-a-q1",
                "span_id": "span-a",
                "status": "ok",
                "asset_id": "asset-a",
                "master_audio_path": str(master),
                "audio_chunk_path": "chunk.wav",
                "chunk_id": "asset-a__0000",
                "rank": 1,
                "score": 0.8,
                "start_seconds": 4.0,
                "start_policy": "matched_chunk",
                "loop_policy": "asset_start",
            }
        ],
    )

    plan = build_playback_plan(run_dir / "retrieval_run.json")

    assert plan["summary"] == {
        "total": 1,
        "playable": 1,
        "missing_master_path": 0,
        "missing_master_file": 0,
        "no_top_candidate": 0,
    }
    entry = plan["entries"][0]
    assert entry["status"] == "playable"
    assert entry["master_audio_path"] == str(master.resolve())
    assert entry["chunk_path"] == "chunk.wav"
    assert entry["start_seconds"] == 4.0
    assert entry["span"]["spine_index"] == 0
    assert entry["span"]["text_block_start"] == 1
    assert entry["span"]["query_text"] == "cold chapel rain"
    assert entry["span"]["excerpt_preview"] == "Long source excerpt for the active passage."
    assert (run_dir / "playback_plan.json").exists()


def test_build_playback_plan_reports_chunk_only_candidate_as_missing_master(tmp_path):
    run_dir = tmp_path / "run_001"
    run_dir.mkdir()
    _write_run_record(
        run_dir,
        top_candidates=[
            {
                "query_record_id": "book-1-span-a-q1",
                "span_id": "span-a",
                "status": "ok",
                "asset_id": "asset-a",
                "asset_path": str(tmp_path / "chunks" / "asset-a__0000.wav"),
                "audio_chunk_path": str(tmp_path / "chunks" / "asset-a__0000.wav"),
                "chunk_id": "asset-a__0000",
                "rank": 1,
            }
        ],
    )

    plan = build_playback_plan(run_dir / "retrieval_run.json")

    assert plan["summary"]["playable"] == 0
    assert plan["summary"]["missing_master_path"] == 1
    assert plan["entries"][0]["status"] == "missing_master_path"
    assert plan["entries"][0]["master_audio_path"] is None
    assert plan["entries"][0]["chunk_path"].endswith("asset-a__0000.wav")


def test_build_playback_plan_derives_sounds_v1_master_from_chunk_path(tmp_path):
    run_dir = tmp_path / "run_001"
    run_dir.mkdir()
    run_root = tmp_path / "audio-finder" / "out" / "runs" / "sounds_v1"
    chunk = run_root / "corpus" / "laion_clap_10s_48k" / "chunks" / "_3kWj69CINE__chapter_0001__0000.wav"
    master = run_root / "prepared" / "masters" / "_3kWj69CINE__chapter_0001.wav"
    chunk.parent.mkdir(parents=True)
    master.parent.mkdir(parents=True)
    chunk.write_text("fake chunk", encoding="utf-8")
    master.write_text("fake master", encoding="utf-8")
    _write_run_record(
        run_dir,
        top_candidates=[
            {
                "query_record_id": "book-1-span-a-q1",
                "span_id": "span-a",
                "status": "ok",
                "asset_id": "_3kWj69CINE__chapter_0001",
                "asset_path": str(chunk),
                "audio_chunk_path": str(chunk),
                "chunk_id": "_3kWj69CINE__chapter_0001__0000",
                "start_seconds": 0.0,
            }
        ],
    )

    plan = build_playback_plan(run_dir / "retrieval_run.json")

    assert plan["summary"]["playable"] == 1
    entry = plan["entries"][0]
    assert entry["status"] == "playable"
    assert entry["master_audio_path"] == str(master.resolve())
    assert entry["master_audio_path_source"] == "sounds_v1_chunk_path"


def test_build_playback_plan_reports_missing_derived_sounds_v1_master_file(tmp_path):
    run_dir = tmp_path / "run_001"
    run_dir.mkdir()
    chunk = (
        tmp_path
        / "audio-finder"
        / "out"
        / "runs"
        / "sounds_v1"
        / "corpus"
        / "m2d_clap_2025_10s_16k"
        / "chunks"
        / "asset-a__0007.wav"
    )
    chunk.parent.mkdir(parents=True)
    chunk.write_text("fake chunk", encoding="utf-8")
    _write_run_record(
        run_dir,
        top_candidates=[
            {
                "query_record_id": "book-1-span-a-q1",
                "span_id": "span-a",
                "status": "ok",
                "asset_id": "asset-a",
                "asset_path": str(chunk),
                "audio_chunk_path": str(chunk),
                "chunk_id": "asset-a__0007",
            }
        ],
    )

    plan = build_playback_plan(run_dir / "retrieval_run.json")

    assert plan["summary"]["playable"] == 0
    assert plan["summary"]["missing_master_file"] == 1
    assert plan["entries"][0]["status"] == "missing_master_file"
    assert plan["entries"][0]["master_audio_path"].endswith(r"sounds_v1\prepared\masters\asset-a.wav")


def test_build_playback_plan_cli_writes_plan_and_reports_summary(tmp_path, capsys):
    run_dir = tmp_path / "run_001"
    run_dir.mkdir()
    _write_run_record(
        run_dir,
        top_candidates=[
            {
                "query_record_id": "book-1-span-a-q1",
                "span_id": "span-a",
                "status": "ok",
                "asset_id": "asset-a",
                "asset_path": "chunk.wav",
                "audio_chunk_path": "chunk.wav",
                "chunk_id": "asset-a__0000",
            }
        ],
    )
    parser = build_parser()
    args = parser.parse_args(["build-playback-plan", str(run_dir / "retrieval_run.json"), "--verbose"])

    assert args.func(args) == 0

    captured = capsys.readouterr()
    assert "Playback plan:" in captured.out
    assert "missing_master_path=1" in captured.out
    assert "status=missing_master_path" in captured.out
    assert (run_dir / "playback_plan.json").exists()


def _write_fake_lab_project(tmp_path):
    lab_project = tmp_path / "fake_lab"
    package = lab_project / "music_retrieval_lab"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "cli.py").write_text(
        """
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("retrieve-query-records")
    p.add_argument("--query-records", required=True)
    p.add_argument("--retrieval-profile", required=True)
    p.add_argument("--profile-config")
    p.add_argument("--out", required=True)
    p.add_argument("--mode", choices=["package-only", "review-html"], default="package-only")
    p.add_argument("--limit", type=int)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True)
    row = {
        "query_record_id": "book-1-span-a-q1",
        "span_id": "span-a",
        "status": "ok",
        "candidates": [
            {
                "rank": 1,
                "asset_id": "asset-a",
                "score": 0.8,
                "audio_chunk": {
                    "chunk_id": "asset-a__0000",
                    "path": "chunk.wav",
                    "start_seconds": 4.0,
                    "duration_seconds": 10.0,
                },
                "playback_asset": {
                    "path": "asset.wav",
                    "start_seconds": 4.0,
                    "start_policy": "matched_chunk",
                },
            }
        ],
    }
    (out / "retrieval_results.jsonl").write_text(json.dumps(row) + "\\n", encoding="utf-8")
    (out / "retrieval_summary.json").write_text(
        json.dumps({"retrieval_profile": args.retrieval_profile}) + "\\n",
        encoding="utf-8",
    )
    if args.mode == "review-html":
        (out / "review_report.html").write_text("<html></html>\\n", encoding="utf-8")
    print("fake lab ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
""".lstrip(),
        encoding="utf-8",
    )
    return lab_project


def _write_run_record(
    run_dir,
    *,
    retrieval_profile="fake",
    top_candidates=None,
):
    record = {
        "schema_version": 1,
        "run_id": run_dir.name,
        "query_records_path": str(run_dir / "query_records.generated.jsonl"),
        "retrieval_profile": retrieval_profile,
        "profile_config_path": None,
        "lab_project": None,
        "lab_command": "music-lab retrieve-query-records ...",
        "exit_status": 0,
        "stdout_path": str(run_dir / "stdout.txt"),
        "stderr_path": str(run_dir / "stderr.txt"),
        "retrieval_package_path": str(run_dir / "retrieval_results.jsonl"),
        "retrieval_summary_path": str(run_dir / "retrieval_summary.json"),
        "review_report_html": None,
        "mode": "package-only",
        "candidate_strategy": "top_ranked",
        "top_candidates": top_candidates or [],
        "created_at": "2026-06-28T00:00:00+00:00",
    }
    (run_dir / "retrieval_run.json").write_text(json.dumps(record) + "\n", encoding="utf-8")


def _write_manifest(data_dir):
    data_dir.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "libraries": [],
        "books": [
            {
                "calibre_book_id": 42,
                "calibre_uuid": "uuid-42",
                "title": "Test Book",
                "authors": ["Test Author"],
                "library_path": str(data_dir),
                "calibre_path": "Test Book",
                "formats": {},
                "preferred_epub_path": None,
                "annots_key": None,
                "prepared": True,
            }
        ],
    }
    (data_dir / "calibre_library_manifest.json").write_text(
        json.dumps(manifest) + "\n",
        encoding="utf-8",
    )
