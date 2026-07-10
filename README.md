# bkpj2

bkpj2 is the book-to-audio-query side of a local reading-audio workflow.

The current code imports a Calibre library, reads live Calibre E-book Viewer
positions, resolves EPUB CFIs through Calibre helpers, prepares text-block
timelines, inspects how a live CFI maps back to book text, exports compact
manual audio-intent query records, exports deterministic span placeholders for
later query authoring, generates local audio-intent query records from those
placeholders, shells out to `music-retrieval-lab` for retrieval packages, and
builds playback-plan artifacts from stored retrieval runs.

Retrieval, embedding indexes, candidate ranking, review HTML, and audio output
are owned by `music-retrieval-lab` or later artifacts. bkpj2's near-term role is
to produce book/span/query provenance, invoke the lab as an external command,
store retrieval-run records that point to lab packages, materialize the top
candidate per span by default, and derive small playback plans that keep chunk
evidence separate from normalized master/runtime audio paths.

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
.\.venv\Scripts\python.exe main.py generate-queries --input .\query_records.needs_query.jsonl --out .\query_records.generated.jsonl --provider ollama --model qwen3:4b-instruct --ollama-url http://localhost:11434 --limit 3 --overwrite
.\.venv\Scripts\python.exe main.py generate-queries --input .\query_records.needs_query.jsonl --out .\query_records.generated.scene.jsonl --provider ollama --model qwen3:4b-instruct --prompt-version audio_intent_scene_v1 --limit 5 --overwrite
.\.venv\Scripts\python.exe main.py generate-queries --input .\query_records.needs_query.jsonl --out .\query_records.generated.sparse.jsonl --provider ollama --model qwen3:4b-instruct --prompt-version audio_intent_sparse_v1 --limit 5 --overwrite
.\.venv\Scripts\python.exe main.py generate-queries --input .\query_records.needs_query.jsonl --out .\query_records.generated.legacy.jsonl --provider local-command --command C:\path\to\local-query-generator.exe --prompt-version audio_intent_v1
.\.venv\Scripts\python.exe main.py generate-queries --input .\query_records.needs_query.jsonl --out .\query_records.generated.jsonl --provider fake
```

`audio_intent_scene_v1` is the default prompt. It frames the output as an
audio-embedding search phrase. Use `audio_intent_v1` for the shorter no-menu scene prompt, and
`audio_intent_sparse_v1` for the checklist-heavy skill-style prompt.

The Ollama provider expects the operator to install Ollama separately, run the
local service, and pull the requested model first. bkpj2 does not install
Ollama or download models.

Run the standard book-to-audio artifact pipeline in one command:

```powershell
.\.venv\Scripts\python.exe main.py run-book-audio-pipeline 5 --run-id run_ollama_001 --provider ollama --model qwen3:4b-instruct --retrieval-profile local_fused_v1 --profile-config C:\dev\music-retrieval-lab\configs\retrieval_profile.yml --lab-project C:\dev\music-retrieval-lab --lab-python C:\dev\music-retrieval-lab\.venv\Scripts\python.exe --max-spans 3 --verbose
.\.venv\Scripts\python.exe main.py run-book-audio-pipeline 5 --run-id run_ollama_001 --provider ollama --model qwen3:4b-instruct --retrieval-profile local_fused_v1 --profile-config C:\dev\music-retrieval-lab\configs\retrieval_profile.yml --lab-project C:\dev\music-retrieval-lab --lab-python C:\dev\music-retrieval-lab\.venv\Scripts\python.exe --reuse-retrieval-run
```

The pipeline prepares or reuses `timeline.json` and `source_units.json`, writes
or reuses `query_records.needs_query.jsonl`, writes or reuses a
provider/model-named generated query JSONL, runs `retrieve-audio`, builds
`playback_plan.json`, and prints a ready-to-run `follow-live-audio` command.
It reuses existing JSON artifacts by default and refuses to overwrite an
existing retrieval run directory unless `--reuse-retrieval-run` is provided.
Use the individual commands below when you need to inspect or rerun one stage.

Run a lab retrieval package and write a bkpj2 retrieval-run pointer:

```powershell
.\.venv\Scripts\python.exe main.py retrieve-audio --query-records .\query_records.generated.jsonl --retrieval-profile local_fused_v1 --profile-config C:\dev\music-retrieval-lab\configs\retrieval_profile.yml --lab-project C:\dev\music-retrieval-lab --lab-python C:\dev\music-retrieval-lab\.venv\Scripts\python.exe --candidate-strategy top_ranked --out .\data\books\5\retrieval_runs\run_001
.\.venv\Scripts\python.exe main.py retrieve-audio --query-records .\query_records.generated.jsonl --retrieval-profile local_fused_v1 --profile-config C:\dev\music-retrieval-lab\configs\retrieval_profile.yml --lab-project C:\dev\music-retrieval-lab --lab-python C:\dev\music-retrieval-lab\.venv\Scripts\python.exe --candidate-strategy top_ranked --out .\data\books\5\retrieval_runs\run_002 --verbose
```

List retrieval runs for an imported book and refresh the small per-book index:

```powershell
.\.venv\Scripts\python.exe main.py list-retrieval-runs "Book Title"
.\.venv\Scripts\python.exe main.py list-retrieval-runs "Book Title" --verbose
.\.venv\Scripts\python.exe main.py list-retrieval-runs "Book Title" --json
```

Build or inspect a playback plan for one retrieval run without starting audio:

```powershell
.\.venv\Scripts\python.exe main.py build-playback-plan .\data\books\5\retrieval_runs\run_001\retrieval_run.json
.\.venv\Scripts\python.exe main.py build-playback-plan .\data\books\5\retrieval_runs\run_001\retrieval_run.json --verbose
.\.venv\Scripts\python.exe main.py build-playback-plan .\data\books\5\retrieval_runs\run_001\retrieval_run.json --json
```

Play a short fixed-dwell preview from a playback plan:

```powershell
.\.venv\Scripts\python.exe main.py play-preview .\data\books\5\retrieval_runs\run_001\playback_plan.json --max-spans 5 --dwell-seconds 20 --crossfade-seconds 4
.\.venv\Scripts\python.exe main.py play-preview .\data\books\5\retrieval_runs\run_001\playback_plan.json --dry-run --max-spans 3
```

Follow the open Calibre viewer position with a playback plan:

```powershell
.\.venv\Scripts\python.exe main.py follow-live-audio 11 .\data\books\11\retrieval_runs\run_ollama_001\playback_plan.json --dry-run --once
.\.venv\Scripts\python.exe main.py follow-live-audio 11 .\data\books\11\retrieval_runs\run_ollama_001\playback_plan.json --dry-run --once --verbose
.\.venv\Scripts\python.exe main.py follow-live-audio 11 .\data\books\11\retrieval_runs\run_ollama_001\playback_plan.json --min-stable-polls 2 --crossfade-seconds 4 --gain 0.8
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
- `data/books/<calibre_book_id>/retrieval_runs/retrieval_run_index.json`: a
  scan-derived run index with package paths, missing-file checks, and top
  candidate coverage without duplicating lab candidate rankings.
- `data/books/<calibre_book_id>/retrieval_runs/<run_id>/playback_plan.json`: a
  derived per-span playback plan that points playable entries at normalized
  master/runtime audio files and keeps chunk paths as retrieval evidence only.
  New plans also carry the source span offsets needed for live CFI lookup.
  When explicit master paths are absent, the current local fallback derives
  `sounds_v1` masters from chunk paths under
  `...\sounds_v1\corpus\<backend>\chunks\`.
- `data/cfi_fixtures/*.json`: captured live CFI resolver fixtures.

The query handoff shape is defined in [docs/SCHEMAS.md](docs/SCHEMAS.md). The
task queue is in [docs/TASKS.md](docs/TASKS.md).
