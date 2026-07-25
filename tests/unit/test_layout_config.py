"""Layout planning + cross-profile validation."""

import pytest

from stele.config.profiles import ContentPolicy, FabProfile, LayoutSpec, Profiles, ReaderProfile
from stele.config.validate import validate_profiles
from stele.ingest.normalize import PageJob
from stele.layout.engine import OverflowError_, plan_plate


def _profiles(**layout_kw) -> Profiles:
    return Profiles(
        fab=FabProfile(name="t"),
        reader=ReaderProfile(name="t"),
        content=ContentPolicy(name="t"),
        layout=LayoutSpec(name="t", **layout_kw),
    )


def _fake_jobs(n: int) -> list[PageJob]:
    return [
        PageJob(
            pdf_path="x.pdf",
            pdf_sha256="0" * 64,
            page_index=i,
            ordinal=i,
            mediabox_pt=(0, 0, 612, 792),
            cropbox_pt=(0, 0, 612, 792),
            rotation_deg=0,
            frame_pt=(612.0, 792.0),
        )
        for i in range(n)
    ]


def test_capacity_matches_review_math():
    p = _profiles()
    plan = plan_plate(p.fab, p.layout, [])
    assert plan.theoretical_slots == 3864  # 69 x 56 grid on 146.4 mm active
    assert plan.capacity() == 3723  # after title band + fiducials + glyph


def test_no_slot_intersects_reserved():
    p = _profiles()
    plan = plan_plate(p.fab, p.layout, [])
    for slot in plan.slots:
        for r in plan.reserved.values():
            assert not slot.intersects(r)


def test_slots_inside_active():
    p = _profiles()
    plan = plan_plate(p.fab, p.layout, [])
    a = plan.active
    for s in plan.slots:
        assert s.x0 >= a.x0 and s.y0 >= a.y0 and s.x1 <= a.x1 and s.y1 <= a.y1


def test_overflow_raises_with_clear_message():
    p = _profiles()
    fab = FabProfile(name="tiny", plate_width_mm=20, plate_height_mm=20)
    with pytest.raises(OverflowError_, match="exceed usable plate capacity"):
        plan_plate(fab, p.layout, _fake_jobs(100))


def test_placement_content_fits_slot():
    p = _profiles()
    plan = plan_plate(p.fab, p.layout, _fake_jobs(10))
    for pl in plan.placements:
        c = pl.content_rect()
        s = pl.slot
        assert c.x0 >= s.x0 - 1e-6 and c.y0 >= s.y0 - 1e-6
        assert c.x1 <= s.x1 + 1e-6 and c.y1 <= s.y1 + 1e-6


def test_pitch_smaller_than_pseudopage_rejected():
    with pytest.raises(ValueError, match="overlap"):
        LayoutSpec(name="bad", pitch_x_um=1000.0)


def test_legibility_warning_fires():
    p = Profiles(
        fab=FabProfile(name="t"),
        reader=ReaderProfile(name="t"),
        content=ContentPolicy(name="t", expected_min_text_pt=5.0),
        layout=LayoutSpec(name="t"),
    )
    r = validate_profiles(p)
    assert r.ok
    assert any("below the reader" in w for w in r.warnings)
    assert any("1.15" in w and "1.42" in w for w in r.warnings)  # both numbers stated


def test_impossible_plate_errors():
    p = Profiles(
        fab=FabProfile(name="t", plate_width_mm=2.0, plate_height_mm=2.0),
        reader=ReaderProfile(name="t"),
        content=ContentPolicy(name="t"),
        layout=LayoutSpec(name="t"),
    )
    r = validate_profiles(p)
    assert not r.ok
