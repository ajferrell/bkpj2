import json
import wave

import numpy as np

from main import build_parser
from src.playback_preview import build_preview_schedule, render_preview_audio


def test_build_preview_schedule_selects_playable_entries(tmp_path):
    master = tmp_path / "master.wav"
    _write_wav(master, seconds=1.0)
    plan = tmp_path / "playback_plan.json"
    _write_plan(
        plan,
        [
            {
                "sequence": 1,
                "span_id": "span-a",
                "query_record_id": "q-a",
                "asset_id": "asset-a",
                "playable": True,
                "master_audio_path": str(master),
                "start_seconds": 0.0,
            },
            {
                "sequence": 2,
                "span_id": "span-b",
                "playable": False,
                "master_audio_path": None,
            },
        ],
    )

    schedule = build_preview_schedule(plan, max_spans=3)

    assert len(schedule) == 1
    assert schedule[0]["status"] == "ready"
    assert schedule[0]["span_id"] == "span-a"
    assert schedule[0]["master_audio_path"] == str(master.resolve())


def test_render_preview_audio_crossfades_segments(tmp_path):
    master_a = tmp_path / "a.wav"
    master_b = tmp_path / "b.wav"
    _write_wav(master_a, seconds=2.0, value=0.25)
    _write_wav(master_b, seconds=2.0, value=0.5)
    schedule = [
        {"master_audio_path": str(master_a), "start_seconds": 0.0},
        {"master_audio_path": str(master_b), "start_seconds": 0.0},
    ]

    audio, sample_rate = render_preview_audio(
        schedule,
        dwell_seconds=1.0,
        crossfade_seconds=0.25,
        gain=1.0,
    )

    assert sample_rate == 48000
    assert audio.shape == (84000, 2)
    assert np.max(np.abs(audio)) <= 1.0


def test_play_preview_cli_dry_run_prints_schedule(tmp_path, capsys):
    master = tmp_path / "master.wav"
    _write_wav(master, seconds=1.0)
    plan = tmp_path / "playback_plan.json"
    _write_plan(
        plan,
        [
            {
                "sequence": 1,
                "span_id": "span-a",
                "query_record_id": "q-a",
                "asset_id": "asset-a",
                "playable": True,
                "master_audio_path": str(master),
                "start_seconds": 0.0,
            }
        ],
    )
    parser = build_parser()
    args = parser.parse_args(["play-preview", str(plan), "--dry-run", "--max-spans", "1"])

    assert args.func(args) == 0

    captured = capsys.readouterr()
    assert "Preview entries: 1" in captured.out
    assert "span=span-a" in captured.out
    assert "Did not play audio: dry_run" in captured.out


def _write_plan(path, entries):
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "summary": {"playable": len([e for e in entries if e.get("playable")])},
                "entries": entries,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _write_wav(path, *, seconds, value=0.25):
    sample_rate = 48000
    frames = int(sample_rate * seconds)
    samples = np.full((frames, 2), value, dtype=np.float32)
    pcm = np.clip(samples * 32767, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())
