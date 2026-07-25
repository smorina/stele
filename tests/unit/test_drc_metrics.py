"""XOR metric and DRC semantics, including the progress-review probes that
exposed false-negative regions in the previous implementations."""

import klayout.db as kdb
import numpy as np

from stele.verify.drc import klayout_width_space
from stele.verify.xordiff import MIN_DEFECT_PX, hysteresis_compare, xor_compare

UM_PER_PX = 0.5


def _canvas() -> np.ndarray:
    return np.zeros((400, 400), dtype=np.uint8)


# --------------- XOR band-tolerance metric ---------------


def test_xor_identical_is_zero():
    img = _canvas()
    img[50:100, 50:300] = 255
    r = xor_compare(img, img)
    assert r.raw_mismatch == 0.0
    assert r.defect_mismatch == 0.0
    assert r.structural_defects == 0
    assert not r.page_fail


def test_xor_edge_fuzz_absorbed():
    ref = _canvas()
    ref[50:100, 50:300] = 255
    cand = np.roll(ref, 1, axis=1)  # 1-px misregistration
    r = xor_compare(cand, ref)
    assert r.raw_mismatch > 0.0
    assert r.defect_mismatch == 0.0
    assert r.structural_defects == 0


def test_deleted_writer_floor_stroke_detected():
    """THE review probe: a deleted 1.0 um stroke is 2 px wide at this grid and
    vanished under the old erosion tolerance. It must be a defect."""
    ref = _canvas()
    ref[100:102, 50:350] = 255  # 1.0 um x 150 um stroke
    cand = _canvas()  # deleted entirely
    r = xor_compare(cand, ref)
    assert r.structural_defects >= 1
    assert r.defect_mismatch > 0.5


def test_severely_thinned_stroke_detected():
    """6 px stroke thinned to 1 px: the lost rim exceeds the 1-px band."""
    ref = _canvas()
    ref[100:106, 50:350] = 255
    cand = _canvas()
    cand[102:103, 50:350] = 255
    r = xor_compare(cand, ref)
    assert r.structural_defects >= 1


def test_xor_dropped_glyph_detected():
    ref = _canvas()
    ref[50:80, 50:80] = 255
    r = xor_compare(_canvas(), ref)
    assert r.structural_defects >= 1
    assert r.component_failures >= 1


def test_shape_mismatch_fails():
    ref = _canvas()
    cand = np.zeros((300, 400), dtype=np.uint8)  # 100 px shorter
    r = xor_compare(cand, ref)
    assert r.shape_mismatch
    assert r.page_fail


def test_component_iou_catches_local_distortion():
    ref = _canvas()
    ref[50:90, 50:90] = 255  # 40x40 block
    cand = _canvas()
    cand[50:90, 50:70] = 255  # right half missing
    r = xor_compare(cand, ref)
    assert r.component_failures >= 1


def test_hysteresis_near_threshold_not_a_defect():
    """Reference ink that is only WEAKLY dark (mid but not strict): missing it
    is reported in raw mismatch but is not a gated defect."""
    strict = _canvas().astype(bool)
    mid = _canvas().astype(bool)
    loose = _canvas().astype(bool)
    mid[50:90, 50:90] = True
    loose[50:90, 50:90] = True
    r = hysteresis_compare(_canvas(), strict, mid, loose)
    assert r.structural_defects == 0
    assert r.raw_mismatch > 0.0


def test_hysteresis_phantom_ink_is_a_defect():
    cand = _canvas()
    cand[200:240, 200:240] = 255  # ink where the reference is clearly white
    empty = _canvas().astype(bool)
    r = hysteresis_compare(cand, empty, empty, empty)
    assert r.structural_defects >= 1


def test_min_defect_px_boundary():
    strict = _canvas().astype(bool)
    side = 4  # 16 px < MIN_DEFECT_PX and < IOU_MIN_COMPONENT_PX
    strict[50 : 50 + side, 50 : 50 + side] = True
    r = hysteresis_compare(_canvas(), strict, strict, strict)
    assert side * side < MIN_DEFECT_PX
    assert r.structural_defects == 0


# --------------- klayout-engine DRC ---------------


def _layout_with(boxes_um: list[tuple[float, float, float, float]]) -> kdb.Layout:
    lay = kdb.Layout()
    lay.dbu = 0.001
    cell = lay.create_cell("T")
    li = lay.layer(1, 0)
    for x0, y0, x1, y1 in boxes_um:
        cell.shapes(li).insert(
            kdb.Box(int(x0 * 1000), int(y0 * 1000), int(x1 * 1000), int(y1 * 1000))
        )
    return lay


def _check(boxes, min_w=1.0, min_s=1.0) -> dict:
    return klayout_width_space(_layout_with(boxes), "T", 1, 0, min_w, min_s)


def test_drc_sliver_flagged_bar_clean():
    r = _check([(0, 0, 100, 0.5), (0, 10, 100, 13)])  # 0.5 um sliver + 3 um bar
    assert r["width_violations"] >= 1
    r2 = _check([(0, 10, 100, 13)])
    assert r2["width_violations"] == 0


def test_drc_thin_appendage_flagged():
    """Review probe: a sub-width appendage attached to a printable stroke was
    invisible to the old component-level check. klayout flags it."""
    r = _check([(0, 0, 10, 10), (10, 4, 18, 4.5)])  # 0.5 um serif on a 10 um block
    assert r["width_violations"] >= 1


def test_drc_open_channel_flagged():
    """Review probe: a 0.5 um OPEN gap (background touches the outside) was
    invisible to the old border-excluded space check."""
    r = _check([(0, 0, 10, 30), (10.5, 0, 20, 30)])
    assert r["space_violations"] >= 1


def test_drc_enclosed_slit_flagged():
    r = _check([(0, 0, 20, 9.75), (0, 10.25, 20, 20), (0, 0, 1, 20), (19, 0, 20, 20)])
    assert r["space_violations"] >= 1


def test_drc_wide_spacing_clean():
    r = _check([(0, 0, 10, 10), (20, 0, 30, 10)])  # 10 um apart
    assert r["space_violations"] == 0
    assert r["width_violations"] == 0
