"""XOR-diff metrics: candidate (klayout render-back of the GDS) vs reference
(pypdfium2 render of the source PDF).

Tolerance model (progress-review finding 2): edge fuzz is excused with a
DIRECTIONAL band, never by eroding the disagreement map — eroding XOR erases
any defect thinner than the band (a deleted 1 um stroke is 2 px at the 0.5
um/px grid and vanished under 3x3 erosion; measured). Instead:

    missing = strict_ref & ~dilate(candidate, tol)   # ref ink with NO candidate
                                                     # ink within tol
    extra   = candidate  & ~dilate(loose_ref,  tol)  # candidate ink far from any
                                                     # plausibly-dark reference

A fully deleted stroke has no candidate ink within tol -> flagged at ANY
width. A 1-px registration shift keeps every pixel within tol -> excused.
Severe thinning is flagged once the lost rim exceeds tol; gradual thinning is
the per-page stroke-width gate's job.

Metrics per page:
- raw_mismatch: plain XOR vs the mid mask / ref ink (reported, not gated)
- defect_mismatch: (missing | extra) / ref ink (gated)
- structural_defects: connected components of the defect map >= MIN_DEFECT_PX
- component_failures: STRICT-mask components (>= COMPONENT_MIN_PX) whose
  coverage by dilate(candidate, tol) falls short of COMPONENT_COVERAGE (with
  an absolute quantization slack). Labeled on the strict mask so that
  engine-marginal content — sub-pixel hairlines, near-threshold tone — is
  never gated, consistent with the defect map. The bloat direction is NOT a
  per-component check: phantom ink is the `extra` defect map's job. (A raw
  per-component IoU re-introduces the edge-fuzz sensitivity the band exists
  to absorb — measured 1,343 false failures on a clean text page. Components
  are not glyphs; glyph ground truth exists only for synthetic pages.)
- shape_mismatch: candidate/reference grids differing by more than 2 px FAIL
  (silently cropping large disagreements hides scaling bugs)
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

MIN_DEFECT_PX = 20
TOL_PX = 1  # one comparison pixel (0.5 um at the default verify grid)
MAX_SHAPE_DELTA_PX = 2
COMPONENT_COVERAGE = 0.95
COMPONENT_MIN_PX = 30
# quantization slack: a component only fails when its uncovered pixels exceed
# BOTH the ratio (1 - COMPONENT_COVERAGE) and this absolute count — a 34-px
# period with 2 px of rim disagreement is quantization, not damage (measured)
COMPONENT_SLACK_PX = 4


@dataclass
class XorResult:
    raw_mismatch: float
    defect_mismatch: float
    structural_defects: int
    component_failures: int
    ref_ink_px: int
    cand_ink_px: int
    shape_mismatch: bool = False
    defect_map: np.ndarray | None = None

    @property
    def page_fail(self) -> bool:
        return bool(
            self.shape_mismatch or self.structural_defects > 0 or self.component_failures > 0
        )

    def to_dict(self) -> dict:
        return {
            "raw_mismatch": round(self.raw_mismatch, 6),
            "defect_mismatch": round(self.defect_mismatch, 6),
            "structural_defects": self.structural_defects,
            "component_failures": self.component_failures,
            "ref_ink_px": self.ref_ink_px,
            "cand_ink_px": self.cand_ink_px,
            "shape_mismatch": self.shape_mismatch,
        }


def _band(tol_px: int) -> np.ndarray:
    return np.ones((2 * tol_px + 1, 2 * tol_px + 1), np.uint8)


def hysteresis_compare(
    candidate: np.ndarray,
    ref_strict: np.ndarray,
    ref_mid: np.ndarray,
    ref_loose: np.ndarray,
    tol_px: int = TOL_PX,
    tol_extra_px: int | None = None,
    keep_map: bool = False,
) -> XorResult:
    """Cross-engine comparison: tone hysteresis + directional band tolerance.

    Tolerances are ASYMMETRIC: `tol_px` gates missing ink (kept tight — a
    dropped stroke is the catastrophic direction), `tol_extra_px` gates
    phantom ink (default tol_px + 1: engines disagree on glyph stem widths by
    up to ~2 comparison px even with embedded fonts — measured on identical
    stems across rows — always in the candidate-fatter direction via AA and
    the trace footprint). Callers must scale both with the effective
    build-pixel size at the comparison grid (tier scales multiply it).
    """
    if tol_extra_px is None:
        tol_extra_px = tol_px + 1
    dh = abs(candidate.shape[0] - ref_mid.shape[0])
    dw = abs(candidate.shape[1] - ref_mid.shape[1])
    if dh > MAX_SHAPE_DELTA_PX or dw > MAX_SHAPE_DELTA_PX:
        return XorResult(
            raw_mismatch=1.0,
            defect_mismatch=1.0,
            structural_defects=1,
            component_failures=0,
            ref_ink_px=int((ref_mid > 0).sum()),
            cand_ink_px=int((candidate > 0).sum()),
            shape_mismatch=True,
        )

    h = min(candidate.shape[0], ref_mid.shape[0])
    w = min(candidate.shape[1], ref_mid.shape[1])
    cand = (candidate[:h, :w] > 0).astype(np.uint8)
    strict = (ref_strict[:h, :w] > 0).astype(np.uint8)
    mid = (ref_mid[:h, :w] > 0).astype(np.uint8)
    loose = (ref_loose[:h, :w] > 0).astype(np.uint8)

    ref_ink = int(mid.sum())
    denom = max(ref_ink, 1)
    raw = float((cand ^ mid).sum()) / denom

    cand_dil = cv2.dilate(cand, _band(tol_px))
    loose_dil = cv2.dilate(loose, _band(tol_extra_px))
    missing = strict & ~cand_dil
    extra = cand & ~loose_dil
    defect_map = (missing | extra).astype(np.uint8)
    defect_frac = float(defect_map.sum()) / denom

    n_labels, _, stats, _ = cv2.connectedComponentsWithStats(defect_map, connectivity=8)
    defects = sum(1 for i in range(1, n_labels) if stats[i, cv2.CC_STAT_AREA] >= MIN_DEFECT_PX)

    comp_failures = _component_failures(strict, cand, tol_px)

    return XorResult(
        raw_mismatch=raw,
        defect_mismatch=defect_frac,
        structural_defects=defects,
        component_failures=comp_failures,
        ref_ink_px=ref_ink,
        cand_ink_px=int(cand.sum()),
        defect_map=(defect_map * 255) if keep_map else None,
    )


def _component_failures(strict: np.ndarray, cand: np.ndarray, tol_px: int) -> int:
    """Band-tolerant per-component coverage: every strict-mask component
    >= COMPONENT_MIN_PX must be covered by the candidate dilated by tol, up to
    a ratio (COMPONENT_COVERAGE) with an absolute quantization slack."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(strict, connectivity=8)
    pad = tol_px + 2
    failures = 0
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < COMPONENT_MIN_PX:
            continue
        x, y, bw, bh = stats[i, 0], stats[i, 1], stats[i, 2], stats[i, 3]
        y0, y1 = max(0, y - pad), min(strict.shape[0], y + bh + pad)
        x0, x1 = max(0, x - pad), min(strict.shape[1], x + bw + pad)
        comp = (labels[y0:y1, x0:x1] == i).astype(np.uint8)
        cand_dil = cv2.dilate((cand[y0:y1, x0:x1] > 0).astype(np.uint8), _band(tol_px)) > 0
        n_comp = int(comp.sum())
        uncovered = n_comp - int(np.logical_and(comp > 0, cand_dil).sum())
        if uncovered > max((1.0 - COMPONENT_COVERAGE) * n_comp, COMPONENT_SLACK_PX):
            failures += 1
    return failures


def xor_compare(candidate: np.ndarray, reference: np.ndarray, keep_map: bool = False) -> XorResult:
    """Single-mask comparison (unit tests / same-tone probes): strict = mid =
    loose, symmetric 1-px bands (no cross-engine divergence to absorb)."""
    ref = (reference > 0).astype(np.uint8)
    return hysteresis_compare(candidate, ref, ref, ref, tol_extra_px=TOL_PX, keep_map=keep_map)
