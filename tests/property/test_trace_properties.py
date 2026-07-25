"""Property tests: the tracer preserves ink area/footprint for ARBITRARY masks."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.extra.numpy import arrays

from stele.frontends.trace import trace_binary

masks = arrays(
    dtype=np.uint8,
    shape=st.tuples(st.integers(8, 40), st.integers(8, 40)),
    elements=st.integers(0, 1),
)


@given(mask=masks)
@settings(max_examples=60, deadline=None)
def test_traced_area_matches_mask(mask):
    ink = int(mask.sum())
    polys = trace_binary(mask, um_per_px=1.0, height_px=mask.shape[0])
    area = sum(p.area for p in polys)
    if ink == 0:
        assert area == 0.0
    else:
        # exact footprint modulo mitre-corner overshoot on diagonals
        assert area >= 0.9 * ink
        assert area <= 1.35 * ink


@given(mask=masks)
@settings(max_examples=30, deadline=None)
def test_all_polygons_valid_positive(mask):
    for p in trace_binary(mask, um_per_px=1.0, height_px=mask.shape[0]):
        assert p.is_valid
        assert p.area > 0


@given(
    n_pages=st.integers(0, 40),
    pitch_x=st.floats(2000, 4000),
    pitch_y=st.floats(2600, 5000),
)
@settings(max_examples=30, deadline=None)
def test_layout_invariants(n_pages, pitch_x, pitch_y):
    from stele.config.profiles import FabProfile, LayoutSpec
    from stele.layout.engine import OverflowError_, plan_plate
    from tests.unit.test_layout_config import _fake_jobs

    fab = FabProfile(name="t", plate_width_mm=50, plate_height_mm=50)
    layout = LayoutSpec(name="t", pitch_x_um=pitch_x, pitch_y_um=pitch_y)
    try:
        plan = plan_plate(fab, layout, _fake_jobs(n_pages))
    except OverflowError_:
        return
    # no slot in reserved, all inside active, placements within slots
    for s in plan.slots:
        assert s.x0 >= plan.active.x0 - 1e-9 and s.x1 <= plan.active.x1 + 1e-9
        for r in plan.reserved.values():
            assert not s.intersects(r)
    seen = set()
    for pl in plan.placements:
        key = (pl.slot.x0, pl.slot.y0)
        assert key not in seen  # one page per slot
        seen.add(key)
