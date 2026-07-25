"""Tracer: exact pixel footprint, degenerate features, hole nesting."""

import numpy as np
import pytest

from stele.backends.preview import rasterize
from stele.frontends.trace import trace_binary
from stele.ir.ops import rings_of


def _roundtrip_iou(mask: np.ndarray, um_per_px: float = 1.0) -> float:
    """Trace a mask, rasterize the polygons back at 1 px/px, compare."""
    polys = trace_binary(mask.astype(np.uint8), um_per_px, height_px=mask.shape[0])
    if not polys:
        return 1.0 if mask.sum() == 0 else 0.0
    # resolve holes for the rasterizer via per-polygon rings difference
    from stele.backends.gdsii import shapely_to_gdstk

    gd = shapely_to_gdstk(polys)
    arrs = [p.points for p in gd]
    h, w = mask.shape
    render = rasterize(arrs, (0.0, 0.0, float(w), float(h)), 1.0, supersample=4)
    a = render > 0
    b = mask > 0
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum()) / union if union else 1.0


def test_single_pixel_dot():
    mask = np.zeros((9, 9), dtype=np.uint8)
    mask[4, 4] = 1
    polys = trace_binary(mask, 1.0)
    assert len(polys) == 1
    assert polys[0].area == pytest.approx(1.0, rel=0.05)


def test_one_px_line():
    mask = np.zeros((9, 20), dtype=np.uint8)
    mask[4, 2:18] = 1
    polys = trace_binary(mask, 1.0)
    assert sum(p.area for p in polys) == pytest.approx(16.0, rel=0.1)


def test_thick_bar_exact_footprint():
    mask = np.zeros((20, 30), dtype=np.uint8)
    mask[5:15, 4:26] = 1
    polys = trace_binary(mask, 1.0)
    assert sum(p.area for p in polys) == pytest.approx(10 * 22, rel=0.02)
    assert _roundtrip_iou(mask) > 0.97


def test_donut_with_dot_in_hole():
    """CCOMP nesting: a dot inside a hole must survive (per-outer subtraction)."""
    mask = np.zeros((40, 40), dtype=np.uint8)
    yy, xx = np.mgrid[0:40, 0:40]
    r = np.hypot(yy - 20, xx - 20)
    mask[(r < 15)] = 1
    mask[(r < 9)] = 0  # hole
    mask[(r < 3)] = 1  # dot inside the hole
    polys = trace_binary(mask, 1.0)
    assert sum(p.area for p in polys) == pytest.approx(float(mask.sum()), rel=0.06)
    assert _roundtrip_iou(mask) > 0.93


def test_thin_ring_keeps_counter_open():
    """1-px ink ring: hole ring ~= outer ring; the counter must NOT fill."""
    mask = np.zeros((16, 16), dtype=np.uint8)
    mask[4, 4:12] = 1
    mask[11, 4:12] = 1
    mask[4:12, 4] = 1
    mask[4:12, 11] = 1
    polys = trace_binary(mask, 1.0)
    area = sum(p.area for p in polys)
    assert area == pytest.approx(float(mask.sum()), rel=0.15)
    assert area < 40  # a filled 8x8 block would be 64


def test_checkerboard_roundtrip():
    """KNOWN LIMITATION: a 1-px checkerboard is 8-corner-connected, so cv2
    chains it into spine contours and the buffered footprint over-covers (up
    to ~1.8x). Dither geometry is GENERATED, never traced, so this pathology
    stays out of the real pipeline; this test documents the bound."""
    mask = (np.indices((24, 24)).sum(axis=0) % 2).astype(np.uint8)
    polys = trace_binary(mask, 1.0)
    area = sum(p.area for p in polys)
    assert area >= float(mask.sum())  # never lose ink
    assert area <= 1.8 * float(mask.sum())  # bounded over-coverage


def test_rings_of_reports_holes():
    mask = np.zeros((30, 30), dtype=np.uint8)
    mask[5:25, 5:25] = 1
    mask[12:18, 12:18] = 0
    polys = trace_binary(mask, 1.0)
    outers, holes = rings_of(polys)
    assert len(outers) >= 1
    assert len(holes) >= 1
