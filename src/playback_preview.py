"""Fixed-dwell audio preview for playback plans."""

from __future__ import annotations

import json
import math
import wave
from pathlib import Path
from typing import Any

import numpy as np


def load_playback_plan(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        plan = json.load(handle)
    if not isinstance(plan, dict):
        raise ValueError(f"Invalid playback plan at {path}: expected object")
    return plan


def build_preview_schedule(
    playback_plan_path: str | Path,
    *,
    max_spans: int = 5,
    start_at: int = 1,
) -> list[dict[str, Any]]:
    if max_spans < 1:
        raise ValueError("--max-spans must be positive")
    if start_at < 1:
        raise ValueError("--start-at must be positive")

    plan = load_playback_plan(playback_plan_path)
    entries = plan.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"Invalid playback plan at {playback_plan_path}: entries must be a list")

    playable = [entry for entry in entries if isinstance(entry, dict) and entry.get("playable")]
    selected = playable[start_at - 1 : start_at - 1 + max_spans]
    schedule: list[dict[str, Any]] = []
    for entry in selected:
        master_path = entry.get("master_audio_path")
        if not master_path:
            continue
        resolved = Path(master_path).resolve()
        if not resolved.exists():
            schedule.append({**_schedule_entry(entry, resolved), "status": "missing_master_file"})
            continue
        schedule.append({**_schedule_entry(entry, resolved), "status": "ready"})
    return schedule


def play_preview(
    playback_plan_path: str | Path,
    *,
    max_spans: int = 5,
    start_at: int = 1,
    dwell_seconds: float = 20.0,
    crossfade_seconds: float = 4.0,
    gain: float = 0.8,
    dry_run: bool = False,
) -> dict[str, Any]:
    if dwell_seconds <= 0:
        raise ValueError("--dwell-seconds must be positive")
    if crossfade_seconds < 0:
        raise ValueError("--crossfade-seconds cannot be negative")
    if crossfade_seconds >= dwell_seconds:
        raise ValueError("--crossfade-seconds must be less than --dwell-seconds")
    if gain <= 0:
        raise ValueError("--gain must be positive")

    schedule = build_preview_schedule(
        playback_plan_path,
        max_spans=max_spans,
        start_at=start_at,
    )
    ready = [entry for entry in schedule if entry["status"] == "ready"]
    if not ready:
        return {
            "schedule": schedule,
            "played": False,
            "reason": "no_playable_entries",
        }
    if dry_run:
        return {
            "schedule": schedule,
            "played": False,
            "reason": "dry_run",
        }

    audio, sample_rate = render_preview_audio(
        ready,
        dwell_seconds=dwell_seconds,
        crossfade_seconds=crossfade_seconds,
        gain=gain,
    )
    import sounddevice as sd

    sd.play(audio, samplerate=sample_rate, blocking=True)
    return {
        "schedule": schedule,
        "played": True,
        "sample_rate": sample_rate,
        "duration_seconds": len(audio) / sample_rate,
    }


def render_preview_audio(
    schedule: list[dict[str, Any]],
    *,
    dwell_seconds: float,
    crossfade_seconds: float,
    gain: float,
) -> tuple[np.ndarray, int]:
    rendered: np.ndarray | None = None
    sample_rate: int | None = None
    crossfade_frames = 0

    for item in schedule:
        segment, segment_rate = _load_segment(
            item["master_audio_path"],
            start_seconds=float(item.get("start_seconds") or 0.0),
            duration_seconds=dwell_seconds,
        )
        if sample_rate is None:
            sample_rate = segment_rate
            crossfade_frames = int(crossfade_seconds * sample_rate)
        elif sample_rate != segment_rate:
            raise ValueError(
                f"Sample-rate mismatch: expected {sample_rate}, got {segment_rate} for {item['master_audio_path']}"
            )
        segment = np.clip(segment * gain, -1.0, 1.0)
        rendered = segment if rendered is None else _append_crossfade(rendered, segment, crossfade_frames)

    if rendered is None or sample_rate is None:
        raise ValueError("No preview audio to render")
    return rendered.astype(np.float32, copy=False), sample_rate


def _schedule_entry(entry: dict[str, Any], master_path: Path) -> dict[str, Any]:
    return {
        "sequence": entry.get("sequence"),
        "span_id": entry.get("span_id"),
        "query_record_id": entry.get("query_record_id"),
        "asset_id": entry.get("asset_id"),
        "master_audio_path": str(master_path),
        "start_seconds": entry.get("start_seconds"),
        "start_policy": entry.get("start_policy"),
        "loop_policy": entry.get("loop_policy"),
    }


def _load_segment(
    path: str | Path,
    *,
    start_seconds: float,
    duration_seconds: float,
) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        sample_rate = handle.getframerate()
        frame_count = handle.getnframes()
        if handle.getcomptype() != "NONE":
            raise ValueError(f"Unsupported compressed WAV: {path}")
        if channels < 1:
            raise ValueError(f"Unsupported WAV channel count: {channels}")

        start_frame = min(max(int(start_seconds * sample_rate), 0), max(frame_count - 1, 0))
        wanted_frames = max(int(duration_seconds * sample_rate), 1)
        handle.setpos(start_frame)
        data = handle.readframes(min(wanted_frames, frame_count - start_frame))
        audio = _decode_pcm(data, sample_width, channels)
        if len(audio) < wanted_frames and frame_count > 0:
            audio = _loop_pad_audio(audio, wanted_frames)
        return audio[:wanted_frames], sample_rate


def _decode_pcm(data: bytes, sample_width: int, channels: int) -> np.ndarray:
    if sample_width == 1:
        raw = np.frombuffer(data, dtype=np.uint8).astype(np.float32)
        audio = (raw - 128.0) / 128.0
    elif sample_width == 2:
        raw = np.frombuffer(data, dtype="<i2").astype(np.float32)
        audio = raw / 32768.0
    elif sample_width == 3:
        raw = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3)
        signed = (
            raw[:, 0].astype(np.int32)
            | (raw[:, 1].astype(np.int32) << 8)
            | (raw[:, 2].astype(np.int32) << 16)
        )
        signed = np.where(signed & 0x800000, signed | ~0xFFFFFF, signed)
        audio = signed.astype(np.float32) / 8388608.0
    elif sample_width == 4:
        raw = np.frombuffer(data, dtype="<i4").astype(np.float32)
        audio = raw / 2147483648.0
    else:
        raise ValueError(f"Unsupported WAV sample width: {sample_width}")
    return audio.reshape(-1, channels)


def _loop_pad_audio(audio: np.ndarray, wanted_frames: int) -> np.ndarray:
    if len(audio) == 0:
        return np.zeros((wanted_frames, 1), dtype=np.float32)
    repeats = math.ceil(wanted_frames / len(audio))
    return np.tile(audio, (repeats, 1))[:wanted_frames]


def _append_crossfade(left: np.ndarray, right: np.ndarray, crossfade_frames: int) -> np.ndarray:
    if crossfade_frames <= 0:
        return np.concatenate([left, right], axis=0)
    frames = min(crossfade_frames, len(left), len(right))
    if frames <= 0:
        return np.concatenate([left, right], axis=0)

    theta = np.linspace(0.0, math.pi / 2.0, frames, endpoint=True, dtype=np.float32)
    fade_out = np.cos(theta)[:, None]
    fade_in = np.sin(theta)[:, None]
    mixed = left[-frames:] * fade_out + right[:frames] * fade_in
    return np.concatenate([left[:-frames], mixed, right[frames:]], axis=0)
