"""Readability simulation: the legibility acceptance instrument (M4).

Models the bundled reader as an incoherent diffraction-limited imager:
render plate geometry at a fine grid -> convolve with the Airy PSF for the
reader profile's (NA, lambda) -> downsample to eye-limited sampling at the
profile magnification -> measure Michelson contrast across the thinnest
strokes. The closed-form manifest check remains an early warning; THIS is
the hard verdict (review finding 2).
"""

from __future__ import annotations

import cv2
import numpy as np

from stele.config.profiles import ReaderProfile

EYE_RESOLUTION_UM_AT_250MM = 73.0  # ~1 arcmin at standard near distance


def _bessel_j1(x: np.ndarray) -> np.ndarray:
    """Abramowitz & Stegun 9.4.4/9.4.6 rational approximations."""
    x = np.asarray(x, dtype=np.float64)
    out = np.empty_like(x)
    small = np.abs(x) < 3.0
    xs = x[small] / 3.0
    x2 = xs * xs
    out[small] = x[small] * (
        0.5 - x2 * (0.56249985 - x2 * (0.21093573 - x2 * (0.03954289 - x2 * (
            0.00443319 - x2 * (0.00031761 - x2 * 0.00001109)))))
    ) / 1.0
    xl = np.abs(x[~small])
    y = 3.0 / xl
    f1 = 0.79788456 + y * (0.00000156 + y * (0.01659667 + y * (0.00017105 - y * (
        0.00249511 - y * (0.00113653 - y * 0.00020033)))))
    t1 = xl - 2.35619449 + y * (0.12499612 + y * (0.0000565 - y * (0.00637879 - y * (
        0.00074348 + y * (0.00079824 - y * 0.00029166)))))
    out[~small] = np.sign(x[~small]) * f1 * np.cos(t1) / np.sqrt(xl)
    return out


def airy_psf(na: float, wavelength_um: float, um_per_px: float, radius_px: int | None = None) -> np.ndarray:
    """Normalized incoherent Airy PSF kernel."""
    if radius_px is None:
        # out to the 3rd dark ring
        r3_um = 3.24 * wavelength_um / (2 * na)
        radius_px = max(4, int(np.ceil(r3_um / um_per_px)))
    ax = np.arange(-radius_px, radius_px + 1)
    yy, xx = np.meshgrid(ax, ax)
    r_um = np.hypot(yy, xx) * um_per_px
    v = 2 * np.pi * na * r_um / wavelength_um
    with np.errstate(divide="ignore", invalid="ignore"):
        amp = np.where(v == 0.0, 1.0, 2.0 * _bessel_j1(v) / np.where(v == 0, 1.0, v))
    psf = amp**2
    return psf / psf.sum()


def simulate_view(
    ink_binary: np.ndarray,
    um_per_px: float,
    reader: ReaderProfile,
    defocus_um: float = 0.0,
) -> tuple[np.ndarray, float]:
    """Transmitted-light intensity through the reader.

    ink = chrome = dark (clear-field). Returns (intensity 0..1 at eye-limited
    sampling, eye_um_per_px on plate).
    """
    transmission = 1.0 - (ink_binary > 0).astype(np.float32)
    psf = airy_psf(
        reader.numerical_aperture, reader.wavelength_nm / 1000.0, um_per_px
    ).astype(np.float32)
    intensity = cv2.filter2D(transmission, -1, psf, borderType=cv2.BORDER_REPLICATE)
    if defocus_um != 0:
        # geometric defocus blur: disk of diameter ~ 2*NA*|defocus| (symmetric
        # in sign — negative defocus was silently ignored, review finding 2)
        d_px = max(1, int(round(2 * reader.numerical_aperture * abs(defocus_um) / um_per_px)))
        if d_px > 1:
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (d_px, d_px)).astype(np.float32)
            intensity = cv2.filter2D(intensity, -1, k / k.sum(), borderType=cv2.BORDER_REPLICATE)
    eye_um = EYE_RESOLUTION_UM_AT_250MM / reader.magnification
    factor = max(1, int(round(eye_um / um_per_px)))
    h = (intensity.shape[0] // factor) * factor
    w = (intensity.shape[1] // factor) * factor
    eye_view = cv2.resize(
        intensity[:h, :w], (w // factor, h // factor), interpolation=cv2.INTER_AREA
    )
    return eye_view, factor * um_per_px


def contrast_gate(
    ink_binary: np.ndarray,
    um_per_px: float,
    reader: ReaderProfile,
    wavelength_nm: float | None = None,
) -> dict:
    """PSF-convolved stroke contrast plus the reader-profile VERDICT.

    The recorded legibility result for verification gates (review finding 2):
    pass = the thinnest measured stroke bin meets the Michelson criterion.
    assessed = False when the window holds too little ink to bin.
    """
    wl_nm = wavelength_nm if wavelength_nm is not None else reader.wavelength_nm
    transmission = 1.0 - (ink_binary > 0).astype(np.float32)
    psf = airy_psf(reader.numerical_aperture, wl_nm / 1000.0, um_per_px).astype(np.float32)
    intensity = cv2.filter2D(transmission, -1, psf, borderType=cv2.BORDER_REPLICATE)
    r = stroke_contrast(ink_binary, intensity, um_per_px)
    crit = reader.contrast_criterion
    r["wavelength_nm"] = wl_nm
    r["criterion"] = crit
    r["legible_bins"] = [b["stroke_um"] for b in r["bins"] if b["michelson"] >= crit]
    r["assessed"] = bool(r["bins"])
    r["pass"] = bool(r["bins"]) and r["bins"][0]["michelson"] >= crit
    return r


def densest_ink_window(
    ink_binary: np.ndarray,
    win_px: tuple[int, int],  # (h, w)
    exclude_rects_px: list[tuple[int, int, int, int]] | None = None,  # (r0,c0,r1,c1)
    block: int = 64,
    min_density: float = 0.002,
) -> tuple[int, int] | None:
    """Top-left (row, col) of the densest ink window avoiding excluded rects.

    Readability is measured on TEXT: windows overlapping image rects (dither
    screens are not strokes) are disqualified, not merely down-weighted.
    Returns None when no window holds enough ink — nothing to read.
    """
    gh = max(1, ink_binary.shape[0] // block)
    gw = max(1, ink_binary.shape[1] // block)
    # downsample WITHOUT a full-size float copy: a 4x-tier page raster is
    # ~1.3 GB uint8, and a float32 conversion would allocate 4x that
    src = ink_binary if ink_binary.dtype == np.uint8 else (ink_binary > 0).astype(np.uint8)
    peak = float(src.max())
    if peak == 0.0:
        return None
    dens = cv2.resize(src, (gw, gh), interpolation=cv2.INTER_AREA).astype(np.float32) / peak
    bad = np.zeros_like(dens)
    for r0, c0, r1, c1 in exclude_rects_px or []:
        bad[max(0, r0 // block) : r1 // block + 1, max(0, c0 // block) : c1 // block + 1] = 1.0
    kh = max(1, win_px[0] // block)
    kw = max(1, win_px[1] // block)
    score = cv2.boxFilter(dens, -1, (kw, kh), borderType=cv2.BORDER_CONSTANT)
    overlap = cv2.boxFilter(bad, -1, (kw, kh), borderType=cv2.BORDER_CONSTANT)
    score[overlap > 0] = -1.0
    cy, cx = np.unravel_index(int(score.argmax()), score.shape)
    if score[cy, cx] < min_density:
        return None
    r0 = int(np.clip(cy * block + block // 2 - win_px[0] // 2, 0,
                     max(0, ink_binary.shape[0] - win_px[0])))
    c0 = int(np.clip(cx * block + block // 2 - win_px[1] // 2, 0,
                     max(0, ink_binary.shape[1] - win_px[1])))
    return r0, c0


def stroke_contrast(
    ink_binary: np.ndarray, intensity: np.ndarray, um_per_px: float
) -> dict:
    """Michelson contrast measured at stroke ridges, binned by stroke width.

    intensity must be at the SAME grid as ink_binary (pre-eye-downsample).
    """
    mask = (ink_binary > 0).astype(np.uint8)
    if mask.sum() == 0:
        return {"bins": [], "min_stroke_contrast": None}
    dt = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    dil = cv2.dilate(dt, np.ones((3, 3), np.float32))
    ridge = (dt >= dil - 1e-4) & (mask > 0) & (dt > 0.5)
    widths_um = 2.0 * dt[ridge] * um_per_px
    i_min = intensity[ridge]
    # local background: bright field away from ink
    bg = intensity[(mask == 0)]
    i_max = float(np.percentile(bg, 90)) if bg.size else 1.0

    bins: list[dict] = []
    edges = [0.0, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 1e9]
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = (widths_um >= lo) & (widths_um < hi)
        if sel.sum() < 20:
            continue
        imin = float(np.percentile(i_min[sel], 50))
        c = (i_max - imin) / max(i_max + imin, 1e-6)
        bins.append({
            "stroke_um": f"{lo}-{hi if hi < 1e9 else 'inf'}",
            "n_ridge_px": int(sel.sum()),
            "michelson": round(float(c), 4),
        })
    return {
        "bins": bins,
        "min_stroke_contrast": bins[0]["michelson"] if bins else None,
        "background_intensity": round(i_max, 4),
    }
