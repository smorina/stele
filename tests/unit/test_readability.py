"""Readability simulation invariants."""

import numpy as np

from stele.config.profiles import ReaderProfile
from stele.verify.readability import airy_psf, simulate_view, stroke_contrast

READER = ReaderProfile(name="t", numerical_aperture=0.25, wavelength_nm=580.0, magnification=100)


def _bars(width_px: int, pitch_px: int, size: int = 400) -> np.ndarray:
    img = np.zeros((size, size), dtype=np.uint8)
    for x0 in range(40, size - 40, pitch_px):
        img[40:-40, x0 : x0 + width_px] = 255
    return img


def test_psf_normalized_and_peaked():
    psf = airy_psf(0.25, 0.58, 0.1)
    assert abs(psf.sum() - 1.0) < 1e-6
    c = psf.shape[0] // 2
    assert psf[c, c] == psf.max()


def test_wide_strokes_high_contrast_thin_strokes_low():
    um_per_px = 0.1
    wide = _bars(width_px=40, pitch_px=120)  # 4 um strokes
    thin = _bars(width_px=6, pitch_px=24)  # 0.6 um strokes << Rayleigh 1.4 um
    for img, expect_high in ((wide, True), (thin, False)):
        psf_view = simulate_view(img, um_per_px, READER)[0]
        # measure contrast at the PSF grid (pre-eye downsample) for stability
        import cv2

        from stele.verify.readability import airy_psf as _a  # noqa: F401

        transmission = 1.0 - (img > 0).astype(np.float32)
        intensity = cv2.filter2D(
            transmission, -1,
            airy_psf(READER.numerical_aperture, 0.58, um_per_px).astype(np.float32),
        )
        r = stroke_contrast(img, intensity, um_per_px)
        c = r["min_stroke_contrast"]
        if expect_high:
            assert c is not None and c > 0.6, r
        else:
            assert c is not None and c < 0.3, r
        assert psf_view.shape[0] > 0


def test_contrast_bins_ordered_by_width():
    """Contrast must not DECREASE with stroke width (monotone physics)."""
    import cv2

    um_per_px = 0.1
    img = np.zeros((500, 700), dtype=np.uint8)
    for i, w in enumerate((6, 12, 20, 40)):  # 0.6, 1.2, 2, 4 um
        img[50:-50, 80 + i * 150 : 80 + i * 150 + w] = 255
    transmission = 1.0 - (img > 0).astype(np.float32)
    intensity = cv2.filter2D(
        transmission, -1, airy_psf(0.25, 0.58, um_per_px).astype(np.float32)
    )
    r = stroke_contrast(img, intensity, um_per_px)
    cs = [b["michelson"] for b in r["bins"]]
    assert len(cs) >= 3
    assert all(b <= a + 0.05 for a, b in zip(cs[1:], cs[:-1])), cs
