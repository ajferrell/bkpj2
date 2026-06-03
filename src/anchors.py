"""Book anchor preparation and lookup."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .atmosphere import build_atmosphere_extension
from .audio_planner import build_audio_intents, load_asset_catalog
from .cfi_fixtures import file_sha256


TIMELINE_SCHEMA_VERSION = 5
REGION_REVIEW_SCHEMA_VERSION = 1
SOURCE_UNITS_SCHEMA_VERSION = 1
REGION_DIAGNOSTICS_SCHEMA_VERSION = 1

DEFAULT_REGION_CONFIG = {
    "min_anchors": 3,
    "target_anchors": 8,
    "max_anchors": 18,
    "boundary_threshold": 0.65,
}

REGION_PROFILES = {
    "conservative": {
        "min_anchors": 5,
        "target_anchors": 10,
        "max_anchors": 24,
        "boundary_threshold": 0.85,
    },
    "normal": DEFAULT_REGION_CONFIG,
    "sensitive": {
        "min_anchors": 2,
        "target_anchors": 5,
        "max_anchors": 12,
        "boundary_threshold": 0.45,
    },
}

KEYWORD_GROUPS = {
    "weather": {
        "rain",
        "storm",
        "thunder",
        "lightning",
        "wind",
        "snow",
        "fog",
    },
    "setting": {
        "forest",
        "wood",
        "woods",
        "city",
        "street",
        "road",
        "room",
        "house",
        "castle",
        "river",
        "sea",
        "cave",
    },
    "combat": {
        "fight",
        "battle",
        "sword",
        "blood",
        "gun",
        "shot",
        "attack",
        "wound",
    },
}


def timeline_dir(data_dir: str | Path, calibre_book_id: int | str) -> Path:
    return Path(data_dir) / "books" / str(calibre_book_id)


def timeline_path(data_dir: str | Path, calibre_book_id: int | str) -> Path:
    return timeline_dir(data_dir, calibre_book_id) / "timeline.json"


def inspect_text_path(data_dir: str | Path, calibre_book_id: int | str) -> Path:
    return timeline_dir(data_dir, calibre_book_id) / "inspect_text.json"


def source_units_path(data_dir: str | Path, calibre_book_id: int | str) -> Path:
    return timeline_dir(data_dir, calibre_book_id) / "source_units.json"


def region_diagnostics_path(data_dir: str | Path, calibre_book_id: int | str) -> Path:
    return timeline_dir(data_dir, calibre_book_id) / "region_diagnostics.json"


def region_review_path(data_dir: str | Path, calibre_book_id: int | str) -> Path:
    return timeline_dir(data_dir, calibre_book_id) / "region_review.json"


def prepare_book_timeline(
    book: dict[str, Any],
    data_dir: str | Path = "data",
    target_words: int = 350,
    min_words: int = 180,
    regions: bool = False,
    atmosphere: bool = False,
    audio_intents: bool = False,
    asset_catalog_path: str | Path | None = None,
    debug_text: bool = False,
    region_config: Optional[dict[str, Any]] = None,
    region_profile: str = "normal",
    region_review: Optional[dict[str, Any]] = None,
    spine_extractor= None,
) -> Path:
    epub_path = book.get("preferred_epub_path")
    if not epub_path:
        raise ValueError(f"Book has no EPUB path: {book.get('title')}")

    extractor = spine_extractor or extract_spine_texts_with_calibre
    spine = extractor(epub_path)
    source_units = extract_source_units(spine)
    anchors = build_anchors(source_units, target_words=target_words, min_words=min_words)
    features = extract_anchor_features(anchors)
    config = resolve_region_config(region_profile, region_config)
    timeline = {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "builder": {
            "name": "anchor_builder",
            "target_words": target_words,
            "min_words": min_words,
            "regions": bool(regions),
            "atmosphere": bool(atmosphere),
            "audio_intents": bool(audio_intents),
            "region_config": config,
        },
        "book": {
            "calibre_book_id": book.get("calibre_book_id"),
            "calibre_uuid": book.get("calibre_uuid"),
            "title": book.get("title"),
            "authors": book.get("authors") or [],
            "epub_path": epub_path,
            "epub_hash": file_sha256(epub_path),
            "annots_key": book.get("annots_key"),
        },
        "spine": [
            {
                "spine_index": item["spine_index"],
                "href": item["href"],
                "text_len": item.get("text_len", len(item.get("text", ""))),
            }
            for item in spine
        ],
        "anchors": anchors,
        "features": features,
    }
    boundary_candidates: list[dict[str, Any]] = []
    if regions:
        provider = BoundaryProvider()
        boundary_candidates = provider.score_boundaries(source_units, anchors, features, config)
        if region_review:
            apply_region_review_marks(boundary_candidates, region_review)
        result = build_regions(anchors, boundary_candidates, config)
        timeline["regions"] = result["regions"]
        region_diagnostics = {
            "schema_version": REGION_DIAGNOSTICS_SCHEMA_VERSION,
            "profile": region_profile,
            "config": config,
            "boundary_candidates": result["boundary_candidates"],
            "selected_boundaries": [
                boundary_summary(candidate)
                for candidate in result["boundary_candidates"]
                if candidate.get("selected")
            ],
        }
        if region_review:
            region_diagnostics["review"] = evaluate_region_review(
                result["boundary_candidates"],
                region_review,
            )
        timeline["region_diagnostics"] = region_diagnostics
    elif atmosphere:
        raise ValueError("--atmosphere requires --regions")

    if atmosphere:
        timeline["atmosphere"] = build_atmosphere_extension(timeline, anchors)
    if audio_intents:
        if not regions:
            raise ValueError("--audio-intents requires --regions")
        catalog = load_asset_catalog(asset_catalog_path)
        timeline["audio_intents"] = build_audio_intents(timeline, catalog)

    out_dir = timeline_dir(data_dir, book["calibre_book_id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "timeline.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(slim_timeline(timeline), f, indent=2, ensure_ascii=False)
    with open(source_units_path(data_dir, book["calibre_book_id"]), "w", encoding="utf-8") as f:
        json.dump(source_units_sidecar(timeline, source_units), f, indent=2, ensure_ascii=False)
    if regions:
        with open(region_diagnostics_path(data_dir, book["calibre_book_id"]), "w", encoding="utf-8") as f:
            json.dump(timeline["region_diagnostics"], f, indent=2, ensure_ascii=False)
    else:
        diagnostics_path = region_diagnostics_path(data_dir, book["calibre_book_id"])
        if diagnostics_path.exists():
            remove_json_sidecar(diagnostics_path, REGION_DIAGNOSTICS_SCHEMA_VERSION)
    sidecar_path = inspect_text_path(data_dir, book["calibre_book_id"])
    if debug_text:
        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(
                inspect_text_sidecar(source_units, anchors, boundary_candidates),
                f,
                indent=2,
                ensure_ascii=False,
            )
    elif sidecar_path.exists():
        sidecar_path.unlink()
    return path


def load_timeline(data_dir: str | Path, calibre_book_id: int | str) -> dict[str, Any]:
    path = timeline_path(data_dir, calibre_book_id)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_timeline_with_sidecars(data_dir: str | Path, calibre_book_id: int | str) -> dict[str, Any]:
    timeline = load_timeline(data_dir, calibre_book_id)
    source_path = source_units_path(data_dir, calibre_book_id)
    if "source_units" not in timeline and source_path.exists():
        with open(source_path, "r", encoding="utf-8") as f:
            timeline["source_units"] = json.load(f).get("source_units", [])
    diagnostics_path = region_diagnostics_path(data_dir, calibre_book_id)
    if "region_diagnostics" not in timeline and diagnostics_path.exists():
        with open(diagnostics_path, "r", encoding="utf-8") as f:
            diagnostics = json.load(f)
        if not diagnostics.get("removed"):
            timeline["region_diagnostics"] = diagnostics
    return timeline


def resolve_region_config(profile: str = "normal", overrides: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    if profile not in REGION_PROFILES:
        choices = ", ".join(sorted(REGION_PROFILES))
        raise ValueError(f"Unknown region profile {profile!r}; choose one of: {choices}")
    return {**REGION_PROFILES[profile], **(overrides or {})}


def load_region_review(data_dir: str | Path, calibre_book_id: int | str) -> dict[str, Any]:
    path = region_review_path(data_dir, calibre_book_id)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_region_review_artifact(
    book: dict[str, Any],
    timeline: dict[str, Any],
    data_dir: str | Path = "data",
    overwrite: bool = False,
) -> Path:
    path = region_review_path(data_dir, book["calibre_book_id"])
    if path.exists() and not overwrite:
        raise FileExistsError(f"Region review already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(build_region_review_artifact(book, timeline), f, indent=2, ensure_ascii=False)
    return path


def build_region_review_artifact(book: dict[str, Any], timeline: dict[str, Any]) -> dict[str, Any]:
    diagnostics = timeline.get("region_diagnostics", {})
    return {
        "schema_version": REGION_REVIEW_SCHEMA_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "book": {
            "calibre_book_id": book.get("calibre_book_id"),
            "calibre_uuid": book.get("calibre_uuid"),
            "title": book.get("title"),
            "epub_path": book.get("preferred_epub_path") or timeline.get("book", {}).get("epub_path"),
            "epub_hash": timeline.get("book", {}).get("epub_hash"),
            "annots_key": book.get("annots_key") or timeline.get("book", {}).get("annots_key"),
        },
        "timeline": {
            "schema_version": timeline.get("schema_version"),
            "created_at": timeline.get("created_at"),
            "region_profile": diagnostics.get("profile"),
            "region_count": len(timeline.get("regions", [])),
            "anchor_count": len(timeline.get("anchors", [])),
            "atmosphere_schema_version": timeline.get("atmosphere", {}).get("schema_version"),
        },
        "expected_boundaries": [
            {
                "anchor_boundary": candidate["anchor_boundary"],
                "source_unit_boundary": candidate.get("source_unit_boundary"),
                "score": candidate.get("score"),
                "reasons": candidate.get("reasons", []),
                "selected": candidate.get("selected", False),
                "review_expected": None,
                "noisy_false_positive": False,
                "note": "",
            }
            for candidate in diagnostics.get("boundary_candidates", [])
            if candidate.get("anchor_boundary", 0) > 0
        ],
        "regions": [
            {
                "region_id": region["region_id"],
                "anchor_start": region["anchor_start"],
                "anchor_end": region["anchor_end"],
                "boundary_in": region.get("boundary_in"),
                "boundary_out": region.get("boundary_out"),
                "preview": region.get("preview"),
                "atmosphere": atmosphere_by_region_id(timeline).get(region["region_id"]),
                "review_labels": {
                    "correct_labels": [],
                    "missing_labels": [],
                    "bad_labels": [],
                    "evidence_quality": None,
                    "audio_change_useful": None,
                    "note": "",
                },
            }
            for region in timeline.get("regions", [])
        ],
    }


def atmosphere_by_region_id(timeline: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(item["region_id"]): item
        for item in timeline.get("atmosphere", {}).get("region_labels", [])
    }


def apply_region_review_marks(
    boundary_candidates: list[dict[str, Any]],
    review: dict[str, Any],
) -> None:
    marks = review_marks_by_boundary(review)
    for candidate in boundary_candidates:
        mark = marks.get(candidate.get("anchor_boundary"))
        if not mark:
            continue
        if mark.get("review_expected") is True:
            candidate["score"] = round(max(float(candidate.get("score", 0)), 1.0), 3)
            if "manual_expected" not in candidate["reasons"]:
                candidate["reasons"].append("manual_expected")
        if mark.get("noisy_false_positive") is True:
            candidate["score"] = 0.0
            candidate["rejected_reason"] = "manual_noisy_false_positive"
            if "manual_noisy_false_positive" not in candidate["reasons"]:
                candidate["reasons"].append("manual_noisy_false_positive")


def evaluate_region_review(
    boundary_candidates: list[dict[str, Any]],
    review: dict[str, Any],
) -> dict[str, Any]:
    selected_edges = {
        candidate["anchor_boundary"]
        for candidate in boundary_candidates
        if candidate.get("selected")
    }
    expected_edges = {
        boundary
        for boundary, mark in review_marks_by_boundary(review).items()
        if mark.get("review_expected") is True
    }
    noisy_edges = {
        boundary
        for boundary, mark in review_marks_by_boundary(review).items()
        if mark.get("noisy_false_positive") is True
    }
    return {
        "expected_count": len(expected_edges),
        "expected_selected": sorted(expected_edges & selected_edges),
        "expected_missed": sorted(expected_edges - selected_edges),
        "noisy_false_positive_count": len(noisy_edges),
        "noisy_selected": sorted(noisy_edges & selected_edges),
        "noisy_rejected": sorted(noisy_edges - selected_edges),
    }


def review_marks_by_boundary(review: dict[str, Any]) -> dict[int, dict[str, Any]]:
    marks: dict[int, dict[str, Any]] = {}
    for item in review.get("expected_boundaries", []):
        boundary = item.get("anchor_boundary")
        if boundary is not None:
            marks[int(boundary)] = item
    return marks


def timeline_drift_warnings(book: dict[str, Any], timeline: dict[str, Any]) -> list[str]:
    """Return warnings when the manifest/book identity diverges from a timeline."""
    warnings: list[str] = []
    timeline_book = timeline.get("book", {})
    manifest_epub = book.get("preferred_epub_path")
    timeline_epub = timeline_book.get("epub_path")
    if manifest_epub and timeline_epub and str(manifest_epub) != str(timeline_epub):
        warnings.append(f"epub_path_changed manifest={manifest_epub} timeline={timeline_epub}")
    if manifest_epub and Path(manifest_epub).exists():
        current_hash = file_sha256(manifest_epub)
        timeline_hash = timeline_book.get("epub_hash")
        if timeline_hash and current_hash != timeline_hash:
            warnings.append(f"epub_hash_changed timeline={timeline_hash} actual={current_hash}")
    elif manifest_epub:
        warnings.append(f"epub_missing path={manifest_epub}")
    manifest_key = book.get("annots_key")
    timeline_key = timeline_book.get("annots_key")
    if manifest_key and timeline_key and manifest_key != timeline_key:
        warnings.append(f"annots_key_changed manifest={manifest_key} timeline={timeline_key}")
    return warnings


def inspect_position_chain(
    book: dict[str, Any],
    timeline: dict[str, Any] | None = None,
    live: Any | None = None,
    resolved: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one structured coordinate chain view for CLI inspection."""
    chain: dict[str, Any] = {
        "book": {
            "calibre_book_id": book.get("calibre_book_id"),
            "calibre_uuid": book.get("calibre_uuid"),
            "title": book.get("title"),
            "authors": book.get("authors") or [],
            "epub_path": book.get("preferred_epub_path"),
            "annots_key": book.get("annots_key"),
        },
        "timeline": None,
        "live": None,
        "resolved": None,
        "anchor": None,
        "region": None,
        "warnings": [],
    }
    if timeline is not None:
        chain["timeline"] = {
            "schema_version": timeline.get("schema_version"),
            "created_at": timeline.get("created_at"),
            "epub_path": timeline.get("book", {}).get("epub_path"),
            "epub_hash": timeline.get("book", {}).get("epub_hash"),
            "spine_count": len(timeline.get("spine", [])),
            "source_unit_count": len(timeline.get("source_units", [])),
            "anchor_count": len(timeline.get("anchors", [])),
            "region_count": len(timeline.get("regions", [])),
        }
        chain["warnings"].extend(timeline_drift_warnings(book, timeline))
    if live is not None:
        chain["live"] = {
            "annots_path": getattr(live, "annots_path", None),
            "annots_key": getattr(live, "annots_key", None),
            "epubcfi": getattr(live, "epubcfi", None),
            "timestamp": getattr(live, "timestamp", None),
        }
        if book.get("annots_key") and getattr(live, "annots_key", None) != book.get("annots_key"):
            chain["warnings"].append(
                f"live_annots_key_mismatch book={book.get('annots_key')} live={getattr(live, 'annots_key', None)}"
            )
    if resolved is not None:
        chain["resolved"] = {
            key: resolved.get(key)
            for key in ("spine_index", "href", "local_char_offset", "spine_text_len", "text_preview", "error")
            if key in resolved
        }
    if timeline is not None and resolved and not resolved.get("error"):
        anchor = find_anchor_for_position(
            timeline,
            spine_index=resolved["spine_index"],
            local_char_offset=resolved["local_char_offset"],
        )
        if anchor is not None:
            pos = anchor["position"]
            text = anchor["text"]
            chain["anchor"] = {
                "anchor_id": anchor["anchor_id"],
                "spine_index": pos["spine_index"],
                "href": pos["href"],
                "start_local_offset": pos["start_local_offset"],
                "end_local_offset": pos["end_local_offset"],
                "word_count": text.get("word_count"),
                "preview": text.get("preview"),
            }
            region = find_region_for_anchor(timeline, anchor["anchor_id"])
            if region is not None:
                chain["region"] = {
                    "region_id": region["region_id"],
                    "anchor_start": region["anchor_start"],
                    "anchor_end": region["anchor_end"],
                    "active_anchor": anchor["anchor_id"],
                    "boundary_in": region.get("boundary_in"),
                    "boundary_out": region.get("boundary_out"),
                    "preview": region.get("preview"),
                }
        else:
            chain["warnings"].append("anchor_not_found")
    return chain


def remove_timeline(data_dir: str | Path, calibre_book_id: int | str) -> bool:
    path = timeline_path(data_dir, calibre_book_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def remove_inspect_text(data_dir: str | Path, calibre_book_id: int | str) -> bool:
    path = inspect_text_path(data_dir, calibre_book_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def remove_regions_from_timeline(data_dir: str | Path, calibre_book_id: int | str) -> bool:
    path = timeline_path(data_dir, calibre_book_id)
    if not path.exists():
        return False
    timeline = load_timeline(data_dir, calibre_book_id)
    changed = False
    if "regions" in timeline:
        timeline.pop("regions")
        changed = True
    if "region_diagnostics" in timeline:
        timeline.pop("region_diagnostics")
        changed = True
    if "atmosphere" in timeline:
        timeline.pop("atmosphere")
        changed = True
    if "audio_intents" in timeline:
        timeline.pop("audio_intents")
        changed = True
    builder = timeline.setdefault("builder", {})
    if builder.get("regions") is not False:
        builder["regions"] = False
        changed = True
    if builder.get("atmosphere") is not False:
        builder["atmosphere"] = False
        changed = True
    if builder.get("audio_intents") is not False:
        builder["audio_intents"] = False
        changed = True
    if changed:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(timeline, f, indent=2, ensure_ascii=False)
    diagnostics_path = region_diagnostics_path(data_dir, calibre_book_id)
    if diagnostics_path.exists():
        remove_json_sidecar(diagnostics_path, REGION_DIAGNOSTICS_SCHEMA_VERSION)
        changed = True
    return changed


def remove_json_sidecar(path: Path, schema_version: int) -> None:
    try:
        path.unlink()
    except PermissionError:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"schema_version": schema_version, "removed": True}, f, indent=2)


def extract_spine_texts_with_calibre(epub_path: str | Path) -> list[dict[str, Any]]:
    helper = Path(__file__).parent / "calibre" / "anchor_helper.py"
    cmd = [
        "calibre-debug",
        "--exec-file",
        str(helper),
        "--",
        str(epub_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError as e:
        raise RuntimeError("calibre-debug not found on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("calibre-debug anchor extraction timed out") from e

    if not result.stdout.strip():
        raise RuntimeError(f"calibre-debug produced no anchor JSON. stderr={result.stderr[-1000:]}")
    try:
        data = json.loads(result.stdout.strip())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Could not parse anchor JSON: {result.stdout[:1000]}") from e
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data.get("spine", [])


def build_anchors_from_spine(
    spine: list[dict[str, Any]],
    target_words: int = 350,
    min_words: int = 180,
) -> list[dict[str, Any]]:
    return build_anchors(extract_source_units(spine), target_words=target_words, min_words=min_words)


def extract_source_units(spine: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_units: list[dict[str, Any]] = []
    unit_id = 0
    for item in spine:
        text = item.get("text") or ""
        for para in paragraph_spans(text):
            source_units.append({
                "unit_id": unit_id,
                "spine_index": item["spine_index"],
                "href": item["href"],
                "start_local_offset": para["start"],
                "end_local_offset": para["end"],
                "kind": "paragraph",
                "word_count": para["word_count"],
                "preview": preview_text(para["text"]),
                "_text": para["text"],
            })
            unit_id += 1
    return source_units


def serialize_source_unit(unit: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in unit.items() if not key.startswith("_")}


def slim_timeline(timeline: dict[str, Any]) -> dict[str, Any]:
    slim = dict(timeline)
    slim.pop("source_units", None)
    slim.pop("region_diagnostics", None)
    slim["anchors"] = [
        slim_anchor(anchor)
        for anchor in timeline.get("anchors", [])
    ]
    return slim


def source_units_sidecar(timeline: dict[str, Any], source_units: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": SOURCE_UNITS_SCHEMA_VERSION,
        "timeline": {
            "schema_version": timeline.get("schema_version"),
            "created_at": timeline.get("created_at"),
            "calibre_book_id": timeline.get("book", {}).get("calibre_book_id"),
        },
        "source_units": [serialize_source_unit(unit) for unit in source_units],
    }


def slim_anchor(anchor: dict[str, Any]) -> dict[str, Any]:
    slim = dict(anchor)
    text = dict(anchor.get("text", {}))
    text.pop("plain", None)
    slim["text"] = text
    return slim


def inspect_text_sidecar(
    source_units: list[dict[str, Any]],
    anchors: list[dict[str, Any]],
    boundary_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    sidecar: dict[str, Any] = {
        "anchors": {
            str(anchor["anchor_id"]): {
                "plain": anchor.get("text", {}).get("plain", ""),
            }
            for anchor in anchors
        },
        "source_units": {
            str(unit["unit_id"]): {
                "text": unit.get("_text", ""),
            }
            for unit in source_units
        },
    }
    if boundary_candidates:
        sidecar["boundary_candidates"] = boundary_candidates
    return sidecar


def build_anchors(
    source_units: list[dict[str, Any]],
    target_words: int = 350,
    min_words: int = 180,
) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    anchor_id = 0
    by_spine: list[list[dict[str, Any]]] = []
    for unit in source_units:
        if not by_spine or by_spine[-1][0]["spine_index"] != unit["spine_index"]:
            by_spine.append([])
        by_spine[-1].append(unit)

    for units in by_spine:
        current: list[dict[str, Any]] = []
        current_words = 0

        for unit in units:
            if current and current_words >= min_words and current_words + unit["word_count"] > target_words:
                anchors.append(make_anchor(anchor_id, current))
                anchor_id += 1
                current = []
                current_words = 0

            current.append(unit)
            current_words += unit["word_count"]

        if current:
            if anchors and anchors[-1]["position"]["spine_index"] == current[0]["spine_index"] and current_words < min_words:
                merge_anchor_tail(anchors[-1], current)
            else:
                anchors.append(make_anchor(anchor_id, current))
                anchor_id += 1
    return anchors


def paragraph_spans(text: str) -> list[dict[str, Any]]:
    spans = []
    for match in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|$)", text):
        raw = match.group(0)
        normalized = re.sub(r"\s+", " ", raw).strip()
        if not normalized:
            continue
        spans.append({
            "start": match.start(),
            "end": match.end(),
            "text": normalized,
            "word_count": len(normalized.split()),
        })
    return spans


def make_anchor(anchor_id: int, source_units: list[dict[str, Any]]) -> dict[str, Any]:
    start = source_units[0]["start_local_offset"]
    end = source_units[-1]["end_local_offset"]
    plain = "\n\n".join(p["_text"] for p in source_units)
    return {
        "anchor_id": anchor_id,
        "source_unit_start": source_units[0]["unit_id"],
        "source_unit_end": source_units[-1]["unit_id"] + 1,
        "position": {
            "spine_index": source_units[0]["spine_index"],
            "href": source_units[0]["href"],
            "start_local_offset": start,
            "end_local_offset": end,
        },
        "text": {
            "plain": plain,
            "preview": preview_text(plain),
            "word_count": sum(p["word_count"] for p in source_units),
            "char_count": len(plain),
            "paragraph_count": len(source_units),
        },
    }


def merge_anchor_tail(anchor: dict[str, Any], source_units: list[dict[str, Any]]) -> None:
    plain = anchor["text"]["plain"] + "\n\n" + "\n\n".join(p["_text"] for p in source_units)
    anchor["source_unit_end"] = source_units[-1]["unit_id"] + 1
    anchor["position"]["end_local_offset"] = source_units[-1]["end_local_offset"]
    anchor["text"]["plain"] = plain
    anchor["text"]["preview"] = preview_text(plain)
    anchor["text"]["word_count"] += sum(p["word_count"] for p in source_units)
    anchor["text"]["char_count"] = len(plain)
    anchor["text"]["paragraph_count"] += len(source_units)


def extract_anchor_features(anchors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [anchor_feature(anchor) for anchor in anchors]


def anchor_feature(anchor: dict[str, Any]) -> dict[str, Any]:
    text = anchor.get("text", {}).get("plain", "")
    words = re.findall(r"[A-Za-z']+", text.casefold())
    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    quote_chars = sum(text.count(mark) for mark in ['"', "'", "\u201c", "\u201d", "\u2018", "\u2019"])
    punctuation = sum(1 for char in text if char in ",.;:!?")
    keyword_hits = {
        group: sorted({word for word in words if word in keywords})
        for group, keywords in KEYWORD_GROUPS.items()
    }
    return {
        "anchor_id": anchor["anchor_id"],
        "dialogue_ratio": round(min(1.0, quote_chars / max(1, len(text))), 4),
        "paragraph_count": anchor.get("text", {}).get("paragraph_count", 0),
        "avg_sentence_words": round(len(words) / max(1, len(sentences)), 2),
        "punctuation_density": round(punctuation / max(1, len(text)), 4),
        "keyword_hits": keyword_hits,
    }


class BoundaryProvider:
    def score_boundaries(
        self,
        source_units: list[dict[str, Any]],
        anchors: list[dict[str, Any]],
        features: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        candidates = []
        for boundary_unit in range(1, len(source_units)):
            prev_unit = source_units[boundary_unit - 1]
            next_unit = source_units[boundary_unit]
            prev_anchor = find_anchor_covering_source_unit(anchors, prev_unit["unit_id"])
            next_anchor = find_anchor_covering_source_unit(anchors, next_unit["unit_id"])
            if not prev_anchor or not next_anchor:
                continue

            anchor_boundary, snap = snap_source_boundary_to_anchor(anchors, boundary_unit)
            prev_feature = features[prev_anchor["anchor_id"]]
            next_feature = features[next_anchor["anchor_id"]]
            score, reasons = score_boundary_pair(prev_unit, next_unit, prev_feature, next_feature)
            candidates.append({
                "candidate_id": len(candidates),
                "preferred_boundary": {
                    "kind": "source_unit",
                    "source_unit_id": boundary_unit,
                    "spine_index": next_unit["spine_index"],
                    "href": next_unit["href"],
                    "local_offset": next_unit["start_local_offset"],
                },
                "source_unit_boundary": boundary_unit,
                "anchor_boundary": anchor_boundary,
                "snap": snap,
                "score": round(score, 3),
                "reasons": reasons,
                "selected": False,
                "rejected_reason": None,
            })
        return candidates


def score_boundary_pair(
    prev_unit: dict[str, Any],
    next_unit: dict[str, Any],
    prev_feature: dict[str, Any],
    next_feature: dict[str, Any],
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    if prev_unit["spine_index"] != next_unit["spine_index"] or prev_unit["href"] != next_unit["href"]:
        score += 1.0
        reasons.append("chapter_start")
    if is_scene_separator(prev_unit.get("_text", prev_unit.get("preview", ""))) or is_scene_separator(next_unit.get("_text", next_unit.get("preview", ""))):
        score += 0.8
        reasons.append("scene_separator")

    dialogue_delta = abs(prev_feature["dialogue_ratio"] - next_feature["dialogue_ratio"])
    if dialogue_delta >= 0.02:
        score += min(0.25, dialogue_delta * 4)
        reasons.append("dialogue_shift")

    prev_keywords = flatten_keywords(prev_feature["keyword_hits"])
    next_keywords = flatten_keywords(next_feature["keyword_hits"])
    if prev_keywords != next_keywords:
        score += min(0.25, 0.08 * len(prev_keywords.symmetric_difference(next_keywords)))
        reasons.append("keyword_shift")

    sentence_delta = abs(prev_feature["avg_sentence_words"] - next_feature["avg_sentence_words"])
    punctuation_delta = abs(prev_feature["punctuation_density"] - next_feature["punctuation_density"])
    if sentence_delta >= 8 or punctuation_delta >= 0.03:
        score += min(0.25, sentence_delta / 60 + punctuation_delta * 3)
        reasons.append("pacing_shift")

    return score, reasons


def build_regions(
    anchors: list[dict[str, Any]],
    boundary_candidates: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    if not anchors:
        return {"regions": [], "boundary_candidates": boundary_candidates}

    min_anchors = int(config.get("min_anchors", DEFAULT_REGION_CONFIG["min_anchors"]))
    max_anchors = int(config.get("max_anchors", DEFAULT_REGION_CONFIG["max_anchors"]))
    threshold = float(config.get("boundary_threshold", DEFAULT_REGION_CONFIG["boundary_threshold"]))
    by_edge = best_candidate_by_anchor_edge(boundary_candidates)
    selected: list[dict[str, Any]] = [book_boundary_candidate(0, "book_start")]
    region_start = 0

    for edge in range(1, len(anchors)):
        candidate = by_edge.get(edge)
        length = edge - region_start
        if length >= max_anchors:
            selected.append(select_boundary(edge, candidate, "max_region_length", 1.0))
            region_start = edge
        elif length < min_anchors:
            if candidate:
                candidate["rejected_reason"] = candidate.get("rejected_reason") or "too_short"
        elif candidate and candidate["score"] >= threshold:
            selected.append(select_boundary(edge, candidate, None, candidate["score"]))
            region_start = edge
        elif candidate:
            candidate["rejected_reason"] = candidate.get("rejected_reason") or "score_below_threshold"

    selected.append(book_boundary_candidate(len(anchors), "book_end"))
    regions = []
    for region_id, (boundary_in, boundary_out) in enumerate(zip(selected, selected[1:])):
        start = boundary_in["anchor_boundary"]
        end = boundary_out["anchor_boundary"]
        if start == end:
            continue
        region_anchors = anchors[start:end]
        regions.append({
            "region_id": region_id,
            "anchor_start": start,
            "anchor_end": end,
            "source_unit_start": region_anchors[0]["source_unit_start"],
            "source_unit_end": region_anchors[-1]["source_unit_end"],
            "boundary_in": boundary_summary(boundary_in),
            "boundary_out": boundary_summary(boundary_out),
            "stats": {
                "word_count": sum(a["text"]["word_count"] for a in region_anchors),
                "anchor_count": len(region_anchors),
            },
            "preview": preview_text(region_anchors[0]["text"]["preview"]),
        })
    return {"regions": regions, "boundary_candidates": boundary_candidates}


def best_candidate_by_anchor_edge(candidates: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    by_edge: dict[int, dict[str, Any]] = {}
    for candidate in candidates:
        edge = candidate["anchor_boundary"]
        if edge <= 0:
            continue
        if edge not in by_edge or candidate["score"] > by_edge[edge]["score"]:
            by_edge[edge] = candidate
    return by_edge


def select_boundary(
    edge: int,
    candidate: Optional[dict[str, Any]],
    added_reason: Optional[str],
    minimum_score: float,
) -> dict[str, Any]:
    if candidate is None:
        candidate = {
            "anchor_boundary": edge,
            "score": minimum_score,
            "reasons": [],
            "snap": "anchor_edge",
            "selected": False,
        }
    candidate["selected"] = True
    candidate["rejected_reason"] = None
    candidate["score"] = round(max(candidate.get("score", 0), minimum_score), 3)
    if added_reason and added_reason not in candidate["reasons"]:
        candidate["reasons"].append(added_reason)
    return candidate


def book_boundary_candidate(edge: int, reason: str) -> dict[str, Any]:
    return {
        "anchor_boundary": edge,
        "score": 1.0,
        "reasons": [reason],
        "snap": "exact",
        "selected": True,
        "rejected_reason": None,
    }


def boundary_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "anchor_boundary": candidate["anchor_boundary"],
        "source_unit_boundary": candidate.get("source_unit_boundary"),
        "score": candidate["score"],
        "reasons": candidate.get("reasons", []),
        "selected": candidate.get("selected", False),
        "rejected_reason": candidate.get("rejected_reason"),
        "snap": candidate.get("snap"),
    }


def find_anchor_covering_source_unit(anchors: list[dict[str, Any]], source_unit_id: int) -> Optional[dict[str, Any]]:
    for anchor in anchors:
        if anchor["source_unit_start"] <= source_unit_id < anchor["source_unit_end"]:
            return anchor
    return None


def snap_source_boundary_to_anchor(anchors: list[dict[str, Any]], source_unit_boundary: int) -> tuple[int, str]:
    best_edge = 0
    best_distance: Optional[int] = None
    for edge in range(1, len(anchors)):
        anchor_source_boundary = anchors[edge]["source_unit_start"]
        distance = abs(anchor_source_boundary - source_unit_boundary)
        if best_distance is None or distance < best_distance:
            best_edge = edge
            best_distance = distance
    snap = "exact" if best_distance == 0 else "nearest_anchor"
    return best_edge, snap


def is_scene_separator(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return bool(compact) and len(compact) <= 5 and set(compact) <= {"*", "#", "-", "_"}


def flatten_keywords(keyword_hits: dict[str, list[str]]) -> set[str]:
    words: set[str] = set()
    for hits in keyword_hits.values():
        words.update(hits)
    return words


def find_region_for_anchor(timeline: dict[str, Any], anchor_id: int) -> Optional[dict[str, Any]]:
    for region in timeline.get("regions", []):
        if region["anchor_start"] <= anchor_id < region["anchor_end"]:
            return region
    return None


def preview_text(text: str, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "..."


def find_anchor_for_position(
    timeline: dict[str, Any],
    spine_index: int,
    local_char_offset: int,
) -> Optional[dict[str, Any]]:
    candidates = [
        a for a in timeline.get("anchors", [])
        if a.get("position", {}).get("spine_index") == spine_index
    ]
    if not candidates:
        return None
    for anchor in candidates:
        pos = anchor["position"]
        if pos["start_local_offset"] <= local_char_offset < pos["end_local_offset"]:
            return anchor
    before = [a for a in candidates if a["position"]["end_local_offset"] <= local_char_offset]
    after = [a for a in candidates if a["position"]["start_local_offset"] > local_char_offset]
    if before and not after:
        return before[-1]
    if after and not before:
        return after[0]
    if before and after:
        prev_dist = abs(local_char_offset - before[-1]["position"]["end_local_offset"])
        next_dist = abs(local_char_offset - after[0]["position"]["start_local_offset"])
        return before[-1] if prev_dist <= next_dist else after[0]
    return None
