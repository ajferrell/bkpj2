# EPUB Ambience Orchestrator v1

A Python-only prototype that plays looping ambient audio synchronized with your reading progress in Calibre's E-book viewer.

## Features

- 🎵 **Scene-based ambience**: 6 fixed scene bins (conflict, tension, movement, dialogue, reflection, wonder)
- 🔄 **Smart crossfading**: Smooth transitions between scenes with no clicks
- 📖 **Calibre integration**: Watches Calibre's annotation files for reading progress
- 🧠 **Anti-thrash logic**: Minimum dwell times and consecutive chunk requirements prevent rapid switching
- 📝 **Comprehensive logging**: Console status line + JSONL event logs
- 🎭 **Dummy mode**: Test without Calibre by cycling through scenes automatically

## Prerequisites

- Python 3.8+
- Calibre E-book viewer
- WAV files for each scene bin

## Installation

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Place WAV files in the `audio/` directory:
   - `audio/conflict.wav`
   - `audio/tension.wav`
   - `audio/movement.wav`
   - `audio/dialogue.wav`
   - `audio/reflection.wav`
   - `audio/wonder.wav`

   (Note: If WAV files are missing, the system will use silence as a fallback)

## Usage

### 1. Preprocess an EPUB

First, preprocess your EPUB to extract text and build a chunk timeline:

```bash
python main.py preprocess path/to/your/book.epub
```

This creates `data/<book_id>/timeline.json` containing:
- Canonical text extracted from XHTML
- Chunks of ~250-400 words (never split paragraphs)
- Spine positions and character offsets

### 2. Run the orchestrator

**Live mode** (watches Calibre):
```bash
python main.py run
```

**Dummy mode** (cycles through scenes for testing):
```bash
python main.py run --dummy
```

### Console Output

While running, you'll see a status line like:
```
book=mybook | chunk=42 | scene=tension | active=tension | dwell=85s | conf=0.89
```

### Logs

Events are logged to `orchestrator.jsonl`:
```json
{"timestamp": "2026-01-19T12:34:56Z", "chunk_id": 42, "confidence": 0.89, "target_scene": "tension", "active_scene": "conflict", "reason": "switched_from_conflict"}
```

## Configuration

Edit `config.json` to customize:

```json
{
  "scene_bins": {
    "conflict": "audio/conflict.wav",
    ...
  },
  "dwell_time_sec": 120,           // Minimum time before switching scenes
  "k_consecutive_chunks": 2,       // Required consecutive chunks to confirm
  "crossfade_duration_sec": 3.0,   // Crossfade length
  "fade_duration_sec": 0.2,        // Fade in/out on start/stop
  "dummy_cycle_sec": 10            // Dummy mode cycle time
}
```

## How It Works

### Architecture

```
Calibre Annotations
        ↓
    Watcher ─→ EPUBCFI
        ↓
    Resolver ─→ chunk_id + confidence
        ↓
    Controller (state machine)
        ↓
    Audio Engine ─→ 🔊 Looping WAV
```

### State Machine

1. **IDLE**: No reading detected
2. **TRACKING**: Reading detected, waiting for K consecutive chunks
3. **PLAYING**: Audio playing, scene locked until dwell time expires
4. **SWITCH_PENDING**: New scene detected, waiting for K consecutive chunks

### CFI Resolution

The resolver parses EPUBCFI strings like:
```
epubcfi(/6/4[chap01]!/4/2/16:23)
```

And coarsely maps them to chunk_id using:
- Spine index (chapter)
- Character offset within chapter
- Stickiness for low-confidence positions

### Scene Assignment (v1)

Currently uses a dummy round-robin assignment:
```python
scene = SCENES[chunk_id % len(SCENES)]
```

Future versions will use ML-based scene classification.

## Project Structure

```
book_project2/
├── main.py              # CLI entry point
├── audio_engine.py      # Two-deck WAV player with crossfading
├── controller.py        # State machine and anti-thrash logic
├── watcher.py           # Calibre annotations watcher
├── resolver.py          # EPUBCFI → chunk_id resolver
├── preprocessor.py      # EPUB text extraction and chunking
├── logger.py            # JSONL logging and status display
├── config.json          # Configuration
├── requirements.txt     # Python dependencies
├── data/                # Book timelines (generated)
│   └── <book_id>/
│       └── timeline.json
└── audio/               # WAV files (user-provided)
    ├── conflict.wav
    ├── tension.wav
    └── ...
```

## Testing

### Test Audio Engine (Dummy Mode)
```bash
python audio_engine.py
```

### Test Calibre Watcher
```bash
python watcher.py
```

### Test CFI Resolver
```bash
python resolver.py
```

### Test Full System (Dummy Mode)
```bash
python main.py run --dummy
```

## Troubleshooting

**"No timeline found"**: Run `python main.py preprocess <epub_path>` first

**No audio playing**: Check that WAV files exist in `audio/` directory

**Calibre not detected**: Verify Calibre's annotation path in config.json
- Default: `%APPDATA%\calibre\viewer\annots`
- Calibre must have saved annotations for the book

**Rapid scene switching**: Increase `dwell_time_sec` or `k_consecutive_chunks` in config.json

## Limitations (v1)

- Single Python process only (no GUI)
- One book at a time (uses most recent annots file)
- Coarse CFI resolution (may drift on complex EPUBs)
- Dummy scene assignment (no semantic analysis)
- Windows-only (path handling, sounddevice)

## Future Enhancements

- ML-based scene classification (v2)
- Multi-book support
- Better CFI resolution with EPUB structure analysis
- Audio effects (reverb, EQ per scene)
- Web-based monitoring dashboard
- Cross-platform support

## License

MIT License - feel free to modify and extend!

---

**Note**: This is a prototype. Scene assignment is currently round-robin for demonstration purposes. Real scene classification requires training a model on labeled text data.
