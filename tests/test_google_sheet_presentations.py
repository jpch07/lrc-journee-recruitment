from __future__ import annotations

import base64
from copy import deepcopy
from datetime import date
from hashlib import sha256
from io import BytesIO
import json

import pytest
from PIL import Image

from app.db import SessionLocal
from app.google_sheet_presentations import build_presentation_tabs, layout_operation
from app.management_report_payload import build_management_report_payload, load_management_report_source
from app.models import GeneralAssessment, Recruit
from app.services import create_journey


@pytest.fixture
def sample_payload():
    with SessionLocal() as db:
        first = create_journey(db, "Alpha day", date(2026, 8, 1), 1, "Test")
        second = create_journey(db, "Bravo day", date(2026, 8, 2), 1, "Test")
        first.status = second.status = "completed"
        first_recruit = Recruit(journey_id=first.id, name="Same Name", present=True)
        second_recruit = Recruit(journey_id=second.id, name="Same Name", present=True)
        absent = Recruit(journey_id=second.id, name="Absent", present=False)
        formula_name = Recruit(journey_id=first.id, name="=Formula Name", present=True)
        db.add_all([first_recruit, second_recruit, absent, formula_name])
        db.flush()
        db.add(GeneralAssessment(
            recruit_id=first_recruit.id,
            comment="=IMPORTDATA(\"https://example.test\")",
            notes='{"accessToken":"never-export","public":"https://example.test/evaluate/private-link"}',
        ))
        db.commit()
        source = load_management_report_source(db)
        payload = build_management_report_payload(source)
    return payload


@pytest.fixture
def sample_photos(sample_payload):
    option = sample_payload["profiles"]["optionsByScope"]["All completed Journees"][0]
    output = BytesIO()
    Image.new("RGB", (20, 20), "navy").save(output, format="PNG")
    raw = output.getvalue()
    return [{
        "recruitId": option["profileKey"].split(":", 1)[1],
        "journeyId": option["profileKey"].split(":", 1)[0],
        "name": option["name"],
        "preview": base64.b64encode(raw).decode("ascii"),
        "sha256": sha256(raw).hexdigest(),
    }]


def _block_rows(tab, block):
    return [
        row[block["startCol"] - 1:block["endCol"]]
        for row in tab["rows"][block["startRow"] - 1:block["endRow"]]
    ]


def test_presentations_are_first_rectangular_and_literal(sample_payload, sample_photos):
    tabs = build_presentation_tabs(sample_payload, sample_photos)
    assert [(tab["name"], tab["finalTitle"], tab["presentation"]) for tab in tabs] == [
        ("Results", "Results", "results-v1"),
        ("Recruit Profiles", "Recruit Profiles", "recruit-profiles-v1"),
    ]
    for tab in tabs:
        assert len(tab["rows"][0]) <= 128
        assert {len(row) for row in tab["rows"]} == {len(tab["rows"][0])}
        assert all(isinstance(cell, str) and not cell.startswith("=") for row in tab["rows"] for cell in row)
    assert tabs[0]["rows"][2][1] == "All completed Journees"
    assert tabs[0]["rows"][2][4] == "Overall ranking"
    assert any(cell.endswith('=IMPORTDATA("https://example.test")') for row in tabs[1]["rows"] for cell in row)
    serialized = json.dumps(tabs, ensure_ascii=False)
    assert "never-export" not in serialized
    assert "private-link" not in serialized


def test_profile_preview_store_covers_every_option_and_verifies_sha(sample_payload, sample_photos):
    profile_tab = build_presentation_tabs(sample_payload, sample_photos)[1]
    preview_rows = _block_rows(profile_tab, profile_tab["layout"]["blocks"]["previews"])[1:]
    grouped = {}
    for profile_key, part, count, digest, chunk in preview_rows:
        grouped.setdefault(profile_key, []).append((int(part), int(count), digest, chunk))
    expected = {
        option["profileKey"]
        for option in sample_payload["profiles"]["optionsByScope"]["All completed Journees"]
    }
    assert set(grouped) == expected
    assert profile_tab["layout"]["expectedPreviewCount"] == len(expected)
    for chunks in grouped.values():
        chunks.sort()
        assert [item[0] for item in chunks] == list(range(len(chunks)))
        assert all(item[1] == len(chunks) for item in chunks)
        png = base64.b64decode("".join(item[3] for item in chunks))
        assert all(item[2] == sha256(png).hexdigest() for item in chunks)
        with Image.open(BytesIO(png)) as image:
            assert image.format == "PNG"


def test_duplicate_display_labels_keep_distinct_stable_profile_keys(sample_payload, sample_photos):
    profile_tab = build_presentation_tabs(sample_payload, sample_photos)[1]
    option_rows = _block_rows(profile_tab, profile_tab["layout"]["blocks"]["profileOptions"])[1:]
    same_name = [row for row in option_rows if row[1].startswith("Same Name")]
    assert len({row[1] for row in same_name}) == 2
    assert len({row[2] for row in same_name}) == 2


def test_layout_operation_contains_only_bounded_template_parameters(sample_payload, sample_photos):
    for tab in build_presentation_tabs(sample_payload, sample_photos):
        operation = layout_operation(tab)
        encoded = json.dumps(operation, ensure_ascii=False)
        assert operation["kind"] == "layout"
        assert operation["version"] == 1
        assert "Same Name" not in encoded
        assert "IMPORTDATA" not in encoded


def test_formula_like_selector_labels_use_the_same_literal_lookup_key(sample_payload, sample_photos):
    profile_tab = build_presentation_tabs(sample_payload, sample_photos)[1]
    blocks = profile_tab["layout"]["blocks"]
    option_rows = _block_rows(profile_tab, blocks["profileOptions"])[1:]
    summary_rows = _block_rows(profile_tab, blocks["summaries"])[1:]
    formula_option = next(row for row in option_rows if row[1].endswith("=Formula Name"))
    scope, label, profile_key = formula_option
    assert any(
        row[0] == f"{scope}|{label}" and row[1] == profile_key
        for row in summary_rows
    )

    payload = deepcopy(sample_payload)
    old_scope = payload["results"]["scopes"][0]
    payload["results"]["scopes"][0] = "=Completed scope"
    for row in payload["results"]["rows"]:
        if row["scope"] == old_scope:
            row["scope"] = "=Completed scope"
    results_tab = build_presentation_tabs(payload, sample_photos)[0]
    results_block = _block_rows(results_tab, results_tab["layout"]["blocks"]["results"])[1:]
    guarded_scope = results_tab["rows"][2][1]
    selected_view = results_tab["rows"][2][4]
    assert guarded_scope.endswith("=Completed scope")
    assert any(row[12] == f"{guarded_scope}|{selected_view}|1" for row in results_block)


@pytest.mark.parametrize("factor_count", [0, 2, 3])
def test_profile_summary_width_tracks_dynamic_factor_count(sample_payload, sample_photos, factor_count):
    payload = deepcopy(sample_payload)
    factors = payload["profiles"]["generalFactors"][:factor_count]
    payload["profiles"]["generalFactors"] = factors
    payload["definition"]["generalFactors"] = deepcopy(factors)
    profile_tab = build_presentation_tabs(payload, sample_photos)[1]
    summary = profile_tab["layout"]["blocks"]["summaries"]
    assert summary["endCol"] - summary["startCol"] + 1 == 21 + factor_count


def test_layout_carries_bounded_configured_performance_band_styles(sample_payload, sample_photos):
    payload = deepcopy(sample_payload)
    payload["definition"]["bands"] = [
        {"key": "needs_review", "name": "Needs review", "minimum": 0, "color": "#dc2626"},
        {"key": "strong", "name": "Strong", "minimum": 80, "color": "#16a34a"},
    ]
    tabs = build_presentation_tabs(payload, sample_photos)
    for tab in tabs:
        assert tab["layout"]["bandStyles"] == [
            {"label": "Needs review", "background": "#DC2626", "font": "#FFFFFF"},
            {"label": "Strong", "background": "#16A34A", "font": "#FFFFFF"},
        ]
