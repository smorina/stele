"""Plate furniture placement: title alignment/fit, guide-band text clear of the
corner marks, labeled scale bar, dark-field polarity, and the validators that
protect them."""

from __future__ import annotations

import pytest

from stele.config.profiles import ContentPolicy, FabProfile, LayoutSpec, Profiles, ReaderProfile
from stele.config.validate import validate_profiles
from stele.layout.engine import Rect, band_text_area, plan_plate
from stele.layout.fiducials import measure_text_width, place_title, stroke_text
from stele.layout.navigation import (
    block_height,
    fit_char_height,
    labeled_scale_bar,
    text_block_aligned,
)


def _plan(**layout_kw):
    fab = FabProfile(name="t")
    layout = LayoutSpec(name="t", **layout_kw)
    return plan_plate(fab, layout, []), fab, layout


def _bbox(polys) -> Rect:
    xs0, ys0, xs1, ys1 = zip(*(p.bounds for p in polys))
    return Rect(min(xs0), min(ys0), max(xs1), max(ys1))


def test_band_text_area_excludes_corner_furniture():
    plan, _, _ = _plan(nav_band_um=5000.0)
    for band in ("title_band", "nav_band"):
        area = band_text_area(plan, band)
        assert area is not None
        rect = plan.reserved[band]
        assert area.y0 == rect.y0 and area.y1 == rect.y1
        for key, other in plan.reserved.items():
            if key != band:
                assert not area.intersects(other), (band, key)
    # the glyph pushes the guide band's span further right than the fiducial alone
    assert band_text_area(plan, "nav_band").x0 > plan.reserved["orientation_glyph"].x1
    assert band_text_area(plan, "missing") is None


def test_title_is_centered_between_fiducials_and_inside_the_band():
    plan, _, layout = _plan()
    area = band_text_area(plan, "title_band")
    polys, record = place_title("STELE-0001", layout.title_height_um, area.as_tuple(), "center")
    bb = _bbox(polys)
    band = plan.reserved["title_band"]
    assert band.x0 <= bb.x0 and bb.x1 <= band.x1 and band.y0 <= bb.y0 and bb.y1 <= band.y1
    plate_center = plan.plate_w_um / 2
    assert abs((bb.x0 + bb.x1) / 2 - plate_center) < 5.0
    assert abs((bb.y0 + bb.y1) / 2 - (band.y0 + band.y1) / 2) < 5.0
    assert abs((bb.y1 - bb.y0) - layout.title_height_um) < 1.0
    assert not record["shrunk_to_fit"]
    for key in ("fiducial_nw", "fiducial_ne"):
        assert not bb.intersects(plan.reserved[key])


@pytest.mark.parametrize("align", ["left", "right"])
def test_title_alignment_hugs_the_free_span(align):
    plan, _, layout = _plan()
    area = band_text_area(plan, "title_band")
    polys, _ = place_title("ARCHIVE", layout.title_height_um, area.as_tuple(), align)
    bb = _bbox(polys)
    if align == "left":
        assert abs(bb.x0 - area.x0) < 2.0
    else:
        assert abs(bb.x1 - area.x1) < 2.0
    assert bb.x0 >= plan.reserved["fiducial_nw"].x1
    assert bb.x1 <= plan.reserved["fiducial_ne"].x0


def test_overlong_title_is_shrunk_never_clipped():
    plan, _, layout = _plan()
    area = band_text_area(plan, "title_band")
    text = "A TITLE FAR TOO LONG FOR THE SPAN BETWEEN THE CORNER FIDUCIALS OF A SIX INCH PLATE " * 2
    polys, record = place_title(text, layout.title_height_um, area.as_tuple(), "center")
    bb = _bbox(polys)
    assert record["shrunk_to_fit"]
    assert record["height_um"] < layout.title_height_um
    assert bb.x0 >= area.x0 - 1.0 and bb.x1 <= area.x1 + 1.0
    assert (bb.y1 - bb.y0) < layout.title_height_um


def test_measure_text_width_scales_linearly_and_matches_stroke_text():
    w1 = measure_text_width("PLATE 1 OF 3", 600.0)
    w2 = measure_text_width("PLATE 1 OF 3", 1200.0)
    assert abs(w2 - 2 * w1) < 1e-6
    _, measured = stroke_text("PLATE 1 OF 3", 600.0, (0.0, 0.0))
    assert abs(measured - w1) < 1e-6
    assert measure_text_width("", 600.0) == 0.0


def test_guide_band_text_block_is_centered_and_clear_of_marks():
    plan, _, _ = _plan(nav_band_um=5000.0)
    area = band_text_area(plan, "nav_band")
    lines = ["ARCHIVE  PLATE 1 OF 1", "PAGES 1-40  READ ROWS L-R, TOP DOWN", "+ BOOK.PDF", "Made by hand"]
    text_area = (area.x0 + 200, area.y0 + 200, area.x1 - 2500, area.y1 - 200)
    char_h = fit_char_height(lines, text_area, 600.0)
    polys, record = text_block_aligned(lines, text_area, char_h, "center")
    bb = _bbox(polys)
    assert text_area[0] <= bb.x0 and bb.x1 <= text_area[2]
    assert text_area[1] <= bb.y0 and bb.y1 <= text_area[3]
    mid = (text_area[1] + text_area[3]) / 2
    assert abs((bb.y0 + bb.y1) / 2 - mid) < char_h * 0.25
    assert abs(bb.y1 - bb.y0 - block_height(len(lines), char_h)) < 2.0
    for key in ("fiducial_sw", "fiducial_se", "orientation_glyph"):
        assert not bb.intersects(plan.reserved[key]), key
    assert len(record["lines"]) == len(lines)


def test_fit_char_height_respects_width_and_height():
    lines = ["SHORT", "A MUCH LONGER LINE OF GUIDE BAND TEXT THAT MUST SHRINK"]
    narrow = (0.0, 0.0, 5000.0, 5000.0)
    tall_wide = (0.0, 0.0, 100000.0, 5000.0)
    h_narrow = fit_char_height(lines, narrow, 600.0)
    h_wide = fit_char_height(lines, tall_wide, 600.0)
    assert h_wide == 600.0
    assert h_narrow < 600.0
    assert measure_text_width(lines[1], h_narrow) <= 5000.0 + 1e-6
    assert fit_char_height(["x"] * 10, (0, 0, 1e6, 1000.0), 600.0) < 100.0


def test_labeled_scale_bar_has_label_to_the_right():
    rings, label, width = labeled_scale_bar(1000.0, 500.0, 1000.0, 100.0, label_height_um=300.0)
    assert rings and label
    label_bb = _bbox(label)
    assert label_bb.x0 >= 2000.0 + 100.0
    assert abs(label_bb.y0 - 500.0) < 1.0
    assert width > 1000.0 + 150.0


def test_layout_spec_new_fields_validate():
    spec = LayoutSpec(name="t", polarity="dark_field", title_align="right", nav_text="a\nb")
    assert spec.polarity == "dark_field"
    with pytest.raises(ValueError):
        LayoutSpec(name="t", polarity="negative")
    with pytest.raises(ValueError):
        LayoutSpec(name="t", title_align="justify")
    with pytest.raises(ValueError, match="tier_scales"):
        LayoutSpec(name="t", tier_scales=[])
    with pytest.raises(ValueError):
        LayoutSpec(name="t", nav_text_height_um=-1.0)


def test_validation_catches_band_budget_and_reader_ranges():
    def profiles(fab_kw=None, layout_kw=None, reader_kw=None):
        return Profiles(
            fab=FabProfile(name="t", **(fab_kw or {})),
            reader=ReaderProfile(name="t", **(reader_kw or {})),
            content=ContentPolicy(name="t"),
            layout=LayoutSpec(name="t", **(layout_kw or {})),
        )

    r = validate_profiles(profiles(layout_kw={"title_band_um": 80000.0, "nav_band_um": 70000.0}))
    assert any("leave no room for a page row" in e for e in r.errors)
    r = validate_profiles(profiles(fab_kw={"edge_exclusion_mm": 80.0}))
    assert any("leaves no active area" in e for e in r.errors)
    r = validate_profiles(profiles(reader_kw={"numerical_aperture": 3.0}))
    assert any("not physical" in e for e in r.errors)
    r = validate_profiles(profiles(fab_kw={"density_min": 0.5, "density_max": 0.2}))
    assert any("density bounds" in e for e in r.errors)
    ok = validate_profiles(profiles(layout_kw={"polarity": "dark_field"}))
    assert ok.ok
    assert any("clear aperture coverage" in line for line in ok.info)
    assert any("exposes only the glyph areas" in line for line in ok.info)
