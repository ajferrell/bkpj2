# bkpj2

bkpj2 is the book-to-audio-query side of a local reading-audio workflow.

The current code imports a Calibre library, reads live Calibre E-book Viewer
positions, resolves EPUB CFIs through Calibre helpers, prepares text timelines,
and inspects how a live CFI maps back to book text. The next implementation
target is to turn those resolved book spans into compact audio-intent query
records that can be reviewed and sent to `music-retrieval-lab` for local audio
retrieval.

## Setup

Use the existing virtual environment when available:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Commands use `data` as the default cache. Put `--data-dir` before the
subcommand to use another cache:

```powershell
.\.venv\Scripts\python.exe main.py --data-dir .\data inspect-live
```

Calibre must be installed for CFI resolution and timeline extraction because
the project delegates EPUB spine and CFI behavior to Calibre-compatible helpers.

## Current Commands

Import and list a Calibre library:

```powershell
.\.venv\Scripts\python.exe main.py import-calibre "C:\Users\<you>\Calibre Library"
.\.venv\Scripts\python.exe main.py list-books --epub-only
.\.venv\Scripts\python.exe main.py list-books "Book Title"
```

Prepare a book timeline:

```powershell
.\.venv\Scripts\python.exe main.py prepare-book "Book Title"
.\.venv\Scripts\python.exe main.py prepare-book "Book Title" --regions
.\.venv\Scripts\python.exe main.py prepare-book "Book Title" --debug-text
```

The following `prepare-book` flags still exist from the older ambience
prototype, but they are not part of the active direction and are candidates for
removal in the next cleanup task:

```powershell
.\.venv\Scripts\python.exe main.py prepare-book "Book Title" --regions --atmosphere --audio-intents
```

Inspect prepared and live position data:

```powershell
.\.venv\Scripts\python.exe main.py inspect-book "Book Title" --anchors
.\.venv\Scripts\python.exe main.py inspect-book "Book Title" --live --resolve-cfi --anchors --chain
.\.venv\Scripts\python.exe main.py inspect-book "Book Title" --live --resolve-cfi --anchors --regions --chain --json
.\.venv\Scripts\python.exe main.py inspect-live --resolve-cfi
.\.venv\Scripts\python.exe main.py watch-live --resolve-cfi
```

Capture or check CFI fixtures:

```powershell
.\.venv\Scripts\python.exe main.py cfi-fixtures list
.\.venv\Scripts\python.exe main.py cfi-fixtures check --anchors
```

Compute a Calibre viewer annotation key for an EPUB path:

```powershell
.\.venv\Scripts\python.exe main.py annots-key "C:\path\to\book.epub"
```

Older ambience-prototype commands still exist in this checkout. They are listed
only so the current CLI surface is honest; do not build new work on them unless
the cleanup task explicitly keeps a legacy boundary:

```powershell
.\.venv\Scripts\python.exe main.py region-review init "Book Title"
.\.venv\Scripts\python.exe main.py region-review check "Book Title"
.\.venv\Scripts\python.exe main.py audio-plan inspect "Book Title"
.\.venv\Scripts\python.exe main.py audio-plan simulate "Book Title"
.\.venv\Scripts\python.exe main.py audio-runtime inspect "Book Title"
.\.venv\Scripts\python.exe main.py audio-runtime play "Book Title" --duration-seconds 30
```

Treat these as prototype surfaces until they are either removed or adapted to
the new retrieval handoff.

## Artifacts

Main local artifacts:

- `data/calibre_library_manifest.json`: imported Calibre book metadata, EPUB
  paths, hashes, and viewer annotation keys.
- `data/books/<calibre_book_id>/timeline.json`: prepared spine summary,
  anchors, optional regions, and optional legacy atmosphere/audio-intent data.
- `data/books/<calibre_book_id>/source_units.json`: paragraph-like text block
  offsets and previews used to trace anchors back to EPUB text.
- `data/books/<calibre_book_id>/inspect_text.json`: optional full-text debug
  sidecar written only with `prepare-book --debug-text`.
- `data/books/<calibre_book_id>/region_diagnostics.json`: legacy region
  boundary diagnostics written when regions are prepared.
- `data/books/<calibre_book_id>/region_review.json`: legacy manual boundary
  review artifact.
- `data/cfi_fixtures/*.json`: captured live CFI resolver fixtures.

The planned query handoff artifact does not exist yet. Before adding it, the
next task is to thin the old ambience prototype out of the active code path.
The target shape is defined in [docs/SPEC.md](docs/SPEC.md), and the task queue
is in [docs/TASKS.md](docs/TASKS.md).
