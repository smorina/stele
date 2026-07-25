"""Plate furniture: fiducials, orientation glyph, stroke text.

All geometry here is generated in plate-scale micrometers, y-up, origin at the
plate's lower-left corner.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import Polygon

from stele.frontends.trace import trace_binary


def fiducial_cross(cx: float, cy: float, size: float, bar: float | None = None) -> list[np.ndarray]:
    """Cross-in-box fiducial centered at (cx, cy): two bars + four corner squares."""
    bar = bar or size / 8.0
    s2, b2 = size / 2.0, bar / 2.0
    horiz = np.array(
        [[cx - s2, cy - b2], [cx + s2, cy - b2], [cx + s2, cy + b2], [cx - s2, cy + b2]]
    )
    vert = np.array(
        [[cx - b2, cy - s2], [cx + b2, cy - s2], [cx + b2, cy + s2], [cx - b2, cy + s2]]
    )
    corners = []
    c = size / 5.0
    for sx in (-1, 1):
        for sy in (-1, 1):
            x0 = cx + sx * s2 - (c if sx > 0 else 0)
            y0 = cy + sy * s2 - (c if sy > 0 else 0)
            corners.append(np.array([[x0, y0], [x0 + c, y0], [x0 + c, y0 + c], [x0, y0 + c]]))
    return [horiz, vert, *corners]


def orientation_glyph(x0: float, y0: float, height: float) -> list[np.ndarray]:
    """A non-symmetric 'F' (chirality witness), lower-left corner at (x0, y0).

    Width = 0.6 * height, stroke = 0.2 * height. Mirroring this glyph is
    machine-detectable in the render-back.
    """
    h = height
    w = 0.6 * h
    t = 0.2 * h
    stem = np.array([[0, 0], [t, 0], [t, h], [0, h]])
    top = np.array([[t, h - t], [w, h - t], [w, h], [t, h]])
    mid = np.array([[t, 0.45 * h], [0.8 * w, 0.45 * h], [0.8 * w, 0.45 * h + t], [t, 0.45 * h + t]])
    return [ring + np.array([x0, y0]) for ring in (stem, top, mid)]


def centered_orientation_glyph(
    rect: tuple[float, float, float, float], height: float
) -> list[np.ndarray]:
    """The chirality 'F' centered in a reserved rect — build and verify must
    place it identically, so both call this."""
    g_w = 0.6 * height
    pad_x = ((rect[2] - rect[0]) - g_w) / 2.0
    pad_y = ((rect[3] - rect[1]) - height) / 2.0
    return orientation_glyph(rect[0] + pad_x, rect[1] + pad_y, height)


def stroke_text(
    text: str, char_height_um: float, origin: tuple[float, float]
) -> tuple[list[Polygon], float]:
    """Render text to polygons at a given cap height (um), lower-left at origin.

    Reuses the raster trace path on a synthetic high-res bitmap: PIL scalable
    default font -> binary -> trace -> scale to target height.
    Returns (polygons, advance_width_um).
    """
    if not text:
        return [], 0.0
    px_h = 120  # render resolution per cap height
    font = ImageFont.load_default(size=px_h)
    bbox = font.getbbox(text)
    pad = 8
    img = Image.new("L", (bbox[2] - bbox[0] + 2 * pad, bbox[3] - bbox[1] + 2 * pad), 0)
    draw = ImageDraw.Draw(img)
    draw.text((pad - bbox[0], pad - bbox[1]), text, fill=255, font=font)
    arr = np.asarray(img) > 128

    # measure actual glyph height in px to hit the requested cap height exactly
    rows = np.where(arr.any(axis=1))[0]
    cols = np.where(arr.any(axis=0))[0]
    if rows.size == 0:
        return [], 0.0
    glyph_h_px = rows[-1] - rows[0] + 1
    um_per_px = char_height_um / glyph_h_px

    polys = trace_binary(arr.astype(np.uint8), um_per_px, height_px=arr.shape[0])
    # shift so the glyph block's lower-left lands at origin
    y_min = (arr.shape[0] - 1 - rows[-1]) * um_per_px
    x_min = cols[0] * um_per_px
    from shapely.affinity import translate

    out = [translate(p, xoff=origin[0] - x_min, yoff=origin[1] - y_min) for p in polys]
    width_um = (cols[-1] - cols[0] + 1) * um_per_px
    return out, width_um
