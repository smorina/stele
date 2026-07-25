"""Raster frontend (DEFAULT): render PDF page -> threshold -> trace.

Build rendering uses PyMuPDF. The verification *reference* render deliberately
uses a different engine (pypdfium2, see stele.verify.reference) so a shared
rendering omission cannot survive the XOR check.
"""

from __future__ import annotations

import cv2
import fitz  # PyMuPDF
import numpy as np

from stele.ir.model import UM_PER_INCH, ImageRegion, PageIR, SourceRef

DEFAULT_DPI = 900


def render_page_gray(pdf_path: str, page_index: int, dpi: int = DEFAULT_DPI) -> np.ndarray:
    """Render one page to a grayscale uint8 array (y-down pixel space)."""
    with fitz.open(pdf_path) as doc:
        page = doc[page_index]
        zoom = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY, alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        return arr.copy()  # detach from pixmap buffer before it is freed


def render_page_rgb(pdf_path: str, page_index: int, dpi: int = DEFAULT_DPI) -> np.ndarray:
    """Render one page to RGB uint8 (y-down, HxWx3)."""
    with fitz.open(pdf_path) as doc:
        page = doc[page_index]
        zoom = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB, alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        return arr.copy()


def binarize(gray: np.ndarray, method: str = "fixed", fixed_threshold: int = 128) -> np.ndarray:
    """Gray -> binary ink mask (True = ink). Otsu falls back to fixed on flat pages."""
    if method == "otsu" and gray.std() > 4.0:
        _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        return ink > 0
    return gray < fixed_threshold


def extract_image_regions(
    pdf_path: str, page_index: int, dpi: int = 300
) -> list[ImageRegion]:
    """Tag embedded raster images as continuous-tone regions (RGBA preserved)."""
    regions: list[ImageRegion] = []
    with fitz.open(pdf_path) as doc:
        page = doc[page_index]
        page_h_um = page.rect.height / 72.0 * UM_PER_INCH
        for block in page.get_image_info(xrefs=False):
            r = fitz.Rect(block["bbox"]) & page.rect
            if r.is_empty or r.width < 4 or r.height < 4:
                continue
            zoom = dpi / 72.0
            clip_pix = page.get_pixmap(
                matrix=fitz.Matrix(zoom, zoom), clip=r, colorspace=fitz.csRGB, alpha=True
            )
            arr = np.frombuffer(clip_pix.samples, dtype=np.uint8).reshape(
                clip_pix.height, clip_pix.width, 4
            )
            rgba = (arr.astype(np.float32) / 255.0).copy()
            to_um = UM_PER_INCH / 72.0
            bbox_um = (
                r.x0 * to_um,
                page_h_um - r.y1 * to_um,
                r.x1 * to_um,
                page_h_um - r.y0 * to_um,
            )
            regions.append(
                ImageRegion(bbox_um=bbox_um, rgba=rgba, colorspace=block.get("cs-name", "DeviceRGB"))
            )
    return regions


def raster_frontend(
    pdf_path: str,
    page_index: int,
    dpi: int = DEFAULT_DPI,
    threshold: str = "fixed",
    fixed_threshold: int = 128,
    tag_images: bool = False,
    exclude_images_from_trace: bool = False,
    sha256: str = "",
) -> PageIR:
    """Full raster path: render -> binarize -> trace -> PageIR (document space).

    With exclude_images_from_trace (dither mode), embedded-image regions are
    blanked before binarization: their content is carried as ImageRegions and
    materialized as dither geometry, not thresholded ink.
    """
    from stele.frontends.trace import trace_binary

    gray = render_page_gray(pdf_path, page_index, dpi)
    h_px, w_px = gray.shape
    images = extract_image_regions(pdf_path, page_index) if tag_images else []
    if exclude_images_from_trace and images:
        um_per_px_ = UM_PER_INCH / dpi
        for reg in images:
            x0, y0, x1, y1 = reg.bbox_um  # y-up document um
            c0, c1 = max(0, int(x0 / um_per_px_)), min(w_px, int(np.ceil(x1 / um_per_px_)))
            r0 = max(0, int((h_px * um_per_px_ - y1) / um_per_px_))
            r1 = min(h_px, int(np.ceil((h_px * um_per_px_ - y0) / um_per_px_)))
            gray[r0:r1, c0:c1] = 255
    ink_mask = binarize(gray, method=threshold, fixed_threshold=fixed_threshold)
    um_per_px = UM_PER_INCH / dpi
    polys = trace_binary(ink_mask, um_per_px, height_px=h_px)
    return PageIR(
        source=SourceRef(
            pdf_path=str(pdf_path),
            page_index=page_index,
            frontend=f"raster@{dpi}dpi/{threshold}",
            sha256=sha256,
        ),
        size_um=(w_px * um_per_px, h_px * um_per_px),
        ink=polys,
        images=images,
    )
