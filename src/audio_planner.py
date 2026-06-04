"""Declarative audio intent planning for prepared regions."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

AUDIO_ASSET_CATALOG_SCHEMA_VERSION = 1
AUDIO_INTENT_SCHEMA_VERSION = 1
PLANNER_METHOD_VERSION = "audio_intent_planner.v1"

DEFAULT_PLANNER_CONFIG = {
    "fade_in_seconds": 6.0,
    "fade_out_seconds": 8.0,
    "minimum_dwell_seconds": 45.0,
    "jump_suppression_seconds": 3.0,
    "max_transitions_per_minute": 2,
}

FAMILY_CATEGORY_MAP = {
    "setting": {
        "interior": "interior",
        "exterior": "exterior",
        "underground": "underground",
        "vehicle": "vehicle",
    },
    "environment": {
        "weather": "weather",
        "water": "water",
        "fire": "fire",
        "machinery": "machinery",
        "crowd": "crowd",
        "silence": "quiet",
    },
    "energy": {
        "quiet": "quiet",
        "tense": "tension",
        "motion": "motion",
        "combat": "combat",
        "ritual": "ritual",
    },
    "affect": {
        "dread": "tension",
        "wonder": "wonder",
        "grief": "neutral",
        "anger": "tension",
        "relief": "quiet",
        "neutral": "neutral",
    },
}

PLANNER_ASSET_CATEGORIES = sorted({
    category
    for family in FAMILY_CATEGORY_MAP.values()
    for category in family.values()
})


def audio_asset_catalog_path(data_dir: str | Path, calibre_book_id: int | str) -> Path:
    return Path(data_dir) / "books" / str(calibre_book_id) / "audio_assets.json"


def default_asset_catalog() -> dict[str, Any]:
    return {
        "schema_version": AUDIO_ASSET_CATALOG_SCHEMA_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "assets": [
            {
                "asset_id": "neutral_silence",
                "path": "",
                "license": "built-in silent fallback",
                "loopable": True,
                "role": "base_bed",
                "categories": ["neutral"],
                "intensity_min": 0.0,
                "intensity_max": 1.0,
                "default_gain": 0.0,
            }
        ],
    }


def load_asset_catalog(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return default_asset_catalog()
    catalog_path = Path(path)
    if not catalog_path.exists():
        return default_asset_catalog()
    with open(catalog_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    validate_asset_catalog(catalog)
    return catalog


def validate_asset_catalog(catalog: dict[str, Any]) -> None:
    if catalog.get("schema_version") != AUDIO_ASSET_CATALOG_SCHEMA_VERSION:
        raise ValueError("Unsupported audio asset catalog schema_version")
    for asset in catalog.get("assets", []):
        for key in (
            "asset_id",
            "path",
            "license",
            "loopable",
            "role",
            "categories",
            "intensity_min",
            "intensity_max",
            "default_gain",
        ):
            if key not in asset:
                raise ValueError(f"Audio asset missing {key}: {asset}")
        if asset["role"] not in {"base_bed", "layer"}:
            raise ValueError(f"Unsupported asset role: {asset['role']}")
        if not isinstance(asset["categories"], list) or not asset["categories"]:
            raise ValueError(f"Audio asset categories must be a non-empty list: {asset}")
        if float(asset["intensity_min"]) > float(asset["intensity_max"]):
            raise ValueError(f"Audio asset intensity_min exceeds intensity_max: {asset}")
        gain = float(asset["default_gain"])
        if gain < 0.0 or gain > 1.0:
            raise ValueError(f"Audio asset default_gain must be normalized 0..1: {asset}")


def build_audio_intents(
    timeline: dict[str, Any],
    catalog: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = {**DEFAULT_PLANNER_CONFIG, **(config or {})}
    assets = (catalog or default_asset_catalog()).get("assets", [])
    atmosphere_by_region = {
        item["region_id"]: item
        for item in timeline.get("atmosphere", {}).get("region_labels", [])
    }
    intents = []
    for region in timeline.get("regions", []):
        atmosphere = atmosphere_by_region.get(region["region_id"])
        query = asset_query_for_region(region, atmosphere)
        requested_base_category = query["base_categories"][0] if query["base_categories"] else "neutral"
        base_selection = select_asset_with_category(
            assets,
            "base_bed",
            query["base_categories"],
            query["intensity"],
        )
        forced_default_fallback = False
        if base_selection is None:
            base_selection = select_asset_with_category(assets, "base_bed", ["neutral"], 0.0)
        if base_selection is None:
            base_selection = (default_asset_catalog()["assets"][0], "neutral")
            forced_default_fallback = True
        base_asset, matched_base_category = base_selection
        fallback = forced_default_fallback or matched_base_category != requested_base_category
        layer_selections = [
            selection
            for category in query["layer_categories"]
            if (selection := select_asset_with_category(assets, "layer", [category], query["intensity"])) is not None
        ]
        base_intent = intent_asset(base_asset, matched_base_category)
        if fallback and matched_base_category != requested_base_category:
            base_intent["fallback_from_category"] = requested_base_category
        intents.append({
            "schema_version": AUDIO_INTENT_SCHEMA_VERSION,
            "intent_id": f"region-{region['region_id']}",
            "region_id": region["region_id"],
            "effective_atmosphere": query["effective_atmosphere"],
            "base_bed": base_intent,
            "layers": [intent_asset(layer, category) for layer, category in layer_selections],
            "intensity": query["intensity"],
            "fade_in_seconds": cfg["fade_in_seconds"],
            "fade_out_seconds": cfg["fade_out_seconds"],
            "minimum_dwell_seconds": cfg["minimum_dwell_seconds"],
            "suppression_reason": "neutral_fallback" if fallback else None,
            "source": query["source"],
        })
    return {
        "schema_version": AUDIO_INTENT_SCHEMA_VERSION,
        "method": {"name": "audio_intent_planner", "version": PLANNER_METHOD_VERSION},
        "config": cfg,
        "catalog": {
            "schema_version": (catalog or default_asset_catalog()).get("schema_version"),
            "asset_count": len(assets),
        },
        "intents": intents,
        "diagnostics": {
            "region_count": len(timeline.get("regions", [])),
            "intent_count": len(intents),
            "neutral_fallback_count": sum(1 for intent in intents if intent.get("suppression_reason") == "neutral_fallback"),
        },
    }


def asset_query_for_region(region: dict[str, Any], atmosphere: dict[str, Any] | None) -> dict[str, Any]:
    if not atmosphere or atmosphere.get("abstained") or atmosphere.get("effective_atmosphere") in {None, "unknown"}:
        return {
            "effective_atmosphere": "unknown",
            "base_categories": ["neutral"],
            "layer_categories": [],
            "intensity": 0.15,
            "source": {"labels": [], "confidence_bands": [], "region_stats": region.get("stats", {})},
        }
    labels = atmosphere.get("labels", [])
    category_confidence = []
    for label in labels:
        category = FAMILY_CATEGORY_MAP.get(label.get("family"), {}).get(label.get("value"))
        if category and label.get("confidence") != "abstain":
            category_confidence.append((category, label.get("confidence", "low"), label.get("family"), label.get("value")))
    if not category_confidence:
        return {
            "effective_atmosphere": atmosphere.get("effective_atmosphere", "unknown"),
            "base_categories": ["neutral"],
            "layer_categories": [],
            "intensity": 0.2,
            "source": {"labels": [], "confidence_bands": [], "region_stats": region.get("stats", {})},
        }
    base_category = category_confidence[0][0]
    layer_categories = [item[0] for item in category_confidence[1:3] if item[0] != base_category]
    confidence_bands = [item[1] for item in category_confidence]
    intensity = confidence_intensity(confidence_bands[0], atmosphere.get("effective_atmosphere"))
    return {
        "effective_atmosphere": atmosphere.get("effective_atmosphere", "unknown"),
        "base_categories": [base_category, "neutral"],
        "layer_categories": layer_categories,
        "intensity": intensity,
        "source": {
            "labels": [{"family": item[2], "value": item[3], "category": item[0]} for item in category_confidence],
            "confidence_bands": confidence_bands,
            "region_stats": region.get("stats", {}),
        },
    }


def confidence_intensity(confidence: str, effective_atmosphere: str | None) -> float:
    base = {"high": 0.65, "medium": 0.45, "low": 0.3}.get(confidence, 0.2)
    if effective_atmosphere and any(tag in effective_atmosphere for tag in ("combat", "fire", "motion")):
        base += 0.1
    if effective_atmosphere and any(tag in effective_atmosphere for tag in ("quiet", "silence")):
        base -= 0.1
    return round(max(0.0, min(1.0, base)), 2)


def select_asset(assets: list[dict[str, Any]], role: str, categories: list[str], intensity: float) -> dict[str, Any] | None:
    for category in categories:
        for asset in assets:
            if asset.get("role") != role or not asset.get("loopable", False):
                continue
            if category not in asset.get("categories", []):
                continue
            if float(asset.get("intensity_min", 0.0)) <= intensity <= float(asset.get("intensity_max", 1.0)):
                return asset
    return None


def select_asset_with_category(
    assets: list[dict[str, Any]],
    role: str,
    categories: list[str],
    intensity: float,
) -> tuple[dict[str, Any], str] | None:
    for category in categories:
        asset = select_asset(assets, role, [category], intensity)
        if asset is not None:
            return asset, category
    return None


def intent_asset(asset: dict[str, Any], matched_category: str) -> dict[str, Any]:
    return {
        "asset_id": asset["asset_id"],
        "path": asset["path"],
        "matched_category": matched_category,
        "default_gain": asset["default_gain"],
        "role": asset["role"],
    }


def catalog_coverage_report(catalog: dict[str, Any]) -> dict[str, Any]:
    assets = catalog.get("assets", [])
    duplicate_asset_ids = sorted({
        asset.get("asset_id")
        for asset in assets
        if sum(1 for item in assets if item.get("asset_id") == asset.get("asset_id")) > 1
    })
    coverage = {}
    for category in PLANNER_ASSET_CATEGORIES:
        base_assets = matching_catalog_assets(assets, category, "base_bed")
        layer_assets = matching_catalog_assets(assets, category, "layer")
        coverage[category] = {
            "base_bed_count": len(base_assets),
            "layer_count": len(layer_assets),
            "base_bed_assets": [asset["asset_id"] for asset in base_assets],
            "layer_assets": [asset["asset_id"] for asset in layer_assets],
        }
    return {
        "schema_version": catalog.get("schema_version"),
        "asset_count": len(assets),
        "categories": coverage,
        "missing_base_bed_categories": [
            category for category, info in coverage.items() if info["base_bed_count"] == 0
        ],
        "missing_layer_categories": [
            category for category, info in coverage.items() if info["layer_count"] == 0
        ],
        "provenance_pending_assets": [
            asset["asset_id"]
            for asset in assets
            if "pending" in str(asset.get("license", "")).casefold()
        ],
        "duplicate_asset_ids": duplicate_asset_ids,
    }


def matching_catalog_assets(assets: list[dict[str, Any]], category: str, role: str) -> list[dict[str, Any]]:
    return [
        asset
        for asset in assets
        if asset.get("role") == role
        and asset.get("loopable", False)
        and category in asset.get("categories", [])
    ]


def simulate_reading_trace(
    intents: list[dict[str, Any]],
    trace: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = {**DEFAULT_PLANNER_CONFIG, **(config or {})}
    by_region = {intent["region_id"]: intent for intent in intents}
    active_region = None
    active_intent = None
    candidate_region = None
    candidate_since = None
    last_transition_at = None
    transition_times: list[float] = []
    events = []
    for poll in sorted(trace, key=lambda item: item["time_seconds"]):
        now = float(poll["time_seconds"])
        region_id = poll.get("region_id")
        intent = by_region.get(region_id)
        if intent is None:
            events.append(event(now, region_id, "suppress", active_intent, "unknown_region"))
            continue
        if active_region is None:
            active_region = region_id
            active_intent = intent
            last_transition_at = now
            transition_times.append(now)
            events.append(event(now, region_id, "transition", intent, "initial"))
            continue
        if region_id == active_region:
            candidate_region = None
            candidate_since = None
            events.append(event(now, region_id, "hold", active_intent, "same_region"))
            continue
        if candidate_region != region_id:
            candidate_region = region_id
            candidate_since = now
            events.append(event(now, region_id, "suppress", active_intent, "jump_suppression"))
            continue
        if now - float(candidate_since or now) < float(cfg["jump_suppression_seconds"]):
            events.append(event(now, region_id, "suppress", active_intent, "jump_suppression"))
            continue
        if last_transition_at is not None and now - last_transition_at < float(cfg["minimum_dwell_seconds"]):
            events.append(event(now, region_id, "suppress", active_intent, "minimum_dwell"))
            continue
        recent = [time for time in transition_times if now - time < 60.0]
        if len(recent) >= int(cfg["max_transitions_per_minute"]):
            events.append(event(now, region_id, "suppress", active_intent, "transition_rate_limit"))
            continue
        active_region = region_id
        active_intent = intent
        last_transition_at = now
        transition_times = recent + [now]
        candidate_region = None
        candidate_since = None
        events.append(event(now, region_id, "transition", intent, "region_change"))
    return {
        "config": cfg,
        "poll_count": len(trace),
        "transition_count": sum(1 for item in events if item["action"] == "transition"),
        "events": events,
    }


def event(
    time_seconds: float,
    region_id: int | None,
    action: str,
    intent: dict[str, Any] | None,
    reason: str,
) -> dict[str, Any]:
    return {
        "time_seconds": time_seconds,
        "region_id": region_id,
        "action": action,
        "intent_id": intent.get("intent_id") if intent else None,
        "effective_atmosphere": intent.get("effective_atmosphere") if intent else None,
        "reason": reason,
    }
