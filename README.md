# Calibre-Native Adaptive Ambience

This repo is being rebuilt around a Calibre-native reading-position spine.
The current active implementation imports a Calibre library, maps EPUB paths to
Calibre E-book Viewer annotation files, prepares source-aligned anchors and
deterministic regions, attaches reviewable deterministic atmosphere labels,
plans restrained declarative audio intents, and can manually preview those
intents through a local audio runtime.

The previous generic EPUB/chunk/round-robin prototype has been moved to
`old/prototype/`.

## Current Commands

All commands accept `--data-dir` before the subcommand to use a cache directory
other than `data`.

### Library and Book Setup

- `import-calibre`: import a Calibre library manifest and compute viewer
  annotation keys for EPUB books.
- `list-books`: list imported books, optionally filtering to EPUB-capable
  entries or a title/author/id query.
- `prepare-book`: extract Calibre-compatible spine text and write a book
  timeline with anchors, optional deterministic regions, optional deterministic
  atmosphere labels, and optional declarative audio intents.
- `clean-book`: remove generated timeline, region, or debug sidecar artifacts
  for one imported book.

```powershell
python main.py import-calibre "C:\Users\<you>\Calibre Library"
python main.py list-books --epub-only
python main.py prepare-book "Book Title" --regions --atmosphere --audio-intents --region-profile normal
python main.py clean-book "Book Title" --regions
```

### Inspection

- `inspect-book`: inspect an imported book, prepared anchors/regions, live
  annotation state, resolved CFI position, or the full coordinate chain.
- `inspect-live`: inspect the newest live Calibre viewer position and match it
  back to an imported book.
- `watch-live`: poll the newest viewer position and optionally capture CFI
  fixtures while reading.
- `audio-plan`: inspect planned declarative audio intents or simulate reading
  traces against transition controls.
- `audio-runtime`: inspect loaded runtime assets or manually play one prepared
  audio intent through the local sounddevice backend.

```powershell
python main.py inspect-book "Book Title" --anchors --regions
python main.py inspect-book "Book Title" --atmosphere
python main.py inspect-book "Book Title" --live --resolve-cfi --anchors --regions --chain
python main.py inspect-live --resolve-cfi
python main.py watch-live --resolve-cfi
python main.py audio-plan inspect "Book Title"
python main.py audio-plan simulate "Book Title"
python main.py audio-runtime inspect "Book Title"
python main.py audio-runtime play "Book Title" --duration-seconds 30
```

### Fixtures and Review

- `cfi-fixtures`: list or check captured live CFI fixtures, optionally requiring
  resolved positions to map to prepared anchors.
- `region-review`: create or check manual review artifacts for expected
  boundaries, noisy false-positive transitions, and generated atmosphere labels.
- `annots-key`: compute the Calibre viewer annotation filename for a specific
  EPUB path.

```powershell
python main.py cfi-fixtures list
python main.py cfi-fixtures check --anchors
python main.py region-review init "Book Title"
python main.py prepare-book "Book Title" --regions --use-region-review
python main.py region-review check "Book Title"
python main.py annots-key "C:\path\to\book.epub"
```

## Current Goal

Prove the inspectable coordinate-to-region chain:

```text
Calibre library EPUB path
-> deterministic viewer annots key
-> live viewer CFI
-> Calibre-resolved spine/href/offset
-> anchor
-> region
-> boundary reasons
-> atmosphere summary
-> audio intent
-> manual runtime preview
```

Live Calibre-driven ambience is still a future stage. Manual playback of a
prepared intent exists now, but it does not yet follow your live reading
position.

## Region, Atmosphere, and Intent Review Workflow

The current region system divides the prepared anchor timeline into larger
regions using chapter starts, scene separators, pacing shifts, keyword shifts,
and length limits. The atmosphere scorer then applies local cue lexicons to
those regions and records conservative labels, abstentions, evidence anchors,
and transition churn. The audio planner maps each region to a declarative
`AudioSceneIntent` with a base category, optional layers, restrained intensity,
fade settings, minimum dwell, and neutral fallback behavior.

Typical flow:

```powershell
python main.py import-calibre "C:\Users\<you>\Calibre Library"
python main.py prepare-book "The Mirror's Truth" --regions --atmosphere --audio-intents
python main.py inspect-book "The Mirror's Truth" --anchors --regions --atmosphere --region-limit 20
python main.py audio-plan inspect "The Mirror's Truth"
python main.py audio-plan simulate "The Mirror's Truth"
python main.py audio-runtime inspect "The Mirror's Truth"
python main.py region-review init "The Mirror's Truth"
```

Interpret the inspect output in layers:

- `Spine items`: Calibre-compatible EPUB spine files extracted for the book.
- `Anchors`: medium-sized text chunks used as stable lookup units.
- `Regions`: larger contiguous anchor ranges that would eventually become the
  minimum audio transition unit.
- `in` and `out`: why the region starts or ends there, such as `chapter_start`,
  `scene_separator`, `keyword_shift`, `pacing_shift`, `max_region_length`, or
  `book_start`/`book_end`.
- `preview`: the first text from that anchor or region, for quick orientation.
- `Boundary candidates`: raw diagnostic edges considered by the region builder.
  These are mainly for tuning and review, not the first thing to read.
- `Atmosphere`: deterministic `setting`, `environment`, `energy`, and `affect`
  labels, confidence bands, evidence anchors, abstentions, comparator status,
  and collapsed transition churn.
- `Audio intents`: planner-facing ambience instructions. Unknown labels,
  missing asset catalogs, or unmatched categories resolve to neutral fallback
  intents instead of hard failures.
- `Audio runtime`: local playback for one prepared intent at a time. It loads
  assets before playback, mixes a base bed plus optional layers, applies fades,
  and keeps disk/JSON/Calibre work out of the audio callback.

After `region-review init`, open
`data/books/<calibre_book_id>/region_review.json`. Mark only boundaries you
care about:

- Set `review_expected` to `true` when a candidate should be a region boundary.
- Set `noisy_false_positive` to `true` when a selected candidate creates a
  distracting or unwanted split.
- Leave uncertain candidates as `null`/`false`.

Then rebuild and check:

```powershell
python main.py prepare-book "The Mirror's Truth" --regions --atmosphere --audio-intents --use-region-review
python main.py region-review check "The Mirror's Truth"
python main.py inspect-book "The Mirror's Truth" --regions --atmosphere --region-limit 20
python main.py audio-plan inspect "The Mirror's Truth"
python main.py audio-runtime inspect "The Mirror's Truth"
```

Use profiles to compare how aggressively the deterministic builder splits:

```powershell
python main.py prepare-book "The Mirror's Truth" --regions --region-profile conservative
python main.py prepare-book "The Mirror's Truth" --regions --region-profile sensitive
```

`conservative` makes fewer, longer regions. `sensitive` makes more, shorter
regions. `normal` is the default.

## Planner Artifacts

New prepared timelines are compact planner-facing `timeline.json` files. Verbose
review data is written to sidecars:

- `source_units.json`: paragraph/source-unit offsets and previews.
- `region_diagnostics.json`: boundary candidates, selected boundaries, rejected
  reasons, and review summaries.
- `inspect_text.json`: optional full-text debug sidecar, written only with
  `prepare-book --debug-text`.

The planner can use `data/books/<calibre_book_id>/audio_assets.json` or an
explicit `--asset-catalog` path. If no usable local catalog is present, it uses
a built-in neutral silent fallback so planner inspection still works.

## Runtime Audio Preview

Phase 5 adds local playback, but only as a manual preview of already-prepared
audio intents. Phase 6 is what will connect live Calibre reading position to
region lookup, planner transitions, and continuous playback.

To hear anything other than silence, provide an asset catalog with local audio
files:

```powershell
python main.py prepare-book "The Mirror's Truth" --regions --atmosphere --audio-intents --asset-catalog ".\data\books\5\audio_assets.json"
python main.py audio-runtime inspect "The Mirror's Truth" --asset-catalog ".\data\books\5\audio_assets.json"
python main.py audio-runtime play "The Mirror's Truth" --asset-catalog ".\data\books\5\audio_assets.json" --region-id 0 --duration-seconds 30
```

Runtime asset requirements:

- `audio_assets.json` uses schema version `1`.
- Each playable asset must be loopable 16-bit PCM `.wav`.
- The default runtime expects 44100 Hz stereo. Override with
  `--sample-rate` and `--channels` only if your catalog was prepared that way.
- Assets are mixed as normalized float32 with conservative `default_gain`.
- Empty or missing planner assets produce silence/neutral fallback.
- Unsupported files or mismatched sample rate/channel count are rejected before
  playback starts.

Minimal catalog shape:

```json
{
  "schema_version": 1,
  "created_at": "2026-06-03T16:00:00",
  "assets": [
    {
      "asset_id": "quiet_room_bed",
      "path": "audio/quiet_room.wav",
      "license": "local test asset",
      "loopable": true,
      "role": "base_bed",
      "categories": ["neutral", "quiet", "interior"],
      "intensity_min": 0.0,
      "intensity_max": 1.0,
      "default_gain": 0.25
    }
  ]
}
```

As-is, if you run `audio-runtime play` without a real matching catalog, the
command can start the runtime but you should expect silence because the built-in
fallback is intentionally silent.
