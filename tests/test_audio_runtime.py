import wave
from pathlib import Path

import numpy as np

from src.audio_runtime import (
    AudioMixer,
    RuntimeConfig,
    SoundDeviceBackend,
    load_runtime_assets,
    validate_runtime_asset_metadata,
)


def write_wav(path: Path, data: np.ndarray, sample_rate: int = 44100) -> None:
    pcm = np.clip(data, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(data.shape[1])
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def asset(asset_id: str, path: str = "") -> dict:
    return {
        "asset_id": asset_id,
        "path": path,
        "license": "test fixture",
        "loopable": True,
        "role": "base_bed",
        "categories": ["neutral"],
        "intensity_min": 0.0,
        "intensity_max": 1.0,
        "default_gain": 0.5,
    }


def test_runtime_asset_metadata_rejects_unsupported_requirements():
    bad = asset("bad")
    bad["loopable"] = False

    try:
        validate_runtime_asset_metadata(bad)
    except ValueError as exc:
        assert "loopable" in str(exc)
    else:
        raise AssertionError("runtime metadata should reject non-loopable assets")

    bad = asset("bad-gain")
    bad["default_gain"] = 1.5

    try:
        validate_runtime_asset_metadata(bad)
    except ValueError as exc:
        assert "default_gain" in str(exc)
    else:
        raise AssertionError("runtime metadata should reject out-of-range gain")


def test_runtime_loads_16_bit_stereo_wav_as_float32(tmp_path):
    audio_path = tmp_path / "bed.wav"
    samples = np.array([[0.25, -0.25], [0.5, -0.5], [0.0, 0.0]], dtype=np.float32)
    write_wav(audio_path, samples)
    catalog = {"schema_version": 1, "assets": [asset("bed", str(audio_path))]}

    loaded = load_runtime_assets(catalog)

    runtime_asset = loaded["bed"]
    assert runtime_asset.samples.dtype == np.float32
    assert runtime_asset.samples.shape == (3, 2)
    assert np.allclose(runtime_asset.samples[0], [0.24996948, -0.24996948])


def test_runtime_rejects_missing_and_unsupported_files(tmp_path):
    missing = {"schema_version": 1, "assets": [asset("missing", str(tmp_path / "missing.wav"))]}

    try:
        load_runtime_assets(missing)
    except FileNotFoundError as exc:
        assert "missing.wav" in str(exc)
    else:
        raise AssertionError("runtime should reject missing files before playback")

    unsupported = tmp_path / "bed.mp3"
    unsupported.write_bytes(b"not decoded")
    catalog = {"schema_version": 1, "assets": [asset("mp3", str(unsupported))]}

    try:
        load_runtime_assets(catalog)
    except ValueError as exc:
        assert "Unsupported runtime asset container" in str(exc)
    else:
        raise AssertionError("runtime should reject unsupported containers")


def test_mixer_missing_intent_asset_falls_back_to_silence():
    mixer = AudioMixer({})
    mixer.set_intent({
        "intent_id": "region-1",
        "region_id": 1,
        "intensity": 1.0,
        "fade_in_seconds": 0.0,
        "base_bed": {"asset_id": "missing", "default_gain": 0.5},
        "layers": [],
    })
    out = np.ones((32, 2), dtype=np.float32)

    mixer.fill_output(out)

    assert np.allclose(out, 0.0)
    assert "missing_asset:missing" in mixer.status_messages


def test_mixer_loops_layers_and_applies_crossfade(tmp_path):
    audio_path = tmp_path / "bed.wav"
    write_wav(audio_path, np.full((8, 2), 0.5, dtype=np.float32), sample_rate=8)
    catalog = {"schema_version": 1, "assets": [asset("bed", str(audio_path))]}
    loaded = load_runtime_assets(catalog, config=RuntimeConfig(sample_rate=8, channels=2))
    mixer = AudioMixer(loaded, config=RuntimeConfig(sample_rate=8, channels=2))

    mixer.set_intent({
        "intent_id": "region-1",
        "region_id": 1,
        "intensity": 1.0,
        "fade_in_seconds": 0.0,
        "base_bed": {"asset_id": "bed", "default_gain": 0.5},
        "layers": [],
    })
    first = np.zeros((12, 2), dtype=np.float32)
    mixer.fill_output(first)

    assert np.all(first > 0.24)

    mixer.set_intent({
        "intent_id": "region-2",
        "region_id": 2,
        "intensity": 0.0,
        "fade_in_seconds": 1.0,
        "base_bed": {"asset_id": "bed", "default_gain": 0.5},
        "layers": [],
    })
    faded = np.zeros((4, 2), dtype=np.float32)
    mixer.fill_output(faded)

    assert float(faded[0, 0]) > float(faded[-1, 0])


def test_callback_uses_preloaded_assets_without_file_io(tmp_path, monkeypatch):
    audio_path = tmp_path / "bed.wav"
    write_wav(audio_path, np.full((8, 2), 0.25, dtype=np.float32))
    loaded = load_runtime_assets({"schema_version": 1, "assets": [asset("bed", str(audio_path))]})
    mixer = AudioMixer(loaded)
    mixer.set_intent({
        "intent_id": "region-1",
        "region_id": 1,
        "intensity": 1.0,
        "fade_in_seconds": 0.0,
        "base_bed": {"asset_id": "bed", "default_gain": 0.5},
        "layers": [],
    })

    def fail_open(*args, **kwargs):
        raise AssertionError("callback attempted file I/O")

    monkeypatch.setattr(wave, "open", fail_open)
    out = np.zeros((8, 2), dtype=np.float32)

    mixer.callback(out, 8, None, None)

    assert np.any(out)


def test_sounddevice_backend_wraps_output_stream():
    created = {}

    class FakeStream:
        def __init__(self, **kwargs):
            created.update(kwargs)
            self.started = False
            self.closed = False

        def start(self):
            self.started = True

        def stop(self):
            self.started = False

        def close(self):
            self.closed = True

    class FakeSoundDevice:
        OutputStream = FakeStream

    mixer = AudioMixer({}, config=RuntimeConfig(sample_rate=22050, channels=2))
    backend = SoundDeviceBackend(mixer, sounddevice_module=FakeSoundDevice)

    assert created["samplerate"] == 22050
    assert created["channels"] == 2
    assert created["dtype"] == "float32"
    assert created["callback"] == mixer.callback
    backend.start()
    backend.stop()
    backend.close()
