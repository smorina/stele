"""Rasterizer compositing: no phantom geometry, no inter-contour interaction.

Regression tests for the PAGE_0000 phantom-bar defect: axis-aligned
rectangles lying entirely outside the viewport left a negative numpy slice
stop, which Python wrapped to the far canvas edge — full-height phantom
bars through the simulate/preview renders (the GDS itself was clean).
"""

import os

import numpy as np
import pytest

from stele.backends.preview import rasterize

BBOX = (0.0, 0.0, 100.0, 100.0)
PX = 2.0


def _rect(x0, y0, x1, y1):
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)


def _tri(x0, y0, size):
    return np.array([[x0, y0], [x0 + size, y0], [x0, y0 + size]], dtype=float)


@pytest.mark.parametrize(
    "rect",
    [
        _rect(40.0, 120.0, 45.0, 130.0),  # above the viewport
        _rect(40.0, -30.0, 45.0, -20.0),  # below
        _rect(-30.0, 40.0, -20.0, 45.0),  # left
        _rect(120.0, 40.0, 130.0, 45.0),  # right
        _rect(40.0, -30.0, 45.0, -0.0),  # touching the bottom edge
    ],
)
def test_off_viewport_rect_paints_nothing(rect):
    arr = rasterize([rect], BBOX, PX)
    assert arr.sum() == 0


def test_off_viewport_rect_does_not_corrupt_visible_ink():
    """The original defect shape: one visible glyph plus one rectangle fully
    outside the viewport must render exactly like the glyph alone."""
    glyph = _tri(20.0, 20.0, 10.0)
    off = _rect(40.0, 120.0, 45.0, 130.0)
    alone = rasterize([glyph], BBOX, PX)
    together = rasterize([glyph, off], BBOX, PX)
    assert alone.sum() > 0
    assert np.array_equal(alone, together)


def test_multi_polygon_render_equals_per_polygon_union():
    """Compositing must be paint-over (OR): overlapping contours in one list
    may not cancel each other (even-odd XOR), and no polygon pair may bridge."""
    polys = [
        _tri(10.0, 10.0, 30.0),
        _tri(25.0, 15.0, 30.0),  # overlaps the first triangle
        _rect(60.0, 60.0, 80.0, 82.0),
        _tri(58.0, 55.0, 20.0),  # overlaps the rectangle
        _rect(5.0, 70.0, 6.0, 71.0),  # isolated 1 um dither site
    ]
    batch = rasterize(polys, BBOX, PX)
    union = np.zeros_like(batch)
    for p in polys:
        union |= rasterize([p], BBOX, PX)
    assert union.sum() > 0
    assert np.array_equal(batch, union)


def test_page0000_crop_has_no_inter_contour_bridges():
    """The defect as observed: the default `stele simulate` crop of PAGE_0000
    must match a strict one-polygon-at-a-time union of the same GDS content."""
    gds = os.path.join(os.path.dirname(__file__), "..", "..", "out", "patent_demo.gds")
    if not os.path.exists(gds):
        pytest.skip("demo artifact out/patent_demo.gds not built")
    from stele.verify.renderback import (
        open_layout,
        rasterize_cell_hierarchical,
        read_polygons_um,
    )

    layout = open_layout(gds)
    cell = layout.cell("PAGE_0000")
    assert cell is not None
    b = cell.dbbox()
    w, h = 400.0, 300.0
    x0 = (b.left + b.right) / 2 - w / 2
    y0 = (b.bottom + b.top) / 2 - h / 2
    bbox = (x0, y0, x0 + w, y0 + h)
    px_per_um = 10.0

    rendered = rasterize_cell_hierarchical(layout, "PAGE_0000", bbox, px_per_um)
    polys, _ = read_polygons_um(layout, cell_name="PAGE_0000")
    union = np.zeros_like(rendered)
    for p in polys:
        union |= rasterize([p], bbox, px_per_um, supersample=2)
    assert rendered.sum() > 0
    assert np.array_equal(rendered, union)
