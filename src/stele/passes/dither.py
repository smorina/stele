"""Ordered dither for continuous-tone regions (C1 design).

Core idea: FULL-CELL dither at pitch p — each dither site is a p x p square
that is entirely chrome or entirely clear. With p >= max(min_feature,
min_space), every ink cluster and every gap is >= the fab floor BY
CONSTRUCTION: an isolated on-site is exactly min_feature, an isolated
off-site exactly min_space. Halftoning then cannot create DRC violations.

Both screening styles are just threshold masks over the same machinery:
- blue-noise (void-and-cluster): dispersed-dot, energy pushed to high
  frequencies — no strong diffraction orders under the patent's narrowband
  illumination;
- clustered-dot (spiral AM): classic halftone rosette, strong periodic
  orders — kept for comparison and for viewers where dot gain dominates.

Encoding: TONE-TILE CELLS. Tiles of B x B sites quantized to TONE_LEVELS;
a tile's pattern depends only on (mask, quantized tone, mask phase), so the
cell library is bounded at TONE_LEVELS x (mask_period/B)^2 cells per mask —
independent of page count. High-variance tiles (sharp edges) fall back to an
exact per-tile pattern so detail never posterizes.
"""

from __future__ import annotations

import numpy as np

TONE_LEVELS = 65  # quantized darkness levels for tile dedup
TILE_SITES = 16  # tile edge, in dither sites
VARIANCE_EXACT_THRESHOLD = 0.02  # tile tone variance above this -> exact pattern


def void_and_cluster(n: int = 64, sigma: float = 1.9, seed: int = 12345) -> np.ndarray:
    """Ulichney void-and-cluster: an n x n toroidal rank mask (0 .. n^2-1).

    Deterministic for a given seed. Committed as an asset; regenerating is an
    explicit, reviewed action (mask changes re-key every tone-tile cell).
    """
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:n, 0:n]
    dy = np.minimum(yy, n - yy)
    dx = np.minimum(xx, n - xx)
    kernel = np.exp(-(dx.astype(float) ** 2 + dy.astype(float) ** 2) / (2 * sigma**2))
    kernel_f = np.fft.rfft2(kernel)

    def energy(pattern: np.ndarray) -> np.ndarray:
        return np.fft.irfft2(np.fft.rfft2(pattern.astype(float)) * kernel_f, s=(n, n))

    # phase 0: random initial pattern, ~10% on, then swap tightest cluster
    # into the largest void until stable
    ones = max(1, (n * n) // 10)
    pattern = np.zeros((n, n), dtype=bool)
    idx = rng.choice(n * n, ones, replace=False)
    pattern.flat[idx] = True
    while True:
        e = energy(pattern)
        cluster = np.unravel_index(np.where(pattern.ravel(), e.ravel(), -np.inf).argmax(), e.shape)
        pattern[cluster] = False
        e = energy(pattern)
        void = np.unravel_index(np.where(~pattern.ravel(), e.ravel(), np.inf).argmin(), e.shape)
        if void == cluster:
            pattern[cluster] = True
            break
        pattern[void] = True

    rank = np.zeros((n, n), dtype=np.int32)
    # phase 1: remove tightest clusters downward
    work = pattern.copy()
    for r in range(ones - 1, -1, -1):
        e = energy(work)
        c = np.unravel_index(np.where(work.ravel(), e.ravel(), -np.inf).argmax(), e.shape)
        work[c] = False
        rank[c] = r
    # phase 2: fill largest voids upward
    work = pattern.copy()
    for r in range(ones, n * n):
        e = energy(work)
        v = np.unravel_index(np.where(~work.ravel(), e.ravel(), np.inf).argmin(), e.shape)
        work[v] = True
        rank[v] = r
    return rank


def clustered_mask(cell: int = 8) -> np.ndarray:
    """Classic clustered-dot (AM) threshold mask: a spiral growing from the
    cell center, tiled — produces the traditional halftone dot."""
    cy = cx = (cell - 1) / 2.0
    yy, xx = np.mgrid[0:cell, 0:cell]
    d = np.hypot(yy - cy, xx - cx) + 0.001 * np.arctan2(yy - cy, xx - cx + 1e-9)
    return np.argsort(np.argsort(d.ravel())).reshape(cell, cell).astype(np.int32)


def thresholds_from_ranks(rank: np.ndarray) -> np.ndarray:
    """Rank mask -> per-site darkness thresholds in (0, 1)."""
    n2 = rank.size
    return (rank.astype(np.float64) + 0.5) / n2


def dither_sites(darkness: np.ndarray, thresholds: np.ndarray, oy: int = 0, ox: int = 0) -> np.ndarray:
    """darkness (H x W sites, 0..1) -> boolean on/off, mask applied toroidally
    with a phase offset (oy, ox) in sites."""
    n = thresholds.shape[0]
    h, w = darkness.shape
    tiled = np.empty((h, w))
    yy = (np.arange(h) + oy) % n
    xx = (np.arange(w) + ox) % n
    tiled = thresholds[np.ix_(yy, xx)]
    return darkness > tiled


def repair_corner_contacts(bitmap: np.ndarray, thresholds: np.ndarray | None = None,
                           oy: int = 0, ox: int = 0, max_passes: int = 12) -> np.ndarray:
    """Remove diagonal-only (corner-touching) site contacts.

    Measured (C1): dispersed blue-noise dither yields ~6.5k zero-width/space
    edge pairs per 128^2-site swatch from corner-touching squares — klayout
    flags them and fabs commonly reject checkerboard contacts. For each 2x2
    diagonal pattern, flip one site; adds and removals are balanced globally
    so tone is preserved to first order. Deterministic (threshold-guided).
    """
    bm = bitmap.copy()
    n = thresholds.shape[0] if thresholds is not None else 0

    def _pass(additive_only: bool, balance: int) -> tuple[int, int]:
        a = bm[:-1, :-1] & bm[1:, 1:] & ~bm[:-1, 1:] & ~bm[1:, :-1]
        b = bm[:-1, 1:] & bm[1:, :-1] & ~bm[:-1, :-1] & ~bm[1:, 1:]
        ys, xs = np.nonzero(a | b)
        for y, x in zip(ys.tolist(), xs.tolist()):
            quad = bm[y : y + 2, x : x + 2]
            ons = [(dy, dx) for dy in (0, 1) for dx in (0, 1) if quad[dy, dx]]
            offs = [(dy, dx) for dy in (0, 1) for dx in (0, 1) if not quad[dy, dx]]
            if len(ons) != 2 or (ons[0][0] == ons[1][0]) or (ons[0][1] == ons[1][1]):
                continue  # repaired by an earlier flip this pass

            def thr(site):
                if thresholds is None:
                    return 0.5
                return thresholds[(y + site[0] + oy) % n, (x + site[1] + ox) % n]

            if additive_only or balance <= 0:
                site = min(offs, key=thr)
                bm[y + site[0], x + site[1]] = True
                balance += 1
            else:
                site = max(ons, key=thr)
                bm[y + site[0], x + site[1]] = False
                balance -= 1
        return int(ys.size), balance

    balance = 0
    for _ in range(max_passes):
        found, balance = _pass(additive_only=False, balance=balance)
        if found == 0:
            return bm
    # the balanced repair can oscillate (measured: 69/272 tile patterns exited
    # with residual contacts); additive-only passes converge monotonically
    while True:
        found, balance = _pass(additive_only=True, balance=balance)
        if found == 0:
            return bm


def tile_pattern(q: int, phase: tuple[int, int], thresholds: np.ndarray,
                 tile: int = TILE_SITES) -> np.ndarray:
    """Deterministic repaired pattern for a quantized tone-tile.

    Repair runs LOCALLY with a per-tile balance counter, so the pattern is a
    pure function of (q, phase) — identical tiles stay identical and the cell
    library stays bounded. (A global repair pass made ~every tile unique:
    2,929 cells vs 489, measured.) Cross-TILE corner contacts are handled
    separately by page-level connector patches (find_diagonal_contacts).
    """
    darkness = np.full((tile, tile), q / (TONE_LEVELS - 1))
    bm = dither_sites(darkness, thresholds, oy=phase[0], ox=phase[1])
    return repair_corner_contacts(bm, thresholds, oy=phase[0], ox=phase[1])


def find_diagonal_contacts(bitmap: np.ndarray) -> list[tuple[int, int]]:
    """Top-left (row, col) of every 2x2 diagonal-only contact quad."""
    a = bitmap[:-1, :-1] & bitmap[1:, 1:] & ~bitmap[:-1, 1:] & ~bitmap[1:, :-1]
    b = bitmap[:-1, 1:] & bitmap[1:, :-1] & ~bitmap[:-1, :-1] & ~bitmap[1:, 1:]
    ys, xs = np.nonzero(a | b)
    return [(int(y), int(x)) for y, x in zip(ys, xs)]


def patch_sites_for_contacts(
    page_bm: np.ndarray, thresholds: np.ndarray, max_passes: int = 8
) -> list[tuple[int, int]]:
    """Page-level repair of residual (cross-tile) diagonal contacts by turning
    one off-site ON per contact — a FULL-SITE square, legal by construction.

    Additive-only (a removal would need a modified tile cell and break the
    bounded library); the off-site with the lower threshold is chosen, i.e.
    the one the screen would darken first, minimizing local tone distortion.
    A corner-centered sub-site connector patch is NOT legal: its exposed
    quadrants are half-site tabs (measured: ~4 width violations per patch).
    Mutates page_bm; returns the added sites for page-level patch polygons.
    """
    n = thresholds.shape[0]
    added: list[tuple[int, int]] = []
    for _ in range(max_passes):
        contacts = find_diagonal_contacts(page_bm)
        if not contacts:
            break
        for y, x in contacts:
            quad = page_bm[y : y + 2, x : x + 2]
            offs = [(dy, dx) for dy in (0, 1) for dx in (0, 1) if not quad[dy, dx]]
            if len(offs) != 2:
                continue  # already repaired by a neighboring patch this pass
            site = min(offs, key=lambda s: thresholds[(y + s[0]) % n, (x + s[1]) % n])
            page_bm[y + site[0], x + site[1]] = True
            added.append((y + site[0], x + site[1]))
    return added


def quantize_tone(darkness_tile: np.ndarray) -> tuple[int, float]:
    """(quantized level index, variance) for a tile."""
    mean = float(darkness_tile.mean())
    var = float(darkness_tile.var())
    q = int(round(mean * (TONE_LEVELS - 1)))
    return q, var


def bitmap_to_rects_um(bitmap: np.ndarray, pitch_um: float) -> list[tuple[float, float, float, float]]:
    """Boolean site bitmap -> merged row-run rectangles (x0, y0, x1, y1) um,
    y-up, with vertically identical runs coalesced."""
    h, w = bitmap.shape
    rects = []
    open_rects: dict[tuple[int, int], list] = {}  # (x0, x1) -> [y_start, y_end)
    for row in range(h):
        cur: set[tuple[int, int]] = set()
        c = 0
        arr = bitmap[row]
        while c < w:
            if arr[c]:
                c0 = c
                while c < w and arr[c]:
                    c += 1
                cur.add((c0, c))
            else:
                c += 1
        # close rects not continued
        for key in list(open_rects):
            if key not in cur:
                y0, y1 = open_rects.pop(key)
                rects.append((key[0], y0, key[1], y1))
        for key in cur:
            if key in open_rects:
                open_rects[key][1] = row + 1
            else:
                open_rects[key] = [row, row + 1]
    for key, (y0, y1) in open_rects.items():
        rects.append((key[0], y0, key[1], y1))
    # sites are y-down rows; flip to y-up um
    out = []
    for x0, y0, x1, y1 in rects:
        out.append((x0 * pitch_um, (h - y1) * pitch_um, x1 * pitch_um, (h - y0) * pitch_um))
    return out
