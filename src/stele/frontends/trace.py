"""Binary bitmap -> document-space polygons (exact pixel footprint).

cv2.findContours (RETR_CCOMP) traces pixel CENTERS, so the true ink footprint
is recovered by explicit offset arithmetic per outer contour:

    footprint_i = buffer(outer_i, +0.5 px)  -  union_j buffer(hole_ij, -0.5 px)

- Outer contours grow half a pixel (contours run through boundary-pixel
  centers; ink extends 0.5 px beyond).
- Hole rings also run through INK-pixel centers around the hole, so the true
  white hole is the ring interior shrunk by 0.5 px.
- 1-px features (halftone dots, hatch lines) yield DEGENERATE outer contours
  (a point or zero-area spine); their footprint is the spine buffered with
  square caps. Dropping them loses ~80% of scanned drawing ink (measured).
- Thin ink RINGS (a 1-px 'o') yield a hole ring nearly identical to the outer
  ring; constructing Polygon(outer, holes) there goes invalid and make_valid
  can fill the counter (measured as phantom-ink defects). The difference-based
  arithmetic above handles it exactly: (ring + 0.5) - (ring - 0.5) = 1 px ring.
- Holes are subtracted per-outer only, never globally: content nested inside
  another polygon's hole (CCOMP reports it as a new outer) must survive.

No RDP simplification by default (review: tolerance must derive from
plate-scale min feature, applied at materialization).
"""

from __future__ import annotations

import cv2
import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid

from stele.ir.ops import _polygons_of, ensure_valid

DEGENERATE_AREA_PX = 1.0  # below this, treat the contour as a point/line spine


def trace_binary(ink: np.ndarray, um_per_px: float, height_px: int | None = None) -> list[Polygon]:
    """Trace a binary ink mask (True/nonzero = ink) into shapely polygons.

    Output is in document space: micrometers, y-up, exact pixel footprint.
    """
    mask = (ink > 0).astype(np.uint8)
    h = height_px if height_px is not None else mask.shape[0]
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return []
    hierarchy = hierarchy[0]  # (N, 4): [next, prev, first_child, parent]
    half_px = 0.5 * um_per_px

    out: list[Polygon] = []
    for i, cnt in enumerate(contours):
        if hierarchy[i][3] != -1:  # a hole; handled with its parent
            continue
        pts = _to_um(cnt, um_per_px, h)
        if len(pts) < 3 or abs(cv2.contourArea(cnt)) < DEGENERATE_AREA_PX:
            spine = Point(pts[0]) if len(pts) == 1 else LineString(pts)
            fp = spine.buffer(half_px, cap_style=3, join_style=2, mitre_limit=2.0)
            out.extend(_polygons_of(fp))
            continue

        ext_fp = _grown_exterior(pts, half_px)
        hole_fps = []
        child = hierarchy[i][2]
        while child != -1:
            ring = _to_um(contours[child], um_per_px, h)
            if len(ring) >= 3 and abs(cv2.contourArea(contours[child])) >= DEGENERATE_AREA_PX:
                shrunk = _shrunk_hole(ring, half_px)
                if shrunk is not None and not shrunk.is_empty:
                    hole_fps.append(shrunk)
            child = hierarchy[child][0]
        if hole_fps:
            fp = ext_fp.difference(unary_union(hole_fps))
        else:
            fp = ext_fp
        out.extend(_polygons_of(fp))
    return ensure_valid(out)


def _grown_exterior(pts: np.ndarray, half_px: float):
    poly = Polygon(pts)
    if not poly.is_valid:
        pieces = _polygons_of(make_valid(poly))
        if not pieces:
            return LineString(pts).buffer(half_px, cap_style=3, join_style=2, mitre_limit=2.0)
        return unary_union([p.buffer(half_px, join_style=2, mitre_limit=2.0) for p in pieces])
    return poly.buffer(half_px, join_style=2, mitre_limit=2.0)


def _shrunk_hole(ring: np.ndarray, half_px: float):
    poly = Polygon(ring)
    if not poly.is_valid:
        pieces = _polygons_of(make_valid(poly))
        if not pieces:
            return None
        return unary_union([p.buffer(-half_px, join_style=2, mitre_limit=2.0) for p in pieces])
    return poly.buffer(-half_px, join_style=2, mitre_limit=2.0)


def _to_um(cnt: np.ndarray, um_per_px: float, height_px: int) -> np.ndarray:
    pts = cnt.reshape(-1, 2).astype(np.float64)
    x = (pts[:, 0] + 0.5) * um_per_px
    y = (height_px - (pts[:, 1] + 0.5)) * um_per_px
    return np.column_stack([x, y])
