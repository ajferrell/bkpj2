# bkpj2

bkpj2 is the book-to-audio-query side of a local reading-audio workflow.

The current code imports a Calibre library, reads live Calibre E-book Viewer
positions, resolves EPUB CFIs through Calibre helpers, prepares text-block
timelines, inspects how a live CFI maps back to book text, and exports compact
manual audio-intent query records for review or downstream local retrieval.

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
.\.venv\Scripts\python.exe main.py prepare-book "Book Title" --debug-text
```

Inspect prepared and live position data:

```powershell
.\.venv\Scripts\python.exe main.py inspect-book "Book Title" --anchors
.\.venv\Scripts\python.exe main.py inspect-book "Book Title" --live --resolve-cfi --anchors --chain
.\.venv\Scripts\python.exe main.py inspect-live --resolve-cfi
.\.venv\Scripts\python.exe main.py watch-live --resolve-cfi
```

Export one manual query record from an explicit resolved coordinate:

```powershell
.\.venv\Scripts\python.exe main.py export-query "Book Title" --spine-index 12 --local-char-offset 2450 --query-text "dark, quiet instrumental tension; sparse low strings; no vocals"
```

Export one manual query record from the current live Calibre position:

```powershell
.\.venv\Scripts\python.exe main.py export-query "Book Title" --live --query-file .\query.txt
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

Clean generated artifacts:

```powershell
.\.venv\Scripts\python.exe main.py clean-book "Book Title" --timeline
.\.venv\Scripts\python.exe main.py clean-book "Book Title" --inspect-text
```

## Artifacts

Main local artifacts:

- `data/calibre_library_manifest.json`: imported Calibre book metadata, EPUB
  paths, hashes, and viewer annotation keys.
- `data/books/<calibre_book_id>/timeline.json`: prepared spine summary,
  anchors, and book identity/drift fields.
- `data/books/<calibre_book_id>/source_units.json`: paragraph-like text-block
  offsets and previews used to trace anchors back to EPUB text. The filename
  and internal key remain `source_units` for compatibility; user-facing docs
  treat these records as text blocks.
- `data/books/<calibre_book_id>/inspect_text.json`: optional full-text debug
  sidecar written only with `prepare-book --debug-text`.
- `data/books/<calibre_book_id>/query_records.jsonl`: manual query handoff
  records containing book identity, text-block span provenance, capped excerpt,
  query text, handoff target, and review status.
- `data/cfi_fixtures/*.json`: captured live CFI resolver fixtures.

The query handoff shape is defined in [docs/SCHEMAS.md](docs/SCHEMAS.md). The
task queue is in [docs/TASKS.md](docs/TASKS.md).
