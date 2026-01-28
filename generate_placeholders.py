"""
Generate silent placeholder WAV files for testing.
Run this if you don't have WAV files yet.
"""

import wave
import numpy as np
from pathlib import Path


def generate_silent_wav(output_path: str, duration_sec: float = 10.0):
    """Generate a silent WAV file."""
    samplerate = 44100
    channels = 2
    n_frames = int(samplerate * duration_sec)
    
    # Silent audio
    audio_data = np.zeros((n_frames, channels), dtype=np.int16)
    
    # Write WAV
    with wave.open(output_path, 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(samplerate)
        wf.writeframes(audio_data.tobytes())
    
    print(f"Created: {output_path}")


def generate_tone_wav(output_path: str, frequency: float = 440.0, duration_sec: float = 10.0):
    """Generate a simple tone WAV file for testing."""
    samplerate = 44100
    channels = 2
    n_frames = int(samplerate * duration_sec)
    
    # Generate tone
    t = np.linspace(0, duration_sec, n_frames, endpoint=False)
    tone = np.sin(2 * np.pi * frequency * t)
    
    # Scale to 16-bit and convert to stereo
    tone = (tone * 0.3 * 32767).astype(np.int16)
    audio_data = np.column_stack([tone, tone])
    
    # Write WAV
    with wave.open(output_path, 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(audio_data.tobytes())
    
    print(f"Created: {output_path} ({frequency} Hz)")


if __name__ == '__main__':
    audio_dir = Path('audio')
    audio_dir.mkdir(exist_ok=True)
    
    print("Generating placeholder WAV files...\n")
    
    scenes = {
        'conflict': 220.0,    # A3
        'tension': 246.94,    # B3
        'movement': 261.63,   # C4
        'dialogue': 293.66,   # D4
        'reflection': 329.63, # E4
        'wonder': 349.23,     # F4
    }
    
    mode = input("Generate (1) silent or (2) tone placeholders? [1/2]: ").strip()
    
    if mode == '1':
        # Silent placeholders
        for scene in scenes.keys():
            output_path = audio_dir / f"{scene}.wav"
            generate_silent_wav(str(output_path), duration_sec=30.0)
    else:
        # Tone placeholders (different frequency per scene)
        for scene, freq in scenes.items():
            output_path = audio_dir / f"{scene}.wav"
            generate_tone_wav(str(output_path), frequency=freq, duration_sec=30.0)
    
    print("\n✓ Done! Placeholder WAV files created in audio/")
    print("\nReplace these with real ambient audio files for production use.")
