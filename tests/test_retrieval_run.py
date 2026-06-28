import json
import sys

from main import build_parser
from src.retrieval_run import materialize_top_ranked_candidates, run_retrieval_audio


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
