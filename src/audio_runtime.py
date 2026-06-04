"""Local audio runtime for prepared audio scene intents."""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

RUNTIME_METHOD_VERSION = "audio_runtime.v1"
DEFAULT_SAMPLE_RATE = 44100
DEFAULT_CHANNELS = 2
DEFAULT_FADE_SECONDS = 6.0
SUPPORTED_EXTENSIONS = {".wav"}


@dataclass(frozen=True)
class RuntimeConfig:
    sample_rate: int = DEFAULT_SAMPLE_RATE
    channels: int = DEFAULT_CHANNELS
    fallback_gain: float = 0.0


@dataclass(frozen=True)
class RuntimeAsset:
    asset_id: str
    path: str
    role: str
    categories: tuple[str, ...]
    default_gain: float
    samples: np.ndarray


class AudioBackend(Protocol):
    def start(self) -> None:
        ...

    def stop(self) -> None:
        ...

    def close(self) -> None:
        ...


def validate_runtime_asset_metadata(asset: dict[str, Any]) -> None:
    for key in (
        "asset_id",
        "path",
        "license",
        "loopable",
        "role",
        "categories",
        "intensity_min",
        "intensity_max",
        "default_gain",
    ):
        if key not in asset:
            raise ValueError(f"Runtime asset missing {key}: {asset}")
    if asset["role"] not in {"base_bed", "layer"}:
        raise ValueError(f"Unsupported runtime asset role: {asset['role']}")
    if not asset["loopable"]:
        raise ValueError(f"Runtime asset must be loopable: {asset['asset_id']}")
    if not isinstance(asset["categories"], list) or not asset["categories"]:
        raise ValueError(f"Runtime asset categories must be a non-empty list: {asset['asset_id']}")
    if float(asset["intensity_min"]) > float(asset["intensity_max"]):
        raise ValueError(f"Runtime asset intensity range is invalid: {asset['asset_id']}")
    gain = float(asset["default_gain"])
    if gain < 0.0 or gain > 1.0:
        raise ValueError(f"Runtime asset default_gain must be normalized 0..1: {asset['asset_id']}")


def resolve_asset_path(path_value: str, catalog_path: str | Path | None = None) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    if path.is_absolute():
        return path
    if catalog_path:
        return Path(catalog_path).parent / path
    return path


def load_runtime_assets(
    catalog: dict[str, Any],
    catalog_path: str | Path | None = None,
    config: RuntimeConfig | None = None,
) -> dict[str, RuntimeAsset]:
    cfg = config or RuntimeConfig()
    if catalog.get("schema_version") != 1:
        raise ValueError("Unsupported runtime audio asset catalog schema_version")
    loaded: dict[str, RuntimeAsset] = {}
    for asset in catalog.get("assets", []):
        validate_runtime_asset_metadata(asset)
        path = resolve_asset_path(str(asset["path"]), catalog_path)
        if path is None:
            samples = np.zeros((max(1, cfg.sample_rate), cfg.channels), dtype=np.float32)
        else:
            samples = load_wav_asset(path, cfg)
        loaded[asset["asset_id"]] = RuntimeAsset(
            asset_id=asset["asset_id"],
            path=str(asset["path"]),
            role=asset["role"],
            categories=tuple(asset["categories"]),
            default_gain=float(asset["default_gain"]),
            samples=samples,
        )
    return loaded


def load_wav_asset(path: Path, config: RuntimeConfig) -> np.ndarray:
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported runtime asset container: {path}")
    if not path.exists():
        raise FileNotFoundError(f"Runtime asset file not found: {path}")
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        sample_width = wav.getsampwidth()
        compression = wav.getcomptype()
        frame_count = wav.getnframes()
        if compression != "NONE":
            raise ValueError(f"Compressed WAV assets are not supported: {path}")
        if channels != config.channels:
            raise ValueError(f"Runtime asset must have {config.channels} channels: {path}")
        if sample_rate != config.sample_rate:
            raise ValueError(f"Runtime asset must use {config.sample_rate} Hz: {path}")
        if sample_width != 2:
            raise ValueError(f"Runtime asset must be 16-bit PCM WAV: {path}")
        raw = wav.readframes(frame_count)
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if data.size == 0:
        raise ValueError(f"Runtime asset contains no samples: {path}")
    return data.reshape((-1, config.channels))


@dataclass
class MixerLayer:
    asset: RuntimeAsset
    gain: float
    position: int = 0


class AudioMixer:
    def __init__(self, assets: dict[str, RuntimeAsset], config: RuntimeConfig | None = None) -> None:
        self.assets = assets
        self.config = config or RuntimeConfig()
        self.current_layers: list[MixerLayer] = []
        self.target_layers: list[MixerLayer] = []
        self.fade_total_frames = 0
        self.fade_remaining_frames = 0
        self.status_messages: list[str] = []

    def set_intent(self, intent: dict[str, Any] | None) -> None:
        layers = self._layers_for_intent(intent)
        fade_seconds = DEFAULT_FADE_SECONDS
        if intent:
            fade_seconds = max(float(intent.get("fade_in_seconds", DEFAULT_FADE_SECONDS)), 0.0)
        self.target_layers = layers
        self.fade_total_frames = int(fade_seconds * self.config.sample_rate)
        self.fade_remaining_frames = self.fade_total_frames
        if self.fade_total_frames == 0 or not self.current_layers:
            self.current_layers = self.target_layers
            self.target_layers = []
            self.fade_remaining_frames = 0

    def fill_output(self, outdata: np.ndarray) -> None:
        frames = int(outdata.shape[0])
        outdata.fill(0.0)
        if frames <= 0:
            return
        if self.fade_remaining_frames > 0:
            step = min(frames, self.fade_remaining_frames)
            old_start = self.fade_remaining_frames / max(self.fade_total_frames, 1)
            old_end = (self.fade_remaining_frames - step) / max(self.fade_total_frames, 1)
            old_gain = np.linspace(old_start, old_end, step, endpoint=False, dtype=np.float32).reshape((-1, 1))
            new_gain = 1.0 - old_gain
            self._mix_layers(outdata[:step], self.current_layers, old_gain)
            self._mix_layers(outdata[:step], self.target_layers, new_gain)
            self.fade_remaining_frames -= step
            if step < frames:
                self.current_layers = self.target_layers
                self.target_layers = []
                self.fade_remaining_frames = 0
                self._mix_layers(outdata[step:], self.current_layers, 1.0)
        else:
            self._mix_layers(outdata, self.current_layers, 1.0)
        np.clip(outdata, -1.0, 1.0, out=outdata)

    def callback(self, outdata: np.ndarray, frames: int, time: Any, status: Any) -> None:
        if status:
            self.status_messages.append(str(status))
        if outdata.shape[0] != frames:
            outdata.fill(0.0)
            return
        self.fill_output(outdata)

    def _layers_for_intent(self, intent: dict[str, Any] | None) -> list[MixerLayer]:
        if not intent:
            return []
        requested = [intent.get("base_bed", {})] + list(intent.get("layers", []))
        layers: list[MixerLayer] = []
        for item in requested:
            asset = self.assets.get(item.get("asset_id"))
            if asset is None:
                self.status_messages.append(f"missing_asset:{item.get('asset_id')}")
                continue
            gain = clamp_gain(float(item.get("default_gain", asset.default_gain)) * float(intent.get("intensity", 1.0)))
            layers.append(MixerLayer(asset=asset, gain=gain))
        return layers

    def _mix_layers(self, outdata: np.ndarray, layers: list[MixerLayer], scale: float | np.ndarray) -> None:
        for layer in layers:
            mix_looped_into(outdata, layer.asset.samples, layer.position, layer.gain * scale)
            layer.position = (layer.position + outdata.shape[0]) % layer.asset.samples.shape[0]


def mix_looped_into(outdata: np.ndarray, samples: np.ndarray, position: int, gain: float | np.ndarray) -> None:
    written = 0
    sample_count = samples.shape[0]
    while written < outdata.shape[0]:
        available = min(sample_count - position, outdata.shape[0] - written)
        outdata[written:written + available] += samples[position:position + available] * gain
        written += available
        position = 0


def clamp_gain(value: float) -> float:
    return max(0.0, min(1.0, value))


class SoundDeviceBackend:
    def __init__(
        self,
        mixer: AudioMixer,
        device: int | str | None = None,
        blocksize: int = 0,
        latency: str | float = "high",
        sounddevice_module: Any | None = None,
    ) -> None:
        self.mixer = mixer
        self.sounddevice = sounddevice_module
        if self.sounddevice is None:
            import sounddevice as sounddevice_module  # type: ignore

            self.sounddevice = sounddevice_module
        self.stream = self.sounddevice.OutputStream(
            samplerate=mixer.config.sample_rate,
            channels=mixer.config.channels,
            dtype="float32",
            device=device,
            blocksize=blocksize,
            latency=latency,
            callback=mixer.callback,
        )

    def start(self) -> None:
        self.stream.start()

    def stop(self) -> None:
        self.stream.stop()

    def close(self) -> None:
        self.stream.close()
