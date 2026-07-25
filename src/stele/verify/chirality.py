"""Machine-check of plate chirality via the non-symmetric orientation glyph.

The costliest real-world mask error is mirrored or inverted data. The check
renders the glyph region from the GDS (hierarchically) and applies the top
cell's ACTUAL transform — derived from the file, never from the declaration —
then compares against the expected 'F' for the DECLARED orientation. A build
that wrote the wrong transform fails even though it declared the right one.
"""

from __future__ import annotations

import numpy as np

from stele.backends.preview import rasterize
from stele.layout.engine import PlatePlan
from stele.layout.fiducials import centered_orientation_glyph
from stele.verify.renderback import rasterize_cell_hierarchical, top_transform_mirrored


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    a = a > 0
    b = b > 0
    union = np.logical_or(a, b).sum()
    if union == 0:
        return 0.0
    return float(np.logical_and(a, b).sum()) / float(union)


def check_chirality(
    layout,
    plan: PlatePlan,
    glyph_height_um: float,
    mirrored_declared: bool,
    layer: int = 1,
    datatype: int = 0,
    px_per_um: float = 1.0,
) -> dict:
    rect = plan.reserved["orientation_glyph"].as_tuple()
    # render the glyph region from CONTENT (where furniture lives), then apply
    # the top transform read from the GDS
    bm = rasterize_cell_hierarchical(layout, "CONTENT", rect, px_per_um, layer, datatype)
    gds_mirrored = top_transform_mirrored(layout)
    actual = bm[:, ::-1] if gds_mirrored else bm  # as seen on the manufactured plate

    expected_polys = centered_orientation_glyph(rect, glyph_height_um)
    expected = rasterize(expected_polys, rect, px_per_um, supersample=2)
    if mirrored_declared:
        expected = expected[:, ::-1]

    iou_declared = _iou(actual, expected)
    iou_flipped = _iou(actual, expected[:, ::-1])
    return {
        "gds_transform_mirrored": gds_mirrored,
        "iou_declared": round(iou_declared, 4),
        "iou_wrong_chirality": round(iou_flipped, 4),
        "pass": bool(iou_declared > 0.5 and iou_declared > iou_flipped * 1.5),
    }
