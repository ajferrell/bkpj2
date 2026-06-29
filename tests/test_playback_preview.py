import json
import wave

import numpy as np

from main import build_parser
from src.playback_preview import (
    LiveFollowState,
    build_preview_schedule,
    find_entry_for_position,
    render_follow_transition,
    render_preview_audio,
    _take_looping_frames,
)


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


def test_find_entry_for_position_maps_live_offset_to_matching_span(tmp_path):
    master = tmp_path / "master.wav"
    _write_wav(master, seconds=1.0)
    plan = {
        "entries": [
            {
                "sequence": 1,
                "span_id": "span-a",
                "playable": True,
                "master_audio_path": str(master),
                "start_seconds": 0.0,
                "span": {
                    "span_id": "span-a",
                    "spine_index": 0,
                    "href": "chapter.xhtml",
                    "start_local_offset": 10,
                    "end_local_offset": 30,
                    "text_block_start": 1,
                    "text_block_end": 2,
                },
            }
        ]
    }

    decision = find_entry_for_position(
        plan,
        {"spine_index": 0, "href": "chapter.xhtml", "local_char_offset": 18},
    )

    assert decision["status"] == "matched"
    assert decision["entry"]["span_id"] == "span-a"


def test_live_follow_state_avoids_restarting_same_entry_and_graces_missing_entry():
    state = LiveFollowState(min_stable_polls=2, missing_grace_polls=1)
    entry = {"sequence": 1, "span_id": "span-a"}
    decision = {"status": "matched", "entry": entry}

    first = state.update(decision)
    second = state.update(decision)
    third = state.update(decision)
    missing = state.update({"status": "no_entry", "entry": None})
    second_missing = state.update({"status": "no_entry", "entry": None})

    assert first["action"] == "pending"
    assert second["action"] == "switch"
    assert third["action"] == "keep"
    assert missing["action"] == "hold"
    assert second_missing["action"] == "stop"


def test_render_follow_transition_crossfades_to_next_entry(tmp_path):
    master_a = tmp_path / "a.wav"
    master_b = tmp_path / "b.wav"
    _write_wav(master_a, seconds=2.0, value=0.25)
    _write_wav(master_b, seconds=2.0, value=0.5)

    audio, sample_rate = render_follow_transition(
        {"master_audio_path": str(master_a), "start_seconds": 0.0},
        {"master_audio_path": str(master_b), "start_seconds": 0.0},
        dwell_seconds=1.0,
        crossfade_seconds=0.25,
        gain=1.0,
    )

    assert sample_rate == 48000
    assert audio.shape == (48000, 2)
    assert np.max(np.abs(audio)) <= 1.0


def test_take_looping_frames_wraps_master_audio_to_start():
    audio = np.array(
        [
            [0.1, 0.1],
            [0.2, 0.2],
            [0.3, 0.3],
        ],
        dtype=np.float32,
    )

    chunk, next_pos = _take_looping_frames(audio, start_pos=2, frames=5)

    assert next_pos == 1
    np.testing.assert_allclose(
        chunk,
        np.array(
            [
                [0.3, 0.3],
                [0.1, 0.1],
                [0.2, 0.2],
                [0.3, 0.3],
                [0.1, 0.1],
            ],
            dtype=np.float32,
        ),
    )


def test_follow_live_audio_cli_manual_dry_run_prints_active_entry(tmp_path, capsys):
    data_dir = tmp_path / "data"
    _write_manifest(data_dir)
    _write_timeline(data_dir)
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
                "span": {
                    "span_id": "span-a",
                    "spine_index": 0,
                    "href": "chapter.xhtml",
                    "start_local_offset": 10,
                    "end_local_offset": 30,
                },
            }
        ],
    )
    parser = build_parser()
    args = parser.parse_args(
        [
            "--data-dir",
            str(data_dir),
            "follow-live-audio",
            "1",
            str(plan),
            "--dry-run",
            "--once",
            "--min-stable-polls",
            "1",
            "--spine-index",
            "0",
            "--local-char-offset",
            "18",
        ]
    )

    assert args.func(args) == 0

    captured = capsys.readouterr()
    assert "Following live audio" in captured.out
    assert "action=switch" in captured.out
    assert "span=span-a" in captured.out


def test_follow_live_audio_cli_verbose_prints_context_and_suppresses_repeated_keep(tmp_path, capsys):
    data_dir = tmp_path / "data"
    _write_manifest(data_dir)
    _write_timeline(data_dir)
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
                "chunk_path": "chunk.wav",
                "start_seconds": 0.0,
                "span": {
                    "span_id": "span-a",
                    "spine_index": 0,
                    "href": "chapter.xhtml",
                    "start_local_offset": 10,
                    "end_local_offset": 30,
                    "query_text": "low tense strings, cold stone, slow dread",
                    "excerpt_preview": "A short preview of the source text.",
                },
            }
        ],
    )
    parser = build_parser()
    args = parser.parse_args(
        [
            "--data-dir",
            str(data_dir),
            "follow-live-audio",
            "1",
            str(plan),
            "--dry-run",
            "--max-polls",
            "3",
            "--poll-interval",
            "0.01",
            "--min-stable-polls",
            "1",
            "--spine-index",
            "0",
            "--local-char-offset",
            "18",
            "--verbose",
        ]
    )

    assert args.func(args) == 0

    captured = capsys.readouterr()
    assert captured.out.count("follow-live-audio: action=") == 2
    assert "action=switch" in captured.out
    assert "action=keep" in captured.out
    assert "query: low tense strings" in captured.out
    assert "text: A short preview" in captured.out
    assert "chunk: chunk.wav" in captured.out


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


def _write_manifest(data_dir):
    data_dir.mkdir(parents=True)
    (data_dir / "calibre_library_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "libraries": [],
                "books": [
                    {
                        "calibre_book_id": 1,
                        "calibre_uuid": "uuid-1",
                        "title": "Book One",
                        "authors": ["Author"],
                        "library_path": "",
                        "calibre_path": "",
                        "formats": {},
                        "preferred_epub_path": None,
                        "annots_key": None,
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _write_timeline(data_dir):
    book_dir = data_dir / "books" / "1"
    book_dir.mkdir(parents=True)
    (book_dir / "timeline.json").write_text(
        json.dumps(
            {
                "schema_version": 5,
                "book": {"calibre_book_id": 1},
                "spine": [
                    {
                        "spine_index": 0,
                        "href": "chapter.xhtml",
                        "text_len": 100,
                    }
                ],
                "anchors": [],
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
