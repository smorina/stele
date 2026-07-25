"""Halftone pass: ImageRegion -> full-cell ordered-dither geometry (C1 design).

Scale-aware materialization: the site grid lives at PLATE scale (pitch from
the fab floor), so a page placed at several tier scales is re-dithered per
scale — never geometrically rescaled.

Geometry is emitted as tone-tile cell placements (bounded, library-shared
across pages/regions) plus page-level full-site patch squares; the whole
region is DRC-clean by construction (validated in C1: 0 width / 0 space
violations on klayout's engine at 1 um rules).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import cv2
import numpy as np

from stele.ir.model import ImageRegion
from stele.passes.dither import (
    TILE_SITES,
    VARIANCE_EXACT_THRESHOLD,
    clustered_mask,
    dither_sites,
    patch_sites_for_contacts,
    quantize_tone,
    repair_corner_contacts,
    thresholds_from_ranks,
    tile_pattern,
)

_ASSET = os.path.join(os.path.dirname(__file__), "..", "..", "..", "assets", "bluenoise", "vnc64.npy")


def load_mask(name: str) -> np.ndarray:
    if name == "clustered":
        return thresholds_from_ranks(clustered_mask(8))
    return thresholds_from_ranks(np.load(_ASSET))


def mask_asset_sha256(name: str) -> str:
    if name == "clustered":
        return "generated:clustered8"
    import hashlib

    return hashlib.sha256(open(_ASSET, "rb").read()).hexdigest()


@dataclass
class HalftoneGeometry:
    """Plate-scale geometry for one dithered region, page-cell local."""

    tile_placements: list[tuple[bytes, float, float]] = field(default_factory=list)  # key, x, y
    tile_bitmaps: dict[bytes, np.ndarray] = field(default_factory=dict)
    patch_squares_um: list[tuple[float, float, float, float]] = field(default_factory=list)
    pitch_um: float = 0.0
    n_sites: int = 0
    n_patches: int = 0
    n_exact_tiles: int = 0
    n_budget_fallbacks: int = 0  # exact-eligible tiles quantized: cap reached
    skipped_sub_site: bool = False  # region thinner than one dither site


def dither_region(
    region: ImageRegion,
    scale: float,
    pitch_um: float,
    thresholds: np.ndarray,
    exact_budget: list[int] | None = None,
) -> HalftoneGeometry:
    """Materialize one image region at a placement scale.

    Returns tile placements (content-hash keyed; caller owns the shared cell
    library) and patch squares, all in page-cell plate-scale coordinates.

    The site grid is floor(extent / pitch): emitted geometry never exceeds the
    region bbox, and at most one sub-pitch strip per edge goes unrendered —
    partial edge tiles are emitted, not dropped (review finding 3; the old
    tile-multiple floor silently lost up to 15 site rows/columns and padded
    sub-tile regions BEYOND their box).

    exact_budget is a shared single-element countdown of exact (high-variance)
    tile patterns per plate: each unique exact pattern is its own GDS cell, so
    noisy content is otherwise unbounded (review finding 6). When exhausted,
    tiles fall back to the bounded quantized-tone pattern — a deterministic,
    tone-preserving degradation that the tone gate still verifies.
    """
    x0, y0, x1, y1 = region.bbox_um
    w_um = (x1 - x0) * scale
    h_um = (y1 - y0) * scale
    sites_w = int(w_um // pitch_um)
    sites_h = int(h_um // pitch_um)
    geo = HalftoneGeometry(pitch_um=pitch_um)
    if sites_w < 1 or sites_h < 1:
        geo.skipped_sub_site = True
        return geo
    # darkness field resampled to the site grid (gray() is y-down; flip later)
    darkness = 1.0 - cv2.resize(region.gray(), (sites_w, sites_h), interpolation=cv2.INTER_AREA)

    geo.n_sites = sites_w * sites_h
    n_mask = thresholds.shape[0]
    page_bm = np.zeros((sites_h, sites_w), dtype=bool)
    ox0 = x0 * scale
    oy0 = y0 * scale
    for ty in range(0, sites_h, TILE_SITES):
        th = min(TILE_SITES, sites_h - ty)
        for tx in range(0, sites_w, TILE_SITES):
            tw = min(TILE_SITES, sites_w - tx)
            tile = darkness[ty : ty + th, tx : tx + tw]
            q, var = quantize_tone(tile)
            phase = (ty % n_mask, tx % n_mask)
            exact = var > VARIANCE_EXACT_THRESHOLD
            if exact and exact_budget is not None and exact_budget[0] <= 0:
                exact = False
                geo.n_budget_fallbacks += 1
            if exact:
                bm = repair_corner_contacts(
                    dither_sites(tile, thresholds, oy=phase[0], ox=phase[1]),
                    thresholds, oy=phase[0], ox=phase[1],
                )
            else:
                # a slice of a repaired pattern has no internal contacts;
                # cross-tile contacts go through the page-level patch pass
                bm = tile_pattern(q, phase, thresholds)[:th, :tw]
            page_bm[ty : ty + th, tx : tx + tw] = bm
            # shape-prefixed key: partial tiles of different shapes may share
            # raw bytes (th, tw <= TILE_SITES < 256)
            key = bytes((th, tw)) + bm.tobytes()
            if key not in geo.tile_bitmaps:
                geo.tile_bitmaps[key] = bm
                if exact:
                    geo.n_exact_tiles += 1
                    if exact_budget is not None:
                        exact_budget[0] -= 1
            # site row ty is the TOP of the region (darkness is y-down)
            geo.tile_placements.append(
                (key, ox0 + tx * pitch_um, oy0 + (sites_h - ty - th) * pitch_um)
            )
    patches = patch_sites_for_contacts(page_bm, thresholds)
    geo.n_patches = len(patches)
    for row, col in patches:
        px = ox0 + col * pitch_um
        py = oy0 + (sites_h - row - 1) * pitch_um
        geo.patch_squares_um.append((px, py, px + pitch_um, py + pitch_um))
    return geo
