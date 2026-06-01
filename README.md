# Calibre-Native Adaptive Ambience

This repo is being rebuilt around a Calibre-native reading-position spine.
The current active implementation imports a Calibre library, maps EPUB paths to
Calibre E-book Viewer annotation files, prepares source-aligned anchors and
deterministic regions, and inspects live EPUB CFI positions against that
timeline.

The previous generic EPUB/chunk/round-robin prototype has been moved to
`old/prototype/`.

## Current Commands

```powershell
python main.py import-calibre "C:\Users\<you>\Calibre Library"
python main.py list-books --epub-only
python main.py prepare-book "Book Title" --regions
python main.py inspect-book "Book Title" --anchors --regions
python main.py inspect-live --resolve-cfi
python main.py inspect-book "Book Title" --live --resolve-cfi --anchors --regions
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

## Docs

See:

- [Usage](docs/USAGE.md) for setup, commands, and validation.
- [Architecture](docs/ARCHITECTURE.md) for the broad product design.
- [Region design](docs/DESIGN.md) for the current segmentation design.
- [Preparation cache shape](docs/PREP_CACHE_DESIGN.md) for the next cache cleanup.
- [Next slice](docs/NEXT.md) for the active implementation handoff.
