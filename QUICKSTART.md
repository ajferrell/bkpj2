# Quick Start Guide

## Setup (5 minutes)

1. **Install dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```

2. **Generate placeholder audio files:**
   ```powershell
   python generate_placeholders.py
   ```
   Choose option 2 (tone placeholders) to hear different tones per scene.

3. **Test in dummy mode:**
   ```powershell
   python main.py run --dummy
   ```
   You should hear audio cycling through scenes every 10 seconds.
   Press Ctrl+C to stop.

## Using with Calibre

1. **Preprocess your EPUB:**
   ```powershell
   python main.py preprocess "C:\path\to\your\book.epub"
   ```
   This creates `data/<book_id>/timeline.json`.

2. **Start the orchestrator:**
   ```powershell
   python main.py run
   ```

3. **Open the book in Calibre:**
   - Launch Calibre E-book viewer
   - Open the same EPUB you preprocessed
   - Start reading

4. **Watch the magic:**
   - As you read, the orchestrator detects your position
   - Ambient audio changes based on your progress
   - Console shows: `book=... | chunk=... | scene=... | conf=...`

## Console Status Explained

```
book=mybook | chunk=42 | scene=tension | active=tension | dwell=85s | conf=0.89
```

- **book**: Current book ID
- **chunk**: Current reading position (chunk number)
- **scene**: Target scene (where we want to be)
- **active**: Actually playing scene (what you hear)
- **dwell**: Seconds remaining before scene can switch
- **conf**: Confidence in position detection (0.0-1.0)

## Testing Individual Components

**Test audio engine:**
```powershell
python audio_engine.py
```

**Test Calibre watcher:**
```powershell
python watcher.py
```
Open a book in Calibre and navigate around. You should see position updates.

**Test CFI resolver:**
```powershell
python resolver.py
```

## Troubleshooting

**Problem:** "No module named 'ebooklib'"
- **Solution:** `pip install -r requirements.txt`

**Problem:** Audio stuttering or glitches
- **Solution:** Increase `blocksize` in audio_engine.py or close other audio apps

**Problem:** Scenes switching too fast
- **Solution:** Edit config.json, increase `dwell_time_sec` to 180 or higher

**Problem:** Calibre not detected
- **Solution:** 
  1. Open Calibre and navigate in a book
  2. Check that `%APPDATA%\calibre\viewer\annots` contains JSON files
  3. Verify the path in config.json matches

**Problem:** Silent audio
- **Solution:** 
  1. Run `python generate_placeholders.py` if you haven't already
  2. Check that `audio/*.wav` files exist
  3. Check your system volume

## Next Steps

1. **Replace placeholder audio**: Put real ambient loops in `audio/` directory
   - Each WAV should be stereo, 44.1kHz, 16-bit
   - Make them seamlessly loopable (same start/end)
   - Recommended: 30-60 second loops

2. **Tune the config**: Adjust `dwell_time_sec` and `k_consecutive_chunks` to your reading speed

3. **Try different books**: Preprocess multiple EPUBs and switch between them in Calibre

4. **Check the logs**: Look at `orchestrator.jsonl` to see detailed events

## Tips

- **For faster switching**: Set `dwell_time_sec` to 60 and `k_consecutive_chunks` to 1
- **For stable audio**: Set `dwell_time_sec` to 180 and `k_consecutive_chunks` to 3
- **For debugging**: Watch `orchestrator.jsonl` with `tail -f` or similar
- **For testing**: Use `--dummy` mode to verify audio works before trying with Calibre

Enjoy your immersive reading experience! 🎵📖
