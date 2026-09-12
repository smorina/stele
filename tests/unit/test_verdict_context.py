"""The verdict explains the common 'errors that are not build defects':
font substitution on non-embedded fonts, small-text rule violations, and a
title shrunk to fit — without weakening the authoritative status."""

from __future__ import annotations

from stele.ui.verdict import summarize_report


def _report(**overrides):
    plate = {
        "verify": {
            "gates": {
                "status": "fail",
                "worst_defect_mismatch": 0.001,
                "structural_defects": 2,
                "component_failures": 1,
                "placements_expected": 1,
                "placements_found": 1,
                "chirality": True,
                "stroke_floor_failing_pages": [],
                "width_violations": 33,
                "space_violations": 132,
                "content_pass": False,
                "geometry_pass": False,
            },
            "pages": {
                "PAGE_0000": {
                    "structural_defects": 2,
                    "component_failures": 1,
                    "stroke_widths_um": {"min_um": 0.5, "p05_um": 2.5},
                }
            },
        },
        "placements": [{"cell": "PAGE_0000", "scale": 1 / 109.1}],
        "ingest": [{"page": 1, "fonts_not_embedded": ["Helvetica"]}],
        "furniture": {
            "polarity": "dark_field",
            "title": {"shrunk_to_fit": True, "height_um": 2447.2, "requested_height_um": 2500.0},
        },
    }
    plate.update(overrides)
    return {
        "plate_set": {"status": "fail", "plates": 1},
        "plates": [plate],
        "config": {"profiles": {"fab": {"min_feature_um": 1.0}}},
        "orientation": {"polarity": "dark_field", "mirrored": False,
                        "tone_truth_table": ["a", "b", "c", "d"]},
        "validation": {"warnings": [], "unenforced_fields": []},
    }


def test_font_substitution_note_follows_the_content_errors_and_keeps_fail():
    result = summarize_report(_report())
    assert result["status"] == "fail"
    levels = [f["level"] for f in result["findings"]]
    assert levels[:2] == ["error", "error"]
    note = next(f for f in result["findings"] if "not embedded" in f["text"])
    assert "Helvetica" in note["text"] and note["level"] == "warning"
    assert "embedded fonts" in note["action"]
    assert result["fonts_not_embedded"] == ["Helvetica"]
    assert result["polarity"] == "dark_field" and len(result["tone_truth_table"]) == 4


def test_stroke_context_explains_small_text_rule_violations():
    result = summarize_report(_report())
    finding = next(f for f in result["findings"] if "spacing rule" in f["text"])
    assert "0.50 µm" in finding["text"] and "1 µm writer minimum" in finding["text"]
    assert "109:1" in finding["text"] and "below about 4 pt" in finding["text"]


def test_title_shrink_is_reported():
    result = summarize_report(_report())
    finding = next(f for f in result["findings"] if "title was etched" in f["text"])
    assert "2.45 mm" in finding["text"] and "2.50 mm" in finding["text"]


def test_no_font_note_when_fonts_are_embedded_or_content_passes():
    clean_fonts = _report(ingest=[{"page": 1, "fonts_not_embedded": []}])
    assert not any("not embedded" in f["text"] for f in summarize_report(clean_fonts)["findings"])
    report = _report()
    report["plates"][0]["verify"]["gates"].update(
        {"structural_defects": 0, "component_failures": 0, "content_pass": True,
         "status": "pass_with_warnings"}
    )
    report["plates"][0]["verify"]["pages"]["PAGE_0000"].update(
        {"structural_defects": 0, "component_failures": 0}
    )
    report["plate_set"]["status"] = "pass_with_warnings"
    findings = summarize_report(report)["findings"]
    assert not any("not embedded" in f["text"] for f in findings)
    assert any("spacing rule" in f["text"] for f in findings)
