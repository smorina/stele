"""PNG preview renderer for plate-scale polygons (the tool's UX).

Supersampled fill via cv2.fillPoly, one call per polygon: independent
paint-over compositing matches physical ink, whereas a single batched call
fills all contours with the even-odd rule, so overlapping contours XOR into
holes. Per-polygon calls stay in C++ and measure ~1.5x a batched fill at
50k polygons (PIL's per-polygon draw loop takes minutes on dithered pages
of ~10^6 polygons; axis-aligned rectangles — all dither geometry — bypass
fillPoly entirely via exact numpy fills). Input polygons must already be
hole-free (post gdstk boolean), which is what the GDS backend and klayout
read-back both produce.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

SUPERSAMPLE = 4
_FP_SHIFT = 4  # fillPoly fixed-point bits: 1/16 px sub-pixel accuracy


def rasterize(
    polygons_um: list[np.ndarray],
    bbox_um: tuple[float, float, float, float],
    px_per_um: float,
    invert: bool = False,
    supersample: int = SUPERSAMPLE,
) -> np.ndarray:
    """Render polygons (plate-space um, y-up) to a binary uint8 array (255 = feature)."""
    xmin, ymin, xmax, ymax = bbox_um
    w = max(1, int(round((xmax - xmin) * px_per_um)))
    h = max(1, int(round((ymax - ymin) * px_per_um)))
    ss = supersample
    canvas = np.zeros((h * ss, w * ss), dtype=np.uint8)
    sx = px_per_um * ss
    scale_fp = float(1 << _FP_SHIFT)
    hs = h * ss
    ws = w * ss
    for pts in polygons_um:
        if len(pts) < 3:
            continue
        if (
            pts[:, 0].max() <= xmin
            or pts[:, 0].min() >= xmax
            or pts[:, 1].max() <= ymin
            or pts[:, 1].min() >= ymax
        ):
            continue
        # axis-aligned rectangles (all dither geometry) get an EXACT half-open
        # numpy fill: fillPoly paints boundary pixels inclusively, which
        # inflates an isolated 1 um dither site to ~1.5 um at the comparison
        # grid and read back as +19% mean tone error (measured)
        if len(pts) == 4:
            xs = pts[:, 0]
            ys = pts[:, 1]
            if xs[0] == xs[3] and xs[1] == xs[2] and ys[0] == ys[1] and ys[2] == ys[3] or (
                xs[0] == xs[1] and xs[2] == xs[3] and ys[1] == ys[2] and ys[3] == ys[0]
            ):
                # clamp BOTH slice ends: an off-viewport rectangle otherwise
                # leaves a negative stop, which Python slicing wraps to the
                # far edge of the canvas — a full-height phantom bar
                c0 = max(0, int(round((xs.min() - xmin) * sx)))
                c1 = min(ws, int(round((xs.max() - xmin) * sx)))
                r0 = max(0, int(round((ymax - ys.max()) * sx)))
                r1 = min(hs, int(round((ymax - ys.min()) * sx)))
                if r1 > r0 and c1 > c0:
                    canvas[r0:r1, c0:c1] = 255
                continue
        pix = np.empty_like(pts)
        pix[:, 0] = (pts[:, 0] - xmin) * sx * scale_fp
        pix[:, 1] = (ymax - pts[:, 1]) * sx * scale_fp
        cv2.fillPoly(
            canvas,
            [np.round(pix).astype(np.int32)],
            255,
            lineType=cv2.LINE_8,
            shift=_FP_SHIFT,
        )
    if ss > 1:
        coverage = cv2.resize(canvas, (w, h), interpolation=cv2.INTER_AREA)
        arr = (coverage >= 128).astype(np.uint8) * 255
    else:
        arr = canvas
    if invert:
        arr = 255 - arr
    return arr


def save_png(arr: np.ndarray, path: str) -> None:
    Image.fromarray(arr).save(path)


def save_preview(
    polygons_um: list[np.ndarray],
    bbox_um: tuple[float, float, float, float],
    px_per_um: float,
    path: str,
    invert: bool = False,
) -> np.ndarray:
    arr = rasterize(polygons_um, bbox_um, px_per_um, invert=invert)
    save_png(255 - arr if not invert else arr, path)  # ink shown dark on white, like paper
    return arr
