# Calibre-Native Adaptive Ambience

This repo is being rebuilt around a Calibre-native reading-position spine.
The current active implementation imports a Calibre library, maps EPUB paths to
Calibre E-book Viewer annotation files, prepares source-aligned anchors and
deterministic regions, and inspects live EPUB CFI positions against that
timeline.

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
  timeline with source units, anchors, and optional deterministic regions.
- `clean-book`: remove generated timeline, region, or debug sidecar artifacts
  for one imported book.

```powershell
python main.py import-calibre "C:\Users\<you>\Calibre Library"
python main.py list-books --epub-only
python main.py prepare-book "Book Title" --regions --region-profile normal
python main.py clean-book "Book Title" --regions
```

### Inspection

- `inspect-book`: inspect an imported book, prepared anchors/regions, live
  annotation state, resolved CFI position, or the full coordinate chain.
- `inspect-live`: inspect the newest live Calibre viewer position and match it
  back to an imported book.
- `watch-live`: poll the newest viewer position and optionally capture CFI
  fixtures while reading.

```powershell
python main.py inspect-book "Book Title" --anchors --regions
python main.py inspect-book "Book Title" --live --resolve-cfi --anchors --regions --chain
python main.py inspect-live --resolve-cfi
python main.py watch-live --resolve-cfi
```

### Fixtures and Review

- `cfi-fixtures`: list or check captured live CFI fixtures, optionally requiring
  resolved positions to map to prepared anchors.
- `region-review`: create or check manual region review artifacts for expected
  boundaries and noisy false-positive transitions.
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
```

Semantic labels, audio scores, and the adaptive mixer are intentionally still
future stages.

## Region Review Workflow

The current region system is a deterministic review tool. It does not yet know
what a scene "means"; it only divides the prepared anchor timeline into larger
regions using chapter starts, scene separators, pacing shifts, keyword shifts,
and length limits.

Typical flow:

```powershell
python main.py import-calibre "C:\Users\<you>\Calibre Library"
python main.py prepare-book "The Mirror's Truth" --regions
python main.py inspect-book "The Mirror's Truth" --anchors --regions --region-limit 20
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

After `region-review init`, open
`data/books/<calibre_book_id>/region_review.json`. Mark only boundaries you
care about:

- Set `review_expected` to `true` when a candidate should be a region boundary.
- Set `noisy_false_positive` to `true` when a selected candidate creates a
  distracting or unwanted split.
- Leave uncertain candidates as `null`/`false`.

Then rebuild and check:

```powershell
python main.py prepare-book "The Mirror's Truth" --regions --use-region-review
python main.py region-review check "The Mirror's Truth"
python main.py inspect-book "The Mirror's Truth" --regions --region-limit 20
```

Use profiles to compare how aggressively the deterministic builder splits:

```powershell
python main.py prepare-book "The Mirror's Truth" --regions --region-profile conservative
python main.py prepare-book "The Mirror's Truth" --regions --region-profile sensitive
```

`conservative` makes fewer, longer regions. `sensitive` makes more, shorter
regions. `normal` is the default.
