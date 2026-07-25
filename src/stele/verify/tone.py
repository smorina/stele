"""Tone metrics for dithered image regions (verify metric 4).

Dither geometry cannot be band-XOR'd against a thresholded render — the
candidate is intentionally a screen. Instead: superpixel coverage of the
candidate vs the CONTINUOUS reference gray (pypdfium2, no threshold), with
the C1 budget: mean error <= 5%, max <= 12%.
"""

from __future__ import annotations

import numpy as np

MEAN_TONE_BUDGET = 0.05
MAX_TONE_BUDGET = 0.12
# superpixels whose SOURCE darkness std exceeds this are edge geometry, not
# tone statements: dither quantizes edges to its pitch by design, so gating
# them on tone reads edge placement as tone error (measured max_err 22% on a
# clean build, all at disk/bar boundaries)
EDGE_STD_EXCLUDE = 0.08


def _block_mean(a: np.ndarray, k: int) -> np.ndarray:
    h = (a.shape[0] // k) * k
    w = (a.shape[1] // k) * k
    if h == 0 or w == 0:
        return np.zeros((0, 0))
    return a[:h, :w].reshape(h // k, k, w // k, k).mean(axis=(1, 3))


def _block_std(a: np.ndarray, k: int) -> np.ndarray:
    m = _block_mean(a, k)
    m2 = _block_mean(a * a, k)
    return np.sqrt(np.maximum(m2 - m * m, 0.0))


def image_tone_metrics(
    cand_binary: np.ndarray,
    ref_gray: np.ndarray,
    rects_px: list[tuple[int, int, int, int]],  # (r0, c0, r1, c1), y-down
    superpixel_px: int,
    ref_gray_build_engine: np.ndarray | None = None,
) -> dict:
    """Superpixel candidate coverage vs source darkness over image rects.

    With ref_gray_build_engine given, the target is the ENVELOPE between the
    two engines' darkness fields (tone-domain hysteresis): color-managed
    content (CMYK) diverges up to ~14% between mupdf and pdfium gray
    conversions (measured) — no binarization policy can adjudicate inside
    that band, but a candidate outside BOTH engines' tones is a real defect.
    """
    h = min(cand_binary.shape[0], ref_gray.shape[0])
    w = min(cand_binary.shape[1], ref_gray.shape[1])
    if ref_gray_build_engine is not None:
        h = min(h, ref_gray_build_engine.shape[0])
        w = min(w, ref_gray_build_engine.shape[1])
    errs: list[np.ndarray] = []
    excluded: list[int] = []
    regions_assessed = 0
    regions_skipped = 0  # regions with NO gated superpixel: unverified content
    for r0, c0, r1, c1 in rects_px:
        r0, c0 = max(0, r0), max(0, c0)
        r1, c1 = min(h, r1), min(w, c1)
        if r1 - r0 < superpixel_px or c1 - c0 < superpixel_px:
            regions_skipped += 1
            continue
        # shrink by one superpixel: the region border mixes with surrounding
        # page white and is not a tone-fidelity statement
        r0 += superpixel_px // 2
        c0 += superpixel_px // 2
        r1 -= superpixel_px // 2
        c1 -= superpixel_px // 2
        if r1 - r0 < superpixel_px or c1 - c0 < superpixel_px:
            regions_skipped += 1
            continue
        darkness_field = 1.0 - ref_gray[r0:r1, c0:c1].astype(np.float32) / 255.0
        cov = _block_mean((cand_binary[r0:r1, c0:c1] > 0).astype(np.float32), superpixel_px)
        dark_a = _block_mean(darkness_field, superpixel_px)
        std = _block_std(darkness_field, superpixel_px)
        if ref_gray_build_engine is not None:
            dark_b = _block_mean(
                1.0 - ref_gray_build_engine[r0:r1, c0:c1].astype(np.float32) / 255.0,
                superpixel_px,
            )
            lo = np.minimum(dark_a, dark_b)
            hi = np.maximum(dark_a, dark_b)
        else:
            lo = hi = dark_a
        gated = np.zeros(0)
        if cov.size:
            smooth = (std <= EDGE_STD_EXCLUDE).ravel()
            err = np.maximum(np.maximum(cov - hi, lo - cov), 0.0)
            gated = err.ravel()[smooth]
            errs.append(gated)
            excluded.append(int((~smooth).sum()))
        if gated.size:
            regions_assessed += 1
        else:
            regions_skipped += 1
    if not errs or not np.concatenate(errs).size:
        # nothing gated: NOT a tone statement — callers must surface this as
        # unverified content, never as an unqualified pass (review finding 3)
        return {"superpixels": 0, "edge_excluded": int(sum(excluded)), "mean_err": 0.0,
                "max_err": 0.0, "pass": True, "assessed": False,
                "regions_assessed": 0, "regions_skipped": regions_skipped}
    e = np.concatenate(errs)
    mean_err = float(e.mean())
    max_err = float(e.max())
    return {
        "superpixels": int(e.size),
        "edge_excluded": int(sum(excluded)),
        "mean_err": round(mean_err, 4),
        "max_err": round(max_err, 4),
        "pass": bool(mean_err <= MEAN_TONE_BUDGET and max_err <= MAX_TONE_BUDGET),
        "assessed": True,
        "regions_assessed": regions_assessed,
        "regions_skipped": regions_skipped,
    }
