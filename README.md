# EPUB Ambience Orchestrator v1

A Python-only prototype that plays looping ambient audio synchronized with your reading progress in Calibre's E-book viewer.

## Features

- 🎵 **Scene-based ambience**: 6 fixed scene bins (conflict, tension, movement, dialogue, reflection, wonder)
- 🔄 **Smart crossfading**: Smooth transitions between scenes with no clicks
- 📖 **Calibre integration**: Watches Calibre's annotation files for reading progress
- 🎯 **Exact CFI resolution**: Uses Calibre's own CFI parser for precise position mapping (no heuristics)
- 🧠 **Anti-thrash logic**: Minimum dwell times and consecutive chunk requirements prevent rapid switching
- 📝 **Comprehensive logging**: Console status line + JSONL event logs
- 🎭 **Dummy mode**: Test without Calibre by cycling through scenes automatically

## Prerequisites

- Python 3.8+
- Calibre E-book viewer (for CFI resolution)
- WAV files for each scene bin

## Installation

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. **(Optional)** Install ML dependencies for scene scoring:
   ```bash
   pip install transformers torch
   ```

3. Place WAV files in the `audio/` directory:
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
- **Spine entries**: Global start positions for each document
- **Chunks**: Global character offsets for each ~250-400 word chunk
- **Canonicalized text**: Deterministic extraction (strips scripts/styles, normalizes whitespace)

Also creates `data/<book_id>/canonical_text.txt` with the full canonical text stream.

### 1b. ML Scene Scoring (Optional)

Add automatic scene labels using zero-shot NLI classification:

```bash
# During preprocessing:
python main.py preprocess path/to/your/book.epub --ml

# Or separately on already-preprocessed books:
python main.py score_ml <book_id>

# Force recomputation:
python main.py score_ml <book_id> --force

# Use a different model:
python main.py score_ml <book_id> --model facebook/bart-large-mnli
```

This assigns scene labels to each chunk:
- Uses the 6 fixed scene labels: `conflict`, `tension`, `movement`, `dialogue`, `reflection`, `wonder`
- Small chunks (< 40 words or < 200 chars) inherit labels from neighbors
- Large chunks (> 450 words) are truncated for faster scoring
- Results are stored in `timeline.json` with per-chunk `scene_label`, `scene_scores`, `scene_conf`
- Summary statistics saved to `data/<book_id>/ml_summary.json`

**Resume support**: Progress is saved every 10 chunks. If interrupted, just re-run the same command to pick up where you left off.

**Running long jobs with tmux/screen** (recommended for large books):
```bash
# Start a tmux session
tmux new -s ml_scoring

# Run the scoring (can take 30+ minutes for large books)
python main.py score_ml <book_id>

# Detach: Ctrl+B, then D
# Reattach later:
tmux attach -t ml_scoring

# Or use screen:
screen -S ml_scoring
python main.py score_ml <book_id>
# Detach: Ctrl+A, then D
# Reattach: screen -r ml_scoring
```

On Windows without tmux, use Windows Terminal's background tabs or run in a minimized PowerShell window. The incremental saves mean you can safely Ctrl+C and resume later.

### 2. Run the orchestrator

**Live mode** (watches Calibre):
```bash
python main.py run
```

**Dummy mode** (cycles through scenes for testing):
```bash
python main.py run --dummy
```

### 3. Link Calibre to Your Book

When Calibre opens a book, it creates an annotation file with an internal ID (a hash).
You need to link this ID to your preprocessed book once:

**Option A**: Let the system auto-detect (recommended)
1. Open the book in Calibre's E-book viewer
2. Run `python main.py run` 
3. The system will show the Calibre ID it detected
4. Follow the prompt to link it

**Option B**: Manual linking
```bash
# First, find your Calibre ID by looking in:
# %APPDATA%\calibre\viewer\annots\
# The filename (without .json) is the Calibre ID

# Then link it:
python main.py link <calibre_id> <book_id>
```

**List preprocessed books** (to find your book_id):
```bash
python main.py list
```

### 4. Test CFI Resolution

Test the CFI resolver with specific CFIs:
```bash
python tests/test_resolver.py path/to/your/book.epub "epubcfi(/8/2/4/140/1:5)"
```

### Console Output

While running, you'll see a status line like:
```
book=mybook | chunk=42/317 (13.2%) | scene=tension | active=tension | dwell=85s | conf=0.89
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
Calibre Annotations (%APPDATA%\calibre\viewer\annots\*.json)
        ↓
    Watcher ─→ EPUBCFI string
        ↓
    calibre-debug (resolve_cfi_calibre.py)
        ↓
    spine_index + local_char_offset
        ↓
    ChunkIndex (binary search)
        ↓
    chunk_id
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

### CFI Resolution (Exact)

The resolver uses Calibre's own CFI parser for exact position mapping:

1. **Parse CFI**: Extract spine index from CFI path
   ```
   epubcfi(/8/2/4/140/1:5) → spine_index=3
   ```

2. **Resolve element**: Use Calibre APIs to navigate DOM to target element

3. **Compute offset**: Traverse canonical text to find exact character offset

4. **Binary search**: Look up chunk by global character offset

This is executed via `calibre-debug --exec-file tools/resolve_cfi_calibre.py`.

### Timeline Format (v2)

```json
{
  "book_id": "my_book",
  "epub_path": "/path/to/book.epub",
  "total_chars": 450000,
  "spine": [
    {"spine_index": 0, "href": "chapter1.xhtml", "global_start_char": 0, "canonical_len": 5000, "sha256": "..."},
    {"spine_index": 1, "href": "chapter2.xhtml", "global_start_char": 5002, "canonical_len": 8000, "sha256": "..."}
  ],
  "chunks": [
    {"chunk_id": 0, "start_char_global": 0, "end_char_global": 1500, "start_doc_spine_index": 0},
    {"chunk_id": 1, "start_char_global": 1500, "end_char_global": 3000, "start_doc_spine_index": 0}
  ]
}
```

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
├── config.json          # Configuration
├── requirements.txt     # Python dependencies
├── README.md            # This file
├── src/                 # Core modules
│   ├── __init__.py
│   ├── audio_engine.py      # Two-deck WAV player with crossfading
│   ├── controller.py        # State machine and anti-thrash logic
│   ├── watcher.py           # Calibre annotations watcher
│   ├── preprocessor.py      # EPUB text extraction and chunking
│   ├── canonicalize.py      # Shared text canonicalization logic
│   ├── chunk_index.py       # Binary search chunk lookup
│   ├── resolver_calibre.py  # Runtime CFI resolver (calls calibre-debug)
│   ├── runtime_orchestrator.py  # High-level orchestration
│   ├── logger.py            # JSONL logging and status display
│   └── calibre/
│       └── cfi_helper.py    # Calibre CFI helper (runs under calibre-debug)
├── tests/               # Test suite
│   ├── test_resolver.py     # Test CFI resolution
│   └── check_setup.py       # Verify installation
├── data/                # Book timelines (generated)
│   └── <book_id>/
│       └── timeline.json
└── audio/               # WAV files (user-provided)
    ├── conflict.wav
    ├── tension.wav
    └── ...
```

## Testing

### Test CFI Resolution
```bash
python tests/test_resolver.py path/to/book.epub "epubcfi(/8/2/4/140/1:5)"
```

### Test Full System (Dummy Mode)
```bash
python main.py run --dummy
```

## Troubleshooting

**"No timeline found"**: Run `python main.py preprocess <epub_path>` first

**"Unknown Calibre ID"**: Link the Calibre ID to your book with `python main.py link <calibre_id> <book_id>`

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
