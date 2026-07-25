"""Measured-geometry gates (review finding 2): stroke widths measured on the
final plate-scale geometry, not inferred from font point sizes."""

from __future__ import annotations

import cv2
import numpy as np


def stroke_width_stats(binary: np.ndarray, um_per_px: float) -> dict:
    """Stroke-width distribution via distance transform ridge sampling.

    `binary`: uint8/bool, nonzero = feature (plate-scale render).
    Width at a ridge pixel ~= 2 * distance-to-background.
    """
    mask = (binary > 0).astype(np.uint8)
    if mask.sum() == 0:
        return {"n_ridge_px": 0}
    dt = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    # ridge = local maxima of the distance transform (approximate skeleton)
    dil = cv2.dilate(dt, np.ones((3, 3), np.float32))
    ridge = (dt >= dil - 1e-4) & (mask > 0) & (dt > 0.5)
    widths_um = 2.0 * dt[ridge] * um_per_px
    if widths_um.size == 0:
        return {"n_ridge_px": 0}
    return {
        "n_ridge_px": int(widths_um.size),
        "p05_um": float(np.percentile(widths_um, 5)),
        "p50_um": float(np.percentile(widths_um, 50)),
        "p95_um": float(np.percentile(widths_um, 95)),
        "min_um": float(widths_um.min()),
        "max_um": float(widths_um.max()),
    }
