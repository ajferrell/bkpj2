"""
Audio engine: looping WAV playback with smooth crossfading.
Uses sounddevice for streaming audio with two logical decks.
"""

import numpy as np
import sounddevice as sd
import wave
import threading
import time
from pathlib import Path
from typing import Optional, Dict
import math


class AudioEngine:
    """
    Two-deck audio engine with smooth crossfading.
    Supports looping WAV files and seamless transitions.
    """
    
    def __init__(
        self,
        scene_bins: Dict[str, str],
        crossfade_duration: float = 3.0,
        fade_duration: float = 0.2,
        device: Optional[int] = None
    ):
        self.scene_bins = scene_bins
        self.crossfade_duration = crossfade_duration
        self.fade_duration = fade_duration
        self.device = device
        
        # Audio state
        self.samplerate = 44100
        self.channels = 2
        self.blocksize = 2048
        
        # Deck state
        self.deck_a = None  # {wav_data, position, scene}
        self.deck_b = None
        self.active_deck = 'a'  # Which deck is currently audible
        
        # Crossfade state
        self.crossfade_progress = 0.0  # 0.0 = deck_a, 1.0 = deck_b
        self.is_crossfading = False
        self.target_scene = None
        
        # Master volume envelope
        self.master_volume = 0.0
        self.target_master_volume = 0.0
        self.fade_speed = 1.0 / (self.fade_duration * self.samplerate / self.blocksize)
        
        # Stream
        self.stream = None
        self.lock = threading.Lock()
        self.running = False
        
        # Preload WAV files
        self.wav_cache: Dict[str, np.ndarray] = {}
        self._load_wav_files()
    
    def _load_wav_files(self):
        """Load all WAV files into memory."""
        for scene, wav_path in self.scene_bins.items():
            path = Path(wav_path)
            if path.exists():
                try:
                    self.wav_cache[scene] = self._load_wav(wav_path)
                    print(f"Loaded {scene}: {wav_path}")
                except Exception as e:
                    print(f"Warning: Failed to load {wav_path}: {e}")
                    # Create silent fallback
                    self.wav_cache[scene] = np.zeros((self.samplerate * 2, 2), dtype=np.float32)
            else:
                print(f"Warning: WAV file not found: {wav_path}, using silence")
                self.wav_cache[scene] = np.zeros((self.samplerate * 2, 2), dtype=np.float32)
    
    def _load_wav(self, path: str) -> np.ndarray:
        """Load a WAV file and convert to normalized float32 stereo."""
        with wave.open(path, 'rb') as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            n_frames = wf.getnframes()
            
            # Read raw audio
            raw_data = wf.readframes(n_frames)
            
            # Convert to numpy array
            if sampwidth == 1:
                dtype = np.uint8
                data = np.frombuffer(raw_data, dtype=dtype).astype(np.float32)
                data = (data - 128) / 128.0
            elif sampwidth == 2:
                dtype = np.int16
                data = np.frombuffer(raw_data, dtype=dtype).astype(np.float32)
                data = data / 32768.0
            elif sampwidth == 4:
                dtype = np.int32
                data = np.frombuffer(raw_data, dtype=dtype).astype(np.float32)
                data = data / 2147483648.0
            else:
                raise ValueError(f"Unsupported sample width: {sampwidth}")
            
            # Convert to stereo if mono
            if n_channels == 1:
                data = np.column_stack([data, data])
            else:
                data = data.reshape(-1, n_channels)
            
            # Resample if necessary (simple nearest-neighbor for now)
            if framerate != self.samplerate:
                ratio = self.samplerate / framerate
                new_length = int(len(data) * ratio)
                indices = (np.arange(new_length) / ratio).astype(int)
                data = data[indices]
            
            return data.astype(np.float32)
    
    def start(self):
        """Start the audio stream."""
        if self.running:
            return
        
        self.running = True
        self.stream = sd.OutputStream(
            samplerate=self.samplerate,
            channels=self.channels,
            blocksize=self.blocksize,
            device=self.device,
            callback=self._audio_callback,
            dtype=np.float32
        )
        self.stream.start()
        print(f"Audio engine started (device={self.device}, sr={self.samplerate})")
    
    def stop(self):
        """Stop the audio stream gracefully."""
        if not self.running:
            return
        
        # Fade out
        with self.lock:
            self.target_master_volume = 0.0
        
        # Wait for fade
        time.sleep(self.fade_duration)
        
        self.running = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        
        print("Audio engine stopped")
    
    def play_scene(self, scene: str):
        """Switch to a new scene with crossfade."""
        if scene not in self.wav_cache:
            print(f"Warning: Unknown scene '{scene}', ignoring")
            return
        
        with self.lock:
            # Check if any audio is currently playing
            active = self.deck_a if self.active_deck == 'a' else self.deck_b
            
            # If no audio is playing, start immediately on deck A
            if active is None:
                self.deck_a = {
                    'data': self.wav_cache[scene],
                    'position': 0,
                    'scene': scene
                }
                self.active_deck = 'a'
                self.target_master_volume = 1.0
                self.is_crossfading = False
                print(f"Started scene: {scene}")
                return
            
            # If already playing this scene, ignore
            if active['scene'] == scene:
                return
            
            # Start crossfade to new scene
            inactive_deck = 'b' if self.active_deck == 'a' else 'a'
            
            if inactive_deck == 'a':
                self.deck_a = {
                    'data': self.wav_cache[scene],
                    'position': 0,
                    'scene': scene
                }
            else:
                self.deck_b = {
                    'data': self.wav_cache[scene],
                    'position': 0,
                    'scene': scene
                }
            
            self.is_crossfading = True
            self.crossfade_progress = 0.0
            self.target_scene = scene
            print(f"Crossfading to scene: {scene}")
    
    def get_current_scene(self) -> Optional[str]:
        """Get the currently playing scene."""
        with self.lock:
            active = self.deck_a if self.active_deck == 'a' else self.deck_b
            return active['scene'] if active else None
    
    def _audio_callback(self, outdata, frames, time_info, status):
        """Audio callback for sounddevice stream."""
        if status:
            print(f"Audio status: {status}")
        
        with self.lock:
            # Initialize output buffer
            outdata.fill(0)
            
            # Update master volume envelope
            if self.master_volume < self.target_master_volume:
                self.master_volume = min(
                    self.target_master_volume,
                    self.master_volume + self.fade_speed
                )
            elif self.master_volume > self.target_master_volume:
                self.master_volume = max(
                    self.target_master_volume,
                    self.master_volume - self.fade_speed
                )
            
            # If no audio, return silence
            active = self.deck_a if self.active_deck == 'a' else self.deck_b
            if active is None:
                return
            
            # Generate audio from active deck(s)
            if not self.is_crossfading:
                # Single deck playback
                if active:
                    self._fill_buffer(outdata, active, 1.0)
            else:
                # Crossfading between decks
                if self.deck_a and self.deck_b:
                    # Update crossfade progress
                    crossfade_speed = 1.0 / (
                        self.crossfade_duration * self.samplerate / self.blocksize
                    )
                    self.crossfade_progress = min(1.0, self.crossfade_progress + crossfade_speed)
                    
                    # Cosine crossfade curve (smooth)
                    t = self.crossfade_progress
                    fade_curve = 0.5 - 0.5 * math.cos(t * math.pi)
                    
                    # Mix both decks
                    if self.active_deck == 'a':
                        vol_a = 1.0 - fade_curve
                        vol_b = fade_curve
                    else:
                        vol_a = fade_curve
                        vol_b = 1.0 - fade_curve
                    
                    self._fill_buffer(outdata, self.deck_a, vol_a)
                    temp_buffer = np.zeros_like(outdata)
                    self._fill_buffer(temp_buffer, self.deck_b, vol_b)
                    outdata[:] += temp_buffer
                    
                    # Complete crossfade
                    if self.crossfade_progress >= 1.0:
                        self.active_deck = 'b' if self.active_deck == 'a' else 'a'
                        self.is_crossfading = False
                        self.crossfade_progress = 0.0
                        print(f"Crossfade complete: now playing {self.target_scene}")
                        
                        # Clear inactive deck
                        if self.active_deck == 'a':
                            self.deck_b = None
                        else:
                            self.deck_a = None
            
            # Apply master volume
            outdata[:] *= self.master_volume
    
    def _fill_buffer(self, buffer, deck, volume):
        """Fill buffer with audio from a deck, handling looping."""
        if deck is None:
            return
        
        data = deck['data']
        position = deck['position']
        frames = len(buffer)
        data_length = len(data)
        
        if data_length == 0:
            return
        
        # Fill buffer with looping
        filled = 0
        while filled < frames:
            remaining = frames - filled
            available = data_length - position
            to_copy = min(remaining, available)
            
            buffer[filled:filled + to_copy] += data[position:position + to_copy] * volume
            
            filled += to_copy
            position += to_copy
            
            # Loop
            if position >= data_length:
                position = 0
        
        # Update position
        deck['position'] = position


def test_dummy_mode():
    """Test the audio engine in dummy mode."""
    import json
    
    # Load config
    with open('config.json', 'r') as f:
        config = json.load(f)
    
    # Scene list
    scenes = list(config['scene_bins'].keys())
    
    # Create engine
    engine = AudioEngine(
        scene_bins=config['scene_bins'],
        crossfade_duration=config['crossfade_duration_sec'],
        fade_duration=config['fade_duration_sec'],
        device=config.get('audio_device')
    )
    
    # Start
    engine.start()
    
    try:
        print("\n=== Dummy Mode: Cycling through scenes ===")
        scene_idx = 0
        cycle_time = config.get('dummy_cycle_sec', 10)
        
        while True:
            scene = scenes[scene_idx % len(scenes)]
            print(f"\n[DUMMY] Playing scene: {scene}")
            engine.play_scene(scene)
            
            time.sleep(cycle_time)
            scene_idx += 1
    
    except KeyboardInterrupt:
        print("\n\nStopping...")
    finally:
        engine.stop()


if __name__ == '__main__':
    test_dummy_mode()
