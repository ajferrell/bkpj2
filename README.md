# bkpj2

bkpj2 is the book-to-audio-query side of a local reading-audio workflow.

The current code imports a Calibre library, reads live Calibre E-book Viewer
positions, resolves EPUB CFIs through Calibre helpers, prepares text-block
timelines, inspects how a live CFI maps back to book text, exports compact
manual audio-intent query records, exports deterministic span placeholders for
later query authoring, generates local audio-intent query records from those
placeholders, and shells out to `music-retrieval-lab` for retrieval packages.

Retrieval, embedding indexes, candidate ranking, review HTML, and playback are
owned by `music-retrieval-lab` or later artifacts. bkpj2's near-term role is to
produce book/span/query provenance, invoke the lab as an external command, and
store retrieval-run records that point to lab packages and materialize the top
candidate per span by default.

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

Export deterministic query-span candidates for manual query authoring:

```powershell
.\.venv\Scripts\python.exe main.py export-batch-spans "Book Title" --target-words 800 --max-spans 20
```

Generate local audio-intent query records from `needs_query` placeholders:

```powershell
.\.venv\Scripts\python.exe main.py generate-queries --input .\query_records.needs_query.jsonl --out .\query_records.generated.jsonl --provider local-command --command C:\path\to\local-query-generator.exe --prompt-version audio_intent_v1
.\.venv\Scripts\python.exe main.py generate-queries --input .\query_records.needs_query.jsonl --out .\query_records.generated.jsonl --provider fake
```

Run a lab retrieval package and write a bkpj2 retrieval-run pointer:

```powershell
.\.venv\Scripts\python.exe main.py retrieve-audio --query-records .\query_records.generated.jsonl --retrieval-profile local_fused_v1 --profile-config C:\dev\music-retrieval-lab\configs\retrieval_profile.yml --lab-project C:\dev\music-retrieval-lab --lab-python C:\dev\music-retrieval-lab\.venv\Scripts\python.exe --candidate-strategy top_ranked --out .\data\books\5\retrieval_runs\run_001
.\.venv\Scripts\python.exe main.py retrieve-audio --query-records .\query_records.generated.jsonl --retrieval-profile local_fused_v1 --profile-config C:\dev\music-retrieval-lab\configs\retrieval_profile.yml --lab-project C:\dev\music-retrieval-lab --lab-python C:\dev\music-retrieval-lab\.venv\Scripts\python.exe --candidate-strategy top_ranked --out .\data\books\5\retrieval_runs\run_002 --verbose
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
- `data/books/<calibre_book_id>/query_records.jsonl`: query handoff records
  containing book identity, text-block span provenance, capped excerpt, query
  text or `needs_query` placeholders, handoff target, and review status. These
  records do not store retrieval candidates, candidate rankings, selected
  assets, runtime state, or playback policy.
- `query_records.generated.jsonl`: generated query records written from
  `needs_query` inputs. Generation cache and failure sidecars are written next
  to the output unless explicit paths are provided.
- `data/books/<calibre_book_id>/retrieval_runs/...`: retrieval-run records
  with package pointers, captured lab stdout/stderr, candidate strategy, top
  candidate per span, and `music-retrieval-lab` package outputs such as
  `retrieval_results.jsonl` and `retrieval_summary.json`.
- `data/cfi_fixtures/*.json`: captured live CFI resolver fixtures.

The query handoff shape is defined in [docs/SCHEMAS.md](docs/SCHEMAS.md). The
task queue is in [docs/TASKS.md](docs/TASKS.md).
