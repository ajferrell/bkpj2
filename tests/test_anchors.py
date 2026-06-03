import json
from pathlib import Path

from src.anchors import (
    BoundaryProvider,
    DEFAULT_REGION_CONFIG,
    REGION_PROFILES,
    apply_region_review_marks,
    build_region_review_artifact,
    build_regions,
    build_anchors_from_spine,
    evaluate_region_review,
    extract_anchor_features,
    extract_source_units,
    find_anchor_for_position,
    find_region_for_anchor,
    inspect_position_chain,
    inspect_text_path,
    load_timeline,
    load_timeline_with_sidecars,
    paragraph_spans,
    prepare_book_timeline,
    region_diagnostics_path,
    region_review_path,
    remove_inspect_text,
    remove_regions_from_timeline,
    remove_timeline,
    resolve_region_config,
    source_units_path,
    timeline_drift_warnings,
    timeline_path,
    write_region_review_artifact,
)
from src.audio_planner import build_audio_intents, simulate_reading_trace
from src.atmosphere import build_atmosphere_extension, transition_churn
from src.calibre_native import LiveAnnotation


def fake_book(tmp_path: Path, calibre_book_id: int = 42) -> dict:
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"fake epub")
    return {
        "calibre_book_id": calibre_book_id,
        "calibre_uuid": f"uuid-{calibre_book_id}",
        "title": "Book",
        "authors": ["Author"],
        "preferred_epub_path": str(epub),
        "annots_key": "abc.json",
    }


def test_paragraph_spans_preserve_offsets():
    text = "One two.\n\nThree four five.\n\nSix."
    spans = paragraph_spans(text)

    assert spans[0]["start"] == 0
    assert spans[0]["end"] == len("One two.")
    assert spans[1]["text"] == "Three four five."
    assert spans[1]["word_count"] == 3


def test_source_units_preserve_order_and_offsets():
    spine = [
        {
            "spine_index": 0,
            "href": "one.xhtml",
            "text": "One two.\n\nThree four five.",
        },
        {
            "spine_index": 1,
            "href": "two.xhtml",
            "text": "Six seven.",
        },
    ]

    units = extract_source_units(spine)

    assert [unit["unit_id"] for unit in units] == [0, 1, 2]
    assert [unit["href"] for unit in units] == ["one.xhtml", "one.xhtml", "two.xhtml"]
    assert units[0]["start_local_offset"] == 0
    assert units[0]["end_local_offset"] == len("One two.")
    assert units[2]["start_local_offset"] == 0
    assert units[2]["kind"] == "paragraph"


def test_build_anchors_groups_paragraphs_and_finds_offset():
    spine = [
        {
            "spine_index": 2,
            "href": "chapter.xhtml",
            "text": "one two three.\n\nfour five six.\n\nseven eight nine.\n\nlast bit.",
            "text_len": 62,
        }
    ]

    anchors = build_anchors_from_spine(spine, target_words=6, min_words=3)
    assert len(anchors) == 2
    assert anchors[0]["position"]["spine_index"] == 2
    assert anchors[0]["text"]["word_count"] == 6

    timeline = {"anchors": anchors}
    anchor = find_anchor_for_position(timeline, spine_index=2, local_char_offset=spine[0]["text"].index("seven"))
    assert anchor is not None
    assert anchor["anchor_id"] == 1


def test_anchors_cover_contiguous_source_units_without_gaps():
    spine = [
        {
            "spine_index": 0,
            "href": "chapter.xhtml",
            "text": "one two.\n\nthree four.\n\nfive six.\n\nseven eight.",
        }
    ]

    anchors = build_anchors_from_spine(spine, target_words=2, min_words=1)

    assert [(a["source_unit_start"], a["source_unit_end"]) for a in anchors] == [
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 4),
    ]


def test_prepare_book_timeline_with_fake_extractor(tmp_path):
    book = fake_book(tmp_path, 42)

    def fake_extractor(epub_path):
        assert epub_path == book["preferred_epub_path"]
        return [
            {
                "spine_index": 0,
                "href": "chapter.xhtml",
                "text": "Alpha beta gamma.\n\nDelta epsilon zeta.",
                "text_len": 39,
            }
        ]

    path = prepare_book_timeline(
        book,
        data_dir=tmp_path / "data",
        target_words=6,
        min_words=3,
        spine_extractor=fake_extractor,
    )
    timeline = load_timeline(tmp_path / "data", 42)

    assert path.exists()
    assert timeline["schema_version"] == 5
    assert timeline["book"]["title"] == "Book"
    assert "source_units" not in timeline
    assert len(timeline["anchors"]) == 1
    assert timeline["anchors"][0]["source_unit_start"] == 0
    assert timeline["anchors"][0]["source_unit_end"] == 2
    assert timeline["anchors"][0]["text"]["word_count"] == 6
    assert "plain" not in timeline["anchors"][0]["text"]
    source_sidecar = json.loads(source_units_path(tmp_path / "data", 42).read_text(encoding="utf-8"))
    assert len(source_sidecar["source_units"]) == 2
    assert "_text" not in source_sidecar["source_units"][0]
    assert not inspect_text_path(tmp_path / "data", 42).exists()


def test_prepare_book_timeline_debug_text_writes_sidecar(tmp_path):
    book = fake_book(tmp_path, 44)

    def fake_extractor(epub_path):
        return [
            {
                "spine_index": 0,
                "href": "chapter.xhtml",
                "text": "Alpha beta gamma.\n\nDelta epsilon zeta.",
            }
        ]

    prepare_book_timeline(
        book,
        data_dir=tmp_path / "data",
        target_words=6,
        min_words=3,
        debug_text=True,
        spine_extractor=fake_extractor,
    )
    timeline = load_timeline(tmp_path / "data", 44)
    sidecar = inspect_text_path(tmp_path / "data", 44)

    assert sidecar.exists()
    assert "plain" not in timeline["anchors"][0]["text"]
    assert "source_units" not in timeline
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["anchors"]["0"]["plain"] == "Alpha beta gamma.\n\nDelta epsilon zeta."
    assert data["source_units"]["0"]["text"] == "Alpha beta gamma."


def test_prepare_book_timeline_can_write_regions(tmp_path):
    book = fake_book(tmp_path, 43)

    def fake_extractor(epub_path):
        return [
            {
                "spine_index": 0,
                "href": "chapter.xhtml",
                "text": "\n\n".join(f"Paragraph {i} rain." for i in range(5)),
            },
            {
                "spine_index": 1,
                "href": "chapter2.xhtml",
                "text": "\n\n".join(f"Paragraph {i} sword." for i in range(5)),
            },
        ]

    prepare_book_timeline(
        book,
        data_dir=tmp_path / "data",
        target_words=3,
        min_words=1,
        regions=True,
        spine_extractor=fake_extractor,
    )
    timeline = load_timeline(tmp_path / "data", 43)

    assert timeline["regions"]
    assert timeline["regions"][0]["boundary_in"]["reasons"] == ["book_start"]
    assert "region_diagnostics" not in timeline
    assert region_diagnostics_path(tmp_path / "data", 43).exists()


def test_prepare_book_timeline_can_write_atmosphere_without_full_text(tmp_path):
    book = fake_book(tmp_path, 51)

    def fake_extractor(epub_path):
        return [
            {
                "spine_index": 0,
                "href": "chapter.xhtml",
                "text": "\n\n".join([
                    "Rain and thunder filled the forest road.",
                    "The storm wind crossed the dark field.",
                    "Inside the room, the engine ticked quietly.",
                    "The machine hummed beside the closed door.",
                ]),
            },
            {
                "spine_index": 1,
                "href": "chapter2.xhtml",
                "text": "\n\n".join([
                    "The sword fight spilled blood across the hall.",
                    "They ran from the attack and shouted in fear.",
                    "Silence returned and the quiet room grew still.",
                    "A soft hush settled after the danger passed.",
                ]),
            },
        ]

    prepare_book_timeline(
        book,
        data_dir=tmp_path / "data",
        target_words=8,
        min_words=1,
        regions=True,
        atmosphere=True,
        spine_extractor=fake_extractor,
    )
    timeline = load_timeline(tmp_path / "data", 51)
    labels = timeline["atmosphere"]["region_labels"]

    assert timeline["schema_version"] == 5
    assert timeline["builder"]["atmosphere"] is True
    assert len(labels) == len(timeline["regions"])
    assert timeline["atmosphere"]["method"]["configuration_hash"]
    assert timeline["atmosphere"]["method"]["model_assisted_comparator"]["status"] == "not_run"
    assert timeline["atmosphere"]["diagnostics"]["transition_churn"]["region_count"] == len(labels)
    assert "plain" not in timeline["anchors"][0]["text"]
    assert any(
        label["evidence"]
        for item in labels
        for label in item["labels"]
        if label["value"] != "unknown"
    )


def test_atmosphere_requires_regions(tmp_path):
    book = fake_book(tmp_path, 52)

    def fake_extractor(epub_path):
        return [{"spine_index": 0, "href": "chapter.xhtml", "text": "Quiet room."}]

    try:
        prepare_book_timeline(
            book,
            data_dir=tmp_path / "data",
            atmosphere=True,
            spine_extractor=fake_extractor,
        )
    except ValueError as exc:
        assert "--atmosphere requires --regions" in str(exc)
    else:
        raise AssertionError("prepare_book_timeline should reject atmosphere without regions")


def test_atmosphere_abstains_when_cues_are_weak():
    anchors = [
        {
            "anchor_id": 0,
            "text": {"plain": "Alpha beta gamma.", "preview": "Alpha beta gamma.", "word_count": 3},
        }
    ]
    timeline = {
        "regions": [
            {
                "region_id": 0,
                "anchor_start": 0,
                "anchor_end": 1,
                "boundary_in": {"reasons": ["book_start"]},
                "boundary_out": {"reasons": ["book_end"]},
                "stats": {"anchor_count": 1},
            }
        ]
    }

    atmosphere = build_atmosphere_extension(timeline, anchors)
    item = atmosphere["region_labels"][0]

    assert item["abstained"] is True
    assert item["effective_atmosphere"] == "unknown"
    assert all(label["value"] == "unknown" for label in item["labels"])


def test_prepare_book_timeline_debug_text_includes_boundary_candidates(tmp_path):
    book = fake_book(tmp_path, 45)

    def fake_extractor(epub_path):
        return [
            {
                "spine_index": 0,
                "href": "chapter.xhtml",
                "text": "\n\n".join(f"Paragraph {i} rain." for i in range(5)),
            },
            {
                "spine_index": 1,
                "href": "chapter2.xhtml",
                "text": "\n\n".join(f"Paragraph {i} sword." for i in range(5)),
            },
        ]

    prepare_book_timeline(
        book,
        data_dir=tmp_path / "data",
        target_words=3,
        min_words=1,
        regions=True,
        debug_text=True,
        spine_extractor=fake_extractor,
    )
    data = json.loads(inspect_text_path(tmp_path / "data", 45).read_text(encoding="utf-8"))
    candidates = data["boundary_candidates"]

    assert candidates
    assert any(candidate["selected"] for candidate in candidates)
    assert any(candidate["rejected_reason"] for candidate in candidates)


def test_prepare_book_timeline_records_region_profile_and_diagnostics(tmp_path):
    book = fake_book(tmp_path, 49)

    def fake_extractor(epub_path):
        return [
            {
                "spine_index": 0,
                "href": "chapter.xhtml",
                "text": "\n\n".join(f"Paragraph {i} rain." for i in range(8)),
            },
            {
                "spine_index": 1,
                "href": "chapter2.xhtml",
                "text": "\n\n".join(f"Paragraph {i} sword." for i in range(8)),
            },
        ]

    prepare_book_timeline(
        book,
        data_dir=tmp_path / "data",
        target_words=3,
        min_words=1,
        regions=True,
        region_profile="sensitive",
        spine_extractor=fake_extractor,
    )
    timeline = load_timeline_with_sidecars(tmp_path / "data", 49)
    diagnostics = timeline["region_diagnostics"]

    assert timeline["builder"]["region_config"] == REGION_PROFILES["sensitive"]
    assert diagnostics["profile"] == "sensitive"
    assert diagnostics["boundary_candidates"]
    assert all("anchor_boundary" in candidate for candidate in diagnostics["boundary_candidates"])
    assert all(
        "anchor_boundary" in boundary
        for boundary in diagnostics["selected_boundaries"]
    )


def test_region_profiles_change_boundary_sensitivity():
    sensitive = resolve_region_config("sensitive")
    conservative = resolve_region_config("conservative")

    assert sensitive["boundary_threshold"] < conservative["boundary_threshold"]
    assert sensitive["max_anchors"] < conservative["max_anchors"]


def test_region_review_marks_suppress_noisy_and_force_expected_boundaries():
    anchors = [{"anchor_id": i, "source_unit_start": i, "source_unit_end": i + 1, "text": {"word_count": 1, "preview": f"a{i}"}} for i in range(6)]
    candidates = [
        {"anchor_boundary": 2, "source_unit_boundary": 2, "score": 0.8, "reasons": ["keyword_shift"], "selected": False, "rejected_reason": None, "snap": "exact"},
        {"anchor_boundary": 4, "source_unit_boundary": 4, "score": 0.2, "reasons": ["dialogue_shift"], "selected": False, "rejected_reason": None, "snap": "exact"},
    ]
    review = {
        "expected_boundaries": [
            {"anchor_boundary": 2, "noisy_false_positive": True, "review_expected": None},
            {"anchor_boundary": 4, "noisy_false_positive": False, "review_expected": True},
        ]
    }

    apply_region_review_marks(candidates, review)
    result = build_regions(
        anchors,
        candidates,
        {"min_anchors": 1, "target_anchors": 3, "max_anchors": 10, "boundary_threshold": 0.65},
    )
    evaluation = evaluate_region_review(result["boundary_candidates"], review)

    assert candidates[0]["rejected_reason"] == "manual_noisy_false_positive"
    assert candidates[1]["selected"] is True
    assert evaluation["expected_missed"] == []
    assert evaluation["noisy_selected"] == []


def test_region_review_artifact_marks_expected_boundaries(tmp_path):
    book = fake_book(tmp_path, 50)
    timeline = {
        "schema_version": 5,
        "created_at": "2026-06-03T12:00:00",
        "book": {"epub_path": book["preferred_epub_path"], "epub_hash": "hash", "annots_key": "abc.json"},
        "anchors": [{"anchor_id": 0}, {"anchor_id": 1}],
        "regions": [{"region_id": 0, "anchor_start": 0, "anchor_end": 2, "boundary_in": {}, "boundary_out": {}, "preview": "Alpha"}],
        "region_diagnostics": {
            "profile": "normal",
            "boundary_candidates": [
                {"anchor_boundary": 1, "source_unit_boundary": 2, "score": 0.7, "reasons": ["chapter_start"], "selected": True}
            ],
        },
    }

    artifact = build_region_review_artifact(book, timeline)
    path = write_region_review_artifact(book, timeline, data_dir=tmp_path / "data")

    assert artifact["schema_version"] == 1
    assert artifact["expected_boundaries"][0]["review_expected"] is None
    assert region_review_path(tmp_path / "data", 50) == path
    assert json.loads(path.read_text(encoding="utf-8"))["regions"][0]["preview"] == "Alpha"


def test_region_review_artifact_includes_atmosphere_review_fields(tmp_path):
    book = fake_book(tmp_path, 53)
    timeline = {
        "schema_version": 5,
        "created_at": "2026-06-03T12:00:00",
        "book": {"epub_path": book["preferred_epub_path"], "epub_hash": "hash", "annots_key": "abc.json"},
        "anchors": [{"anchor_id": 0}],
        "regions": [{"region_id": 0, "anchor_start": 0, "anchor_end": 1, "boundary_in": {}, "boundary_out": {}, "preview": "Rain"}],
        "region_diagnostics": {"profile": "normal", "boundary_candidates": []},
        "atmosphere": {
            "schema_version": 1,
            "region_labels": [{"region_id": 0, "labels": [], "effective_atmosphere": "unknown", "abstained": True}],
        },
    }

    artifact = build_region_review_artifact(book, timeline)

    assert artifact["timeline"]["atmosphere_schema_version"] == 1
    assert artifact["regions"][0]["atmosphere"]["effective_atmosphere"] == "unknown"
    assert artifact["regions"][0]["review_labels"]["audio_change_useful"] is None


def test_transition_churn_collapses_adjacent_equivalent_atmospheres():
    churn = transition_churn([
        {"region_id": 0, "effective_atmosphere": "setting:exterior"},
        {"region_id": 1, "effective_atmosphere": "setting:exterior"},
        {"region_id": 2, "effective_atmosphere": "environment:fire"},
    ])

    assert churn["collapsed_count"] == 2
    assert churn["transition_count"] == 1
    assert churn["collapsed_regions"][0]["region_ids"] == [0, 1]


def test_region_ranges_cover_all_anchors_exactly_once_and_enforce_maximum():
    spine = [
        {
            "spine_index": 0,
            "href": "chapter.xhtml",
            "text": "\n\n".join(f"Paragraph {i}." for i in range(10)),
        }
    ]
    anchors = build_anchors_from_spine(spine, target_words=2, min_words=1)
    features = extract_anchor_features(anchors)
    candidates = BoundaryProvider().score_boundaries(
        extract_source_units(spine),
        anchors,
        features,
        DEFAULT_REGION_CONFIG,
    )
    result = build_regions(
        anchors,
        candidates,
        {"min_anchors": 3, "target_anchors": 3, "max_anchors": 4, "boundary_threshold": 99},
    )

    covered = []
    for region in result["regions"]:
        covered.extend(range(region["anchor_start"], region["anchor_end"]))
        assert region["stats"]["anchor_count"] <= 4

    assert covered == list(range(len(anchors)))
    internal_lengths = [
        region["stats"]["anchor_count"]
        for region in result["regions"][:-1]
    ]
    assert all(length >= 3 for length in internal_lengths)


def test_live_anchor_lookup_maps_to_expected_region():
    spine = [
        {
            "spine_index": 0,
            "href": "chapter.xhtml",
            "text": "\n\n".join(f"Paragraph {i} forest." for i in range(6)),
        },
        {
            "spine_index": 1,
            "href": "next.xhtml",
            "text": "\n\n".join(f"Paragraph {i} battle." for i in range(6)),
        },
    ]
    source_units = extract_source_units(spine)
    anchors = build_anchors_from_spine(spine, target_words=3, min_words=1)
    features = extract_anchor_features(anchors)
    candidates = BoundaryProvider().score_boundaries(source_units, anchors, features, DEFAULT_REGION_CONFIG)
    regions = build_regions(anchors, candidates, DEFAULT_REGION_CONFIG)["regions"]
    timeline = {"anchors": anchors, "regions": regions}

    offset = spine[1]["text"].index("Paragraph 1")
    anchor = find_anchor_for_position(timeline, spine_index=1, local_char_offset=offset)
    region = find_region_for_anchor(timeline, anchor["anchor_id"])

    assert anchor is not None
    assert region is not None
    assert region["anchor_start"] <= anchor["anchor_id"] < region["anchor_end"]


def test_inspect_position_chain_reports_book_to_region_chain():
    spine = [
        {
            "spine_index": 0,
            "href": "chapter.xhtml",
            "text": "\n\n".join(f"Paragraph {i} forest." for i in range(6)),
        }
    ]
    source_units = extract_source_units(spine)
    anchors = build_anchors_from_spine(spine, target_words=3, min_words=1)
    features = extract_anchor_features(anchors)
    regions = build_regions(
        anchors,
        BoundaryProvider().score_boundaries(source_units, anchors, features, DEFAULT_REGION_CONFIG),
        DEFAULT_REGION_CONFIG,
    )["regions"]
    book = {
        "calibre_book_id": 1,
        "calibre_uuid": "uuid-1",
        "title": "Book",
        "authors": ["Author"],
        "preferred_epub_path": "book.epub",
        "annots_key": "abc.json",
    }
    live = LiveAnnotation("abc.json", "annots/abc.json", "epubcfi(/6/2:1)", 10.0)
    resolved = {
        "spine_index": 0,
        "href": "chapter.xhtml",
        "local_char_offset": spine[0]["text"].index("Paragraph 2"),
        "spine_text_len": len(spine[0]["text"]),
    }
    timeline = {
        "schema_version": 4,
        "book": {"epub_path": "book.epub", "annots_key": "abc.json"},
        "spine": spine,
        "source_units": source_units,
        "anchors": anchors,
        "regions": regions,
    }

    chain = inspect_position_chain(book, timeline=timeline, live=live, resolved=resolved)

    assert chain["book"]["annots_key"] == "abc.json"
    assert chain["resolved"]["href"] == "chapter.xhtml"
    assert chain["anchor"]["anchor_id"] is not None
    assert chain["region"]["active_anchor"] == chain["anchor"]["anchor_id"]


def test_timeline_drift_warnings_report_manifest_changes(tmp_path):
    old_epub = tmp_path / "old.epub"
    new_epub = tmp_path / "new.epub"
    old_epub.write_bytes(b"old")
    new_epub.write_bytes(b"new")
    book = {
        "preferred_epub_path": str(new_epub),
        "annots_key": "new.json",
    }
    timeline = {
        "book": {
            "epub_path": str(old_epub),
            "epub_hash": "not-current",
            "annots_key": "old.json",
        }
    }

    warnings = timeline_drift_warnings(book, timeline)

    assert any(w.startswith("epub_path_changed") for w in warnings)
    assert any(w.startswith("epub_hash_changed") for w in warnings)
    assert any(w.startswith("annots_key_changed") for w in warnings)


def test_chapter_boundary_reason_appears_in_deterministic_fixture():
    spine = [
        {
            "spine_index": 0,
            "href": "one.xhtml",
            "text": "one.\n\ntwo.\n\nthree.",
        },
        {
            "spine_index": 1,
            "href": "two.xhtml",
            "text": "four.\n\nfive.\n\nsix.",
        },
    ]
    source_units = extract_source_units(spine)
    anchors = build_anchors_from_spine(spine, target_words=1, min_words=1)
    features = extract_anchor_features(anchors)
    candidates = BoundaryProvider().score_boundaries(source_units, anchors, features, DEFAULT_REGION_CONFIG)
    regions = build_regions(anchors, candidates, DEFAULT_REGION_CONFIG)["regions"]

    assert any(
        "chapter_start" in region["boundary_in"]["reasons"]
        or "chapter_start" in region["boundary_out"]["reasons"]
        for region in regions
    )


def test_clean_timeline_removes_only_timeline(tmp_path, monkeypatch):
    book = fake_book(tmp_path, 46)
    data_dir = tmp_path / "data"
    book_dir = data_dir / "books" / "46"
    book_dir.mkdir(parents=True)
    target = timeline_path(data_dir, 46)
    target.write_text("{}", encoding="utf-8")
    inspect_text_path(data_dir, 46).write_text("{}", encoding="utf-8")
    unlinked = []

    def fake_unlink(path):
        unlinked.append(path)

    monkeypatch.setattr(Path, "unlink", fake_unlink)

    assert remove_timeline(data_dir, book["calibre_book_id"]) is True

    assert unlinked == [target]
    assert inspect_text_path(data_dir, 46).exists()


def test_clean_regions_preserves_anchors_features_and_removes_only_regions(tmp_path):
    book = fake_book(tmp_path, 47)

    def fake_extractor(epub_path):
        return [
            {
                "spine_index": 0,
                "href": "chapter.xhtml",
                "text": "\n\n".join(f"Paragraph {i} forest." for i in range(6)),
            },
        ]

    prepare_book_timeline(
        book,
        data_dir=tmp_path / "data",
        target_words=3,
        min_words=1,
        regions=True,
        spine_extractor=fake_extractor,
    )

    assert remove_regions_from_timeline(tmp_path / "data", 47) is True
    timeline = load_timeline(tmp_path / "data", 47)

    assert "regions" not in timeline
    assert "audio_intents" not in timeline
    assert timeline["builder"]["regions"] is False
    assert timeline["anchors"]
    assert timeline["features"]


def test_clean_inspect_text_removes_only_sidecar(tmp_path, monkeypatch):
    book = fake_book(tmp_path, 48)
    data_dir = tmp_path / "data"
    book_dir = data_dir / "books" / "48"
    book_dir.mkdir(parents=True)
    timeline_path(data_dir, 48).write_text("{}", encoding="utf-8")
    target = inspect_text_path(data_dir, 48)
    target.write_text("{}", encoding="utf-8")
    unlinked = []

    def fake_unlink(path):
        unlinked.append(path)

    monkeypatch.setattr(Path, "unlink", fake_unlink)

    assert remove_inspect_text(data_dir, book["calibre_book_id"]) is True

    assert timeline_path(data_dir, 48).exists()
    assert unlinked == [target]


def test_prepare_book_timeline_writes_audio_intents_without_verbose_diagnostics(tmp_path):
    book = fake_book(tmp_path, 54)

    def fake_extractor(epub_path):
        return [
            {
                "spine_index": 0,
                "href": "chapter.xhtml",
                "text": "\n\n".join([
                    "Rain and thunder filled the forest road.",
                    "The storm wind crossed the dark field.",
                    "The sword fight spilled blood across the hall.",
                    "They ran from the attack and shouted in fear.",
                ]),
            }
        ]

    prepare_book_timeline(
        book,
        data_dir=tmp_path / "data",
        target_words=8,
        min_words=1,
        regions=True,
        atmosphere=True,
        audio_intents=True,
        spine_extractor=fake_extractor,
    )
    timeline = load_timeline(tmp_path / "data", 54)

    assert timeline["audio_intents"]["intents"]
    assert "source_units" not in timeline
    assert "region_diagnostics" not in timeline
    assert source_units_path(tmp_path / "data", 54).exists()
    assert region_diagnostics_path(tmp_path / "data", 54).exists()


def test_audio_intents_use_neutral_fallback_for_unknown_or_unmatched_assets():
    timeline = {
        "regions": [{"region_id": 0, "anchor_start": 0, "anchor_end": 1, "stats": {"anchor_count": 1}}],
        "atmosphere": {"region_labels": [{"region_id": 0, "effective_atmosphere": "unknown", "abstained": True}]},
    }
    planned = build_audio_intents(timeline, {"schema_version": 1, "assets": []})
    intent = planned["intents"][0]

    assert intent["effective_atmosphere"] == "unknown"
    assert intent["base_bed"]["matched_category"] == "neutral"
    assert intent["suppression_reason"] == "neutral_fallback"


def test_audio_trace_simulation_suppresses_repeated_and_short_jumps():
    intents = [
        {"intent_id": "region-0", "region_id": 0, "effective_atmosphere": "unknown"},
        {"intent_id": "region-1", "region_id": 1, "effective_atmosphere": "environment:weather"},
    ]
    trace = [
        {"time_seconds": 0, "region_id": 0},
        {"time_seconds": 1, "region_id": 0},
        {"time_seconds": 2, "region_id": 1},
        {"time_seconds": 4, "region_id": 1},
        {"time_seconds": 70, "region_id": 1},
    ]

    result = simulate_reading_trace(intents, trace)

    assert result["transition_count"] == 2
    assert result["events"][1]["reason"] == "same_region"
    assert any(event["reason"] == "jump_suppression" for event in result["events"])
