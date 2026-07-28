"""Verification reference renders via pypdfium2 — a DIFFERENT PDF engine than
the build frontend (PyMuPDF), so a shared rendering omission cannot survive
the XOR check (review finding 3)."""

from __future__ import annotations

import numpy as np
import pypdfium2 as pdfium


def _threshold_mask(
    gray: np.ndarray,
    threshold: int,
    rect_slices: list[tuple[int, int, int, int]],
    rect_threshold: int,
) -> np.ndarray:
    """Threshold one high-resolution band without a full-size threshold map."""
    mask = gray < threshold
    if rect_threshold != threshold:
        for r0, r1, c0, c1 in rect_slices:
            mask[r0:r1, c0:c1] = gray[r0:r1, c0:c1] < rect_threshold
    return mask


def _to_comparison_grid(
    mask: np.ndarray,
    width_px: int,
    height_px: int,
    coverage: float,
    upsampling: bool,
) -> np.ndarray:
    """Resize a build-resolution binary mask with its original coverage rule."""
    import cv2

    if upsampling:
        return cv2.resize(
            mask.astype(np.uint8), (width_px, height_px), interpolation=cv2.INTER_NEAREST
        ) > 0
    resized = cv2.resize(
        mask.astype(np.float32), (width_px, height_px), interpolation=cv2.INTER_AREA
    )
    return resized >= coverage


def render_reference_gray(pdf_path: str, page_index: int, width_px: int) -> np.ndarray:
    """Render one page to grayscale uint8 at an exact pixel width (y-down)."""
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        page = pdf[page_index]
        w_pt = page.get_width()
        scale = width_px / w_pt
        bitmap = page.render(scale=scale, grayscale=True)
        arr = bitmap.to_numpy()
        if arr.ndim == 3:
            arr = arr[..., 0]
        return arr.copy()
    finally:
        pdf.close()


def image_regions_pt(pdf_path: str, page_index: int) -> list[tuple[float, float, float, float]]:
    """Embedded-image bboxes in page points (y-down), via PyMuPDF."""
    import fitz

    with fitz.open(pdf_path) as doc:
        page = doc[page_index]
        return [tuple(fitz.Rect(b["bbox"]) & page.rect) for b in page.get_image_info(xrefs=False)]


def reference_masks(
    pdf_path: str,
    page_index: int,
    width_px: int,
    fixed_threshold: int = 128,
    binarize_dpi: int = 900,
    hysteresis: int = 8,
    image_rects_pt: list[tuple[float, float, float, float]] | None = None,
    image_hysteresis: int = 40,
    threshold_mode: str = "fixed",
    threshold_exclude_rects: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """(strict, mid, loose, threshold_info) at the comparison grid.

    threshold_mode mirrors the BUILD's declared binarization policy on the
    independent reference render (review finding: an `otsu` build verified
    against a fixed threshold is comparing different policies). For "otsu" the
    threshold is recomputed from this engine's build-DPI render with the same
    flat-page fallback rule as the build frontend; the cross-engine gray delta
    lands inside the hysteresis band. threshold_exclude_rects blanks the image
    rects before computing the Otsu level, mirroring dither-mode builds where
    images are blanked before binarization.

    Cross-engine hysteresis: `strict` = clearly-dark ink (gray < thr - h),
    `loose` = anything plausibly ink (gray < thr + h). Content whose ink-ness
    depends on +/-h gray levels around the declared threshold is ambiguous
    between renderers and is not gated.

    REGION-SCOPED tolerance: inside embedded-image bboxes the hysteresis
    widens to `image_hysteresis` — measured CMYK->gray conversion deltas
    between mupdf and pdfium reach ~36 gray levels, a color-management gap no
    binarization policy can adjudicate. Text/vector content keeps the tight
    band. (Interim until M3 replaces image regions with generated dither
    geometry checked by tone metrics.)

    All masks are binarized at the build DPI first, then area-downsampled
    (threshold(downsample(x)) != downsample(threshold(x)) on halftones).
    """
    import cv2

    pdf = pdfium.PdfDocument(pdf_path)
    try:
        w_pt = pdf[page_index].get_width()
    finally:
        pdf.close()
    # binarize at EXACTLY the build DPI: the candidate geometry is the build
    # engine's 900-dpi footprint, and a reference binarized at a different
    # effective resolution disagrees wholesale on small glyphs (measured:
    # 2,377 false defects on a 4x tier page, whose comparison grid is ~1860
    # dpi-equivalent). Binary is then resampled to the comparison grid.
    hi_width_px = int(round(w_pt / 72.0 * binarize_dpi))
    gray = render_reference_gray(pdf_path, page_index, hi_width_px)
    h_px = int(round(gray.shape[0] * width_px / gray.shape[1]))

    scale = hi_width_px / w_pt
    rect_slices = []
    for x0, y0, x1, y1 in image_rects_pt or []:
        r0, r1 = max(0, int(y0 * scale)), min(gray.shape[0], int(np.ceil(y1 * scale)))
        c0, c1 = max(0, int(x0 * scale)), min(gray.shape[1], int(np.ceil(x1 * scale)))
        rect_slices.append((r0, r1, c0, c1))

    thr = int(fixed_threshold)
    if threshold_mode == "otsu":
        t_src = gray
        if threshold_exclude_rects and rect_slices:
            t_src = gray.copy()
            for r0, r1, c0, c1 in rect_slices:
                t_src[r0:r1, c0:c1] = 255
        # same flat-page fallback rule as the build frontend's binarize()
        if t_src.std() > 4.0:
            t, _ = cv2.threshold(t_src, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            thr = int(round(t))

    upsampling = width_px > hi_width_px

    # Build and downsample one band at a time. The former int16 gray copy plus
    # full-page int16 hysteresis map together cost ~303 MB on a 900-DPI Letter
    # render, and all three masks overlapped in memory. Only embedded-image
    # rectangles need a different band, so patch those slices in each boolean
    # mask instead.
    strict = _to_comparison_grid(
        _threshold_mask(gray, thr - hysteresis, rect_slices, thr - image_hysteresis),
        width_px,
        h_px,
        0.55,
        upsampling,
    )
    mid = _to_comparison_grid(
        _threshold_mask(gray, thr, rect_slices, thr),
        width_px,
        h_px,
        0.5,
        upsampling,
    )
    loose = _to_comparison_grid(
        _threshold_mask(gray, thr + hysteresis, rect_slices, thr + image_hysteresis),
        width_px,
        h_px,
        0.35,
        upsampling,
    )
    return strict, mid, loose, {"mode": threshold_mode, "threshold": thr}


def render_reference_rgb(pdf_path: str, page_index: int, width_px: int) -> np.ndarray:
    """RGB render at an exact pixel width (y-down, HxWx3)."""
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        page = pdf[page_index]
        scale = width_px / page.get_width()
        arr = page.render(scale=scale).to_numpy()
        return arr[..., :3].copy()
    finally:
        pdf.close()


def reference_ink_mask(
    pdf_path: str,
    page_index: int,
    width_px: int,
    fixed_threshold: int = 128,
    binarize_dpi: int | None = None,
) -> np.ndarray:
    """Binary reference mask at the comparison grid.

    When binarize_dpi is given (the build's render DPI), the page is rendered
    and thresholded at THAT resolution first, and the binary mask is then
    area-downsampled to the comparison grid — mirroring the candidate path
    (binarize at build DPI -> geometry -> raster at grid). Thresholding after
    downsampling instead would turn halftone/hatched artwork into solid ink
    (threshold(downsample(x)) != downsample(threshold(x))).
    """
    import cv2

    if binarize_dpi is None:
        gray = render_reference_gray(pdf_path, page_index, width_px)
        return gray < fixed_threshold

    pdf = pdfium.PdfDocument(pdf_path)
    try:
        w_pt = pdf[page_index].get_width()
    finally:
        pdf.close()
    hi_width_px = max(width_px, int(round(w_pt / 72.0 * binarize_dpi)))
    gray = render_reference_gray(pdf_path, page_index, hi_width_px)
    binary = (gray < fixed_threshold).astype(np.float32)
    h_px = int(round(binary.shape[0] * width_px / binary.shape[1]))
    coverage = cv2.resize(binary, (width_px, h_px), interpolation=cv2.INTER_AREA)
    return coverage >= 0.5
