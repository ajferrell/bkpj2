"""Fixed-dwell audio preview for playback plans."""

from __future__ import annotations

import json
import math
import threading
import time
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


def prepare_follow_plan(playback_plan_path: str | Path) -> dict[str, Any]:
    plan = load_playback_plan(playback_plan_path)
    if _plan_has_follow_spans(plan):
        return plan

    record_path = plan.get("retrieval_run_record_path")
    if not record_path:
        return plan
    record = _read_json(Path(record_path))
    span_lookup = _load_query_span_lookup(record.get("query_records_path"))
    if not span_lookup:
        return plan

    enriched = dict(plan)
    enriched_entries = []
    for entry in plan.get("entries") or []:
        if not isinstance(entry, dict):
            enriched_entries.append(entry)
            continue
        if _entry_has_follow_span(entry):
            enriched_entries.append(entry)
            continue
        replacement = dict(entry)
        span = span_lookup.get(str(entry.get("query_record_id"))) or span_lookup.get(str(entry.get("span_id")))
        if span:
            replacement["span"] = span
        enriched_entries.append(replacement)
    enriched["entries"] = enriched_entries
    return enriched


def find_entry_for_position(
    playback_plan: dict[str, Any],
    resolved_position: dict[str, Any] | None,
) -> dict[str, Any]:
    if not resolved_position or resolved_position.get("error"):
        return {
            "status": "unresolved",
            "error": resolved_position.get("error") if resolved_position else "missing_position",
            "entry": None,
        }
    try:
        spine_index = int(resolved_position["spine_index"])
        offset = int(resolved_position["local_char_offset"])
    except (KeyError, TypeError, ValueError):
        return {"status": "unresolved", "error": "missing_resolved_coordinate", "entry": None}

    entries = [
        entry
        for entry in playback_plan.get("entries") or []
        if isinstance(entry, dict) and entry.get("playable") and _entry_has_follow_span(entry)
    ]
    same_spine = [entry for entry in entries if int((entry.get("span") or {}).get("spine_index")) == spine_index]
    href = resolved_position.get("href")
    if href:
        href_matches = [entry for entry in same_spine if (entry.get("span") or {}).get("href") == href]
        if href_matches:
            same_spine = href_matches
    if not same_spine:
        return {"status": "no_entry", "entry": None, "spine_index": spine_index, "local_char_offset": offset}

    for entry in sorted(same_spine, key=lambda item: int(item.get("sequence") or 0)):
        span = entry["span"]
        if int(span["start_local_offset"]) <= offset < int(span["end_local_offset"]):
            return _follow_decision("matched", entry, offset)

    nearest = min(same_spine, key=lambda item: _span_distance(item["span"], offset))
    return _follow_decision("nearest", nearest, offset)


class LiveFollowState:
    def __init__(self, *, min_stable_polls: int = 2, missing_grace_polls: int = 3) -> None:
        if min_stable_polls < 1:
            raise ValueError("--min-stable-polls must be positive")
        if missing_grace_polls < 0:
            raise ValueError("--missing-grace-polls cannot be negative")
        self.min_stable_polls = min_stable_polls
        self.missing_grace_polls = missing_grace_polls
        self.pending_key: str | None = None
        self.pending_count = 0
        self.active_key: str | None = None
        self.active_entry: dict[str, Any] | None = None
        self.missing_count = 0

    def update(self, decision: dict[str, Any]) -> dict[str, Any]:
        entry = decision.get("entry") if isinstance(decision, dict) else None
        if not isinstance(entry, dict):
            self.pending_key = None
            self.pending_count = 0
            if self.active_key is not None:
                self.missing_count += 1
                if self.missing_count > self.missing_grace_polls:
                    previous = self.active_entry
                    self.active_key = None
                    self.active_entry = None
                    return {"action": "stop", "entry": None, "previous_entry": previous, "decision": decision}
                return {"action": "hold", "entry": self.active_entry, "decision": decision}
            return {"action": "none", "entry": None, "decision": decision}

        self.missing_count = 0
        key = _entry_key(entry)
        if key == self.active_key:
            self.pending_key = None
            self.pending_count = 0
            return {"action": "keep", "entry": entry, "decision": decision}
        if key == self.pending_key:
            self.pending_count += 1
        else:
            self.pending_key = key
            self.pending_count = 1

        if self.pending_count < self.min_stable_polls:
            return {"action": "pending", "entry": entry, "decision": decision, "stable_polls": self.pending_count}

        previous = self.active_entry
        self.active_key = key
        self.active_entry = entry
        self.pending_key = None
        self.pending_count = 0
        return {"action": "switch", "entry": entry, "previous_entry": previous, "decision": decision}


def play_follow_entry(
    entry: dict[str, Any],
    *,
    previous_entry: dict[str, Any] | None = None,
    dwell_seconds: float = 20.0,
    crossfade_seconds: float = 4.0,
    gain: float = 0.8,
) -> dict[str, Any]:
    if dwell_seconds <= 0:
        raise ValueError("--dwell-seconds must be positive")
    if crossfade_seconds < 0:
        raise ValueError("--crossfade-seconds cannot be negative")
    if gain <= 0:
        raise ValueError("--gain must be positive")

    audio, sample_rate = render_follow_transition(
        previous_entry,
        entry,
        dwell_seconds=dwell_seconds,
        crossfade_seconds=crossfade_seconds,
        gain=gain,
    )
    import sounddevice as sd

    sd.play(audio, samplerate=sample_rate, blocking=False)
    return {"sample_rate": sample_rate, "duration_seconds": len(audio) / sample_rate}


class LiveAudioPlayer:
    def __init__(self, *, gain: float = 0.8, crossfade_seconds: float = 4.0) -> None:
        if gain <= 0:
            raise ValueError("--gain must be positive")
        if crossfade_seconds < 0:
            raise ValueError("--crossfade-seconds cannot be negative")
        self.gain = gain
        self.crossfade_seconds = crossfade_seconds
        self._lock = threading.Lock()
        self._stream = None
        self._sample_rate: int | None = None
        self._channels: int | None = None
        self._current_audio: np.ndarray | None = None
        self._current_pos = 0
        self._target_audio: np.ndarray | None = None
        self._target_pos = 0
        self._fade_pos = 0
        self._fade_total = 0

    def switch_to(self, entry: dict[str, Any]) -> dict[str, Any]:
        audio, sample_rate = _load_wav_file(entry["master_audio_path"])
        audio = np.clip(audio * self.gain, -1.0, 1.0).astype(np.float32, copy=False)
        if audio.ndim != 2:
            raise ValueError(f"Invalid audio shape for {entry['master_audio_path']}: {audio.shape}")
        channels = int(audio.shape[1])
        start_pos = _start_frame_for_audio(audio, sample_rate, float(entry.get("start_seconds") or 0.0))
        self._ensure_stream(sample_rate, channels)

        with self._lock:
            if self._current_audio is None or self.crossfade_seconds <= 0:
                self._current_audio = audio
                self._current_pos = start_pos
                self._target_audio = None
                self._target_pos = 0
                self._fade_pos = 0
                self._fade_total = 0
            else:
                self._target_audio = audio
                self._target_pos = start_pos
                self._fade_pos = 0
                self._fade_total = int(self.crossfade_seconds * sample_rate)
        return {"sample_rate": sample_rate, "channels": channels, "streaming": True}

    def stop(self) -> None:
        with self._lock:
            self._current_audio = None
            self._target_audio = None
            self._current_pos = 0
            self._target_pos = 0
            self._fade_pos = 0
            self._fade_total = 0
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            self._sample_rate = None
            self._channels = None

    def _ensure_stream(self, sample_rate: int, channels: int) -> None:
        if self._stream is not None:
            if self._sample_rate != sample_rate or self._channels != channels:
                raise ValueError(
                    f"Sample-rate/channel mismatch: active stream is {self._sample_rate}Hz/{self._channels}ch, "
                    f"next entry is {sample_rate}Hz/{channels}ch"
                )
            return

        import sounddevice as sd

        self._sample_rate = sample_rate
        self._channels = channels
        self._stream = sd.OutputStream(
            samplerate=sample_rate,
            channels=channels,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, outdata, frames, _time_info, _status) -> None:
        with self._lock:
            rendered = self._render_locked(frames)
        outdata[:] = rendered

    def _render_locked(self, frames: int) -> np.ndarray:
        channels = self._channels or 1
        if self._current_audio is None:
            return np.zeros((frames, channels), dtype=np.float32)

        if self._target_audio is None or self._fade_total <= 0:
            chunk, self._current_pos = _take_looping_frames(self._current_audio, self._current_pos, frames)
            return chunk

        current, self._current_pos = _take_looping_frames(self._current_audio, self._current_pos, frames)
        target, self._target_pos = _take_looping_frames(self._target_audio, self._target_pos, frames)
        fade_indexes = np.arange(self._fade_pos, self._fade_pos + frames, dtype=np.float32)
        fade = np.clip(fade_indexes / max(self._fade_total, 1), 0.0, 1.0)
        theta = fade * (math.pi / 2.0)
        mixed = current * np.cos(theta)[:, None] + target * np.sin(theta)[:, None]
        self._fade_pos += frames
        if self._fade_pos >= self._fade_total:
            self._current_audio = self._target_audio
            self._current_pos = self._target_pos
            self._target_audio = None
            self._target_pos = 0
            self._fade_pos = 0
            self._fade_total = 0
        return mixed.astype(np.float32, copy=False)


def render_follow_transition(
    previous_entry: dict[str, Any] | None,
    next_entry: dict[str, Any],
    *,
    dwell_seconds: float,
    crossfade_seconds: float,
    gain: float,
) -> tuple[np.ndarray, int]:
    next_audio, sample_rate = _load_segment(
        next_entry["master_audio_path"],
        start_seconds=float(next_entry.get("start_seconds") or 0.0),
        duration_seconds=dwell_seconds,
    )
    next_audio = np.clip(next_audio * gain, -1.0, 1.0)
    if not previous_entry or crossfade_seconds <= 0:
        return next_audio.astype(np.float32, copy=False), sample_rate

    previous_audio, previous_rate = _load_segment(
        previous_entry["master_audio_path"],
        start_seconds=float(previous_entry.get("start_seconds") or 0.0),
        duration_seconds=crossfade_seconds,
    )
    if previous_rate != sample_rate:
        raise ValueError(
            f"Sample-rate mismatch: expected {sample_rate}, got {previous_rate} for {previous_entry['master_audio_path']}"
        )
    previous_audio = np.clip(previous_audio * gain, -1.0, 1.0)
    crossfade_frames = int(crossfade_seconds * sample_rate)
    return _append_crossfade(previous_audio, next_audio, crossfade_frames).astype(np.float32, copy=False), sample_rate


def follow_live_audio(
    *,
    playback_plan_path: str | Path,
    position_reader,
    poll_interval: float = 1.0,
    min_stable_polls: int = 2,
    missing_grace_polls: int = 3,
    dwell_seconds: float = 20.0,
    crossfade_seconds: float = 4.0,
    gain: float = 0.8,
    dry_run: bool = False,
    max_polls: int | None = None,
    on_event=None,
) -> list[dict[str, Any]]:
    if poll_interval <= 0:
        raise ValueError("--poll-interval must be positive")
    if dwell_seconds <= 0:
        raise ValueError("--dwell-seconds must be positive")
    plan = prepare_follow_plan(playback_plan_path)
    state = LiveFollowState(min_stable_polls=min_stable_polls, missing_grace_polls=missing_grace_polls)
    player = None if dry_run else LiveAudioPlayer(gain=gain, crossfade_seconds=crossfade_seconds)
    events: list[dict[str, Any]] = []
    poll_count = 0

    try:
        while True:
            poll_count += 1
            resolved = position_reader()
            decision = find_entry_for_position(plan, resolved)
            event = state.update(decision)
            if event["action"] == "switch" and player is not None:
                event["playback"] = player.switch_to(event["entry"])
            elif event["action"] == "stop" and player is not None:
                player.stop()
            events.append(event)
            if on_event is not None:
                on_event(event)
            if max_polls is not None and poll_count >= max_polls:
                return events
            time.sleep(poll_interval)
    finally:
        if player is not None:
            player.stop()


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


def _plan_has_follow_spans(plan: dict[str, Any]) -> bool:
    return any(_entry_has_follow_span(entry) for entry in plan.get("entries") or [] if isinstance(entry, dict))


def _entry_has_follow_span(entry: dict[str, Any]) -> bool:
    span = entry.get("span")
    if not isinstance(span, dict):
        return False
    return all(key in span for key in ("spine_index", "start_local_offset", "end_local_offset"))


def _follow_decision(status: str, entry: dict[str, Any], offset: int) -> dict[str, Any]:
    span = entry["span"]
    return {
        "status": status,
        "entry": entry,
        "sequence": entry.get("sequence"),
        "span_id": entry.get("span_id"),
        "spine_index": span.get("spine_index"),
        "local_char_offset": offset,
        "span_start": span.get("start_local_offset"),
        "span_end": span.get("end_local_offset"),
    }


def _span_distance(span: dict[str, Any], offset: int) -> int:
    start = int(span["start_local_offset"])
    end = int(span["end_local_offset"])
    if start <= offset < end:
        return 0
    if offset < start:
        return start - offset
    return offset - end


def _entry_key(entry: dict[str, Any]) -> str:
    return str(entry.get("span_id") or entry.get("query_record_id") or entry.get("sequence"))


def _load_query_span_lookup(query_records_path: Any) -> dict[str, dict[str, Any]]:
    if not query_records_path:
        return {}
    path = Path(str(query_records_path))
    if not path.exists():
        return {}
    lookup: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            row = json.loads(stripped)
            if not isinstance(row, dict):
                continue
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
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "..."


def _read_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Invalid JSON at {path}: expected object")
    return value


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


def _load_wav_file(path: str | Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        sample_rate = handle.getframerate()
        frame_count = handle.getnframes()
        if handle.getcomptype() != "NONE":
            raise ValueError(f"Unsupported compressed WAV: {path}")
        if channels < 1:
            raise ValueError(f"Unsupported WAV channel count: {channels}")
        data = handle.readframes(frame_count)
    return _decode_pcm(data, sample_width, channels), sample_rate


def _start_frame_for_audio(audio: np.ndarray, sample_rate: int, start_seconds: float) -> int:
    if len(audio) == 0:
        return 0
    return min(max(int(start_seconds * sample_rate), 0), max(len(audio) - 1, 0))


def _take_looping_frames(audio: np.ndarray, start_pos: int, frames: int) -> tuple[np.ndarray, int]:
    if len(audio) == 0:
        channels = audio.shape[1] if audio.ndim == 2 else 1
        return np.zeros((frames, channels), dtype=np.float32), 0
    chunks: list[np.ndarray] = []
    remaining = frames
    pos = start_pos % len(audio)
    while remaining > 0:
        available = len(audio) - pos
        take = min(available, remaining)
        chunks.append(audio[pos : pos + take])
        remaining -= take
        pos = 0 if pos + take >= len(audio) else pos + take
    return np.concatenate(chunks, axis=0).astype(np.float32, copy=False), pos


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
