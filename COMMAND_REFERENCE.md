# EPUB Ambience Orchestrator - Command Reference

## Installation & Setup

```powershell
# Install dependencies (already done ✓)
pip install -r requirements.txt

# Verify setup
python check_setup.py

# Generate test audio if needed
python generate_placeholders.py
```

## Common Commands

### Testing & Debugging

```powershell
# Test audio engine in isolation
python audio_engine.py

# Test Calibre watcher (shows detected positions)
python watcher.py

# Test CFI resolver
python resolver.py

# Full system test without Calibre (cycles scenes)
python main.py run --dummy

# Test with verbose output
python main.py run --dummy 2>&1 | Tee-Object dummy-test.log
```

### Production Use

```powershell
# Preprocess a new EPUB (one-time setup per book)
python main.py preprocess "C:\path\to\book.epub"

# Run orchestrator (watches Calibre)
python main.py run

# Run with specific book
python main.py run --book-id my_book_id

# Run with custom config
python main.py run --config custom-config.json
```

## Configuration Tuning

Edit `config.json`:

```json
{
  "dwell_time_sec": 120,         // Fast readers: 60, Slow: 180
  "k_consecutive_chunks": 2,     // Fast: 1, Stable: 3
  "crossfade_duration_sec": 3.0, // Smooth: 3-5s, Quick: 1-2s
  "dummy_cycle_sec": 10          // Test speed: 5-20s
}
```

## Log Files

```powershell
# View event log (real-time)
Get-Content orchestrator.jsonl -Wait

# Analyze with PowerShell
@(Get-Content orchestrator.jsonl | ConvertFrom-Json) | 
  Where-Object { $_.reason -match 'switched' } |
  Select-Object timestamp, chunk_id, target_scene

# Export to CSV for analysis
@(Get-Content orchestrator.jsonl | ConvertFrom-Json) |
  Export-Csv analysis.csv -NoTypeInformation
```

## File Locations

| File | Purpose |
|------|---------|
| `config.json` | Scene bins, timings, audio device |
| `main.py` | CLI entry point - start here |
| `audio_engine.py` | WAV playback engine |
| `controller.py` | Scene switching logic |
| `resolver.py` | Position → chunk mapping |
| `watcher.py` | Calibre monitor |
| `preprocessor.py` | EPUB text extraction |
| `logger.py` | Console + JSONL output |
| `data/<book_id>/timeline.json` | Preprocessed book data |
| `audio/` | WAV files for scenes |
| `orchestrator.jsonl` | Event log (append-only) |

## Keyboard Shortcuts

```
Ctrl+C      Stop running process
Ctrl+Break  Force stop (Windows)
```

## Environment Variables

```powershell
# Set custom Calibre path (if not in default location)
$env:CALIBRE_ANNOTS = "C:\custom\path"

# Check default Calibre path
$env:APPDATA
# → C:\Users\<username>\AppData\Roaming\calibre\viewer\annots
```

## Status Line Fields

```
book=mybook | chunk=42 | scene=tension | active=conflict | dwell=85s | conf=0.89

book       → Current book ID
chunk      → Detected reading position (chunk number)
scene      → Target scene (should play)
active     → Actually playing (might be different during switch pending)
dwell      → Seconds until scene can change
conf       → Confidence in position (0.0-1.0)
```

## Performance Benchmarks

```
Dummy Mode Startup: ~1s
Calibre Detection:  ~1s
Scene Crossfade:    3s (configurable)
Memory Usage:       ~80-100 MB
CPU Usage:          <5% (I/O bound)
```

## Troubleshooting Commands

```powershell
# Check Python version
python --version

# Check dependencies
python -c "import ebooklib, sounddevice, numpy, watchdog; print('✓ All OK')"

# Check audio device
python -c "import sounddevice as sd; print(sd.default); print(sd.query_devices())"

# Verify Calibre path exists
Test-Path $env:APPDATA\calibre\viewer\annots

# List annotation files
Get-ChildItem $env:APPDATA\calibre\viewer\annots -Filter "*.json"

# Check if process is running
Get-Process python | Where-Object { $_.Path -match 'main.py' }

# Kill running process
Stop-Process -Name python -Force
```

## Workflow for New Book

```powershell
# 1. Preprocess the EPUB
python main.py preprocess "C:\eBooks\mybook.epub"
# → Creates data/mybook/timeline.json

# 2. Start orchestrator
python main.py run

# 3. Open book in Calibre
# (Just open the same EPUB file)

# 4. Read and enjoy!
# Ambience should change as you read

# 5. Analyze what happened
Get-Content orchestrator.jsonl | ConvertFrom-Json | Format-Table timestamp, chunk_id, target_scene, active_scene
```

## Quick Diagnostics

```powershell
# Is Calibre being detected?
python watcher.py
# Should show updates when you navigate in Calibre

# Does audio work?
python main.py run --dummy
# Should cycle through scenes with audio every 10s

# Is position being resolved correctly?
python resolver.py
# Tests CFI parsing

# Is everything configured?
python check_setup.py
# Shows 6/6 if ready
```

## Tips & Tricks

1. **Faster feedback**: Set `dummy_cycle_sec: 5` in config.json
2. **Debugging**: Use `tail -f orchestrator.jsonl` in another terminal
3. **Different speeds**: Create multiple config files (config-fast.json, config-slow.json)
4. **Batch preprocess**: `for $f in Get-ChildItem *.epub { python main.py preprocess $f }`
5. **Background run**: `Start-Job -ScriptBlock { python main.py run }`

---

See `README.md` for full documentation or `QUICKSTART.md` for beginners guide.
