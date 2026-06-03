"""Deterministic region-level atmosphere scoring."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from typing import Any


ATMOSPHERE_SCHEMA_VERSION = 1
ATMOSPHERE_METHOD_NAME = "deterministic_cue_scoring"
ATMOSPHERE_METHOD_VERSION = "0.1"
CUE_LEXICON_VERSION = "0.1"

ATMOSPHERE_TAXONOMY = {
    "setting": ["interior", "exterior", "underground", "vehicle", "unknown"],
    "environment": ["weather", "water", "fire", "machinery", "crowd", "silence", "unknown"],
    "energy": ["quiet", "tense", "motion", "combat", "ritual", "unknown"],
    "affect": ["dread", "wonder", "grief", "anger", "relief", "neutral", "unknown"],
}

ATMOSPHERE_CONFIG = {
    "min_medium_score": 2.0,
    "min_high_score": 4.0,
    "min_margin": 0.75,
    "max_evidence_anchors": 3,
}

CUE_LEXICON = {
    "setting": {
        "interior": ["room", "hall", "house", "kitchen", "door", "window", "chamber", "cabin"],
        "exterior": ["forest", "woods", "street", "road", "field", "sky", "garden", "mountain"],
        "underground": ["cave", "tunnel", "cellar", "crypt", "mine", "underground"],
        "vehicle": ["car", "train", "ship", "boat", "wagon", "carriage", "truck", "bus"],
    },
    "environment": {
        "weather": ["rain", "storm", "thunder", "lightning", "wind", "snow", "fog", "hail"],
        "water": ["river", "sea", "ocean", "wave", "water", "stream", "lake", "rain"],
        "fire": ["fire", "flame", "smoke", "ash", "ember", "burning", "torch"],
        "machinery": ["engine", "machine", "gear", "motor", "factory", "metal", "steam"],
        "crowd": ["crowd", "voices", "people", "market", "audience", "mob", "cheer"],
        "silence": ["silence", "silent", "quiet", "hush", "stillness", "whisper"],
    },
    "energy": {
        "quiet": ["quiet", "silent", "still", "soft", "slow", "calm", "hush"],
        "tense": ["fear", "danger", "threat", "tense", "watching", "waited", "breath"],
        "motion": ["ran", "run", "rushed", "fled", "chased", "moved", "climbed", "jumped"],
        "combat": ["fight", "battle", "sword", "blood", "gun", "shot", "attack", "wound"],
        "ritual": ["prayer", "chant", "altar", "ceremony", "ritual", "sacrifice"],
    },
    "affect": {
        "dread": ["dread", "terror", "fear", "horror", "afraid", "panic"],
        "wonder": ["wonder", "marvel", "glow", "beautiful", "awe", "strange"],
        "grief": ["grief", "tears", "wept", "mourning", "loss", "sorrow"],
        "anger": ["anger", "rage", "furious", "hate", "shouted", "fist"],
        "relief": ["relief", "safe", "smiled", "laugh", "peace", "free"],
        "neutral": ["said", "looked", "thought", "walked", "stood"],
    },
}

PLANNER_FAMILIES = ("setting", "environment", "energy")


def build_atmosphere_extension(
    timeline: dict[str, Any],
    anchors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    source_anchors = anchors if anchors is not None else timeline.get("anchors", [])
    region_labels = [
        score_region(region, source_anchors)
        for region in timeline.get("regions", [])
    ]
    churn = transition_churn(region_labels)
    return {
        "schema_version": ATMOSPHERE_SCHEMA_VERSION,
        "method": {
            "name": ATMOSPHERE_METHOD_NAME,
            "version": ATMOSPHERE_METHOD_VERSION,
            "configuration_hash": configuration_hash(ATMOSPHERE_CONFIG, CUE_LEXICON),
            "cue_lexicon_version": CUE_LEXICON_VERSION,
            "model_assisted_comparator": {
                "status": "not_run",
                "reason": "no local model comparator configured and remote calls are out of scope",
            },
        },
        "taxonomy": {
            "version": CUE_LEXICON_VERSION,
            "families": ATMOSPHERE_TAXONOMY,
        },
        "region_labels": region_labels,
        "diagnostics": {
            "label_counts": label_counts(region_labels),
            "abstention_rate": abstention_rate(region_labels),
            "transition_churn": churn,
        },
    }


def score_region(region: dict[str, Any], anchors: list[dict[str, Any]]) -> dict[str, Any]:
    region_anchors = [
        anchor
        for anchor in anchors
        if region["anchor_start"] <= anchor.get("anchor_id", -1) < region["anchor_end"]
    ]
    labels = [
        score_family(family, values, region_anchors, region)
        for family, values in ATMOSPHERE_TAXONOMY.items()
    ]
    effective = effective_atmosphere(labels)
    return {
        "region_id": region["region_id"],
        "labels": labels,
        "effective_atmosphere": effective,
        "abstained": effective == "unknown",
    }


def score_family(
    family: str,
    values: list[str],
    anchors: list[dict[str, Any]],
    region: dict[str, Any],
) -> dict[str, Any]:
    scores: dict[str, float] = {value: 0.0 for value in values if value != "unknown"}
    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cue_map = CUE_LEXICON.get(family, {})
    for anchor in anchors:
        text = anchor.get("text", {}).get("plain") or anchor.get("text", {}).get("preview", "")
        words = Counter(re.findall(r"[A-Za-z']+", text.casefold()))
        for value, cues in cue_map.items():
            hits = sorted({cue for cue in cues if words.get(cue, 0) > 0})
            if not hits:
                continue
            score = sum(words[cue] for cue in hits)
            scores[value] += score
            evidence[value].append({
                "anchor_id": anchor["anchor_id"],
                "cue_categories": [value],
                "matched_cues": hits[:6],
                "preview": anchor.get("text", {}).get("preview", ""),
            })

    add_context_scores(scores, family, region)
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    best_value, best_score = ranked[0] if ranked else ("unknown", 0.0)
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = best_score - second_score
    confidence = confidence_band(best_score, margin)
    if confidence == "abstain":
        value = "unknown"
        polarity = "mixed" if best_score and margin < ATMOSPHERE_CONFIG["min_margin"] else "negative"
    else:
        value = best_value
        polarity = "positive"
    return {
        "family": family,
        "value": value,
        "score": round(best_score, 3),
        "confidence": confidence,
        "polarity": polarity,
        "evidence": evidence_for(value, evidence),
        "competing": competing_labels(ranked, best_value),
    }


def add_context_scores(scores: dict[str, float], family: str, region: dict[str, Any]) -> None:
    reasons = set(region.get("boundary_in", {}).get("reasons", [])) | set(region.get("boundary_out", {}).get("reasons", []))
    if family == "energy" and "scene_separator" in reasons:
        scores["quiet"] = scores.get("quiet", 0.0) + 0.5
    if family == "energy" and region.get("stats", {}).get("anchor_count", 0) <= 2:
        scores["motion"] = scores.get("motion", 0.0) + 0.25


def confidence_band(score: float, margin: float) -> str:
    if score >= ATMOSPHERE_CONFIG["min_high_score"] and margin >= ATMOSPHERE_CONFIG["min_margin"]:
        return "high"
    if score >= ATMOSPHERE_CONFIG["min_medium_score"] and margin >= ATMOSPHERE_CONFIG["min_margin"]:
        return "medium"
    if score > 0 and margin >= ATMOSPHERE_CONFIG["min_margin"]:
        return "low"
    return "abstain"


def evidence_for(value: str, evidence: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    if value == "unknown":
        return []
    return evidence.get(value, [])[: int(ATMOSPHERE_CONFIG["max_evidence_anchors"])]


def competing_labels(ranked: list[tuple[str, float]], best_value: str) -> list[dict[str, Any]]:
    return [
        {"value": value, "score": round(score, 3)}
        for value, score in ranked
        if value != best_value and score > 0
    ][:3]


def effective_atmosphere(labels: list[dict[str, Any]]) -> str:
    parts = [
        f"{label['family']}:{label['value']}"
        for label in labels
        if label["family"] in PLANNER_FAMILIES
        and label["value"] != "unknown"
        and label["confidence"] in {"medium", "high"}
    ]
    return "|".join(parts) if parts else "unknown"


def transition_churn(region_labels: list[dict[str, Any]]) -> dict[str, Any]:
    collapsed: list[dict[str, Any]] = []
    for item in region_labels:
        atmosphere = item.get("effective_atmosphere") or "unknown"
        if collapsed and collapsed[-1]["effective_atmosphere"] == atmosphere:
            collapsed[-1]["region_end"] = item["region_id"] + 1
            collapsed[-1]["region_ids"].append(item["region_id"])
        else:
            collapsed.append({
                "effective_atmosphere": atmosphere,
                "region_start": item["region_id"],
                "region_end": item["region_id"] + 1,
                "region_ids": [item["region_id"]],
            })
    rapid = [
        item
        for item in collapsed
        if item["region_end"] - item["region_start"] == 1 and item["effective_atmosphere"] != "unknown"
    ]
    return {
        "region_count": len(region_labels),
        "collapsed_count": len(collapsed),
        "transition_count": max(0, len(collapsed) - 1),
        "rapid_changes": rapid,
        "collapsed_regions": collapsed,
    }


def label_counts(region_labels: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for item in region_labels:
        for label in item.get("labels", []):
            counts[label["family"]][label["value"]] += 1
    return {family: dict(counter) for family, counter in counts.items()}


def abstention_rate(region_labels: list[dict[str, Any]]) -> float:
    if not region_labels:
        return 0.0
    return round(sum(1 for item in region_labels if item.get("abstained")) / len(region_labels), 3)


def configuration_hash(config: dict[str, Any], lexicon: dict[str, Any]) -> str:
    payload = json.dumps({"config": config, "lexicon": lexicon}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def atmosphere_by_region(timeline: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(item["region_id"]): item
        for item in timeline.get("atmosphere", {}).get("region_labels", [])
    }
