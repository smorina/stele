"""Through-the-microscope simulation of a built plate region.

Shared by `stele simulate` and the local UI's microscope panel so both show
the same picture and the same measured numbers: KLayout renders the GDS
region (never the source PDF), the reader profile's Airy PSF blurs it, the
result is downsampled to eye-limited sampling at the profile magnification,
and Michelson stroke contrast is binned by stroke width against the
profile's criterion. Changing NA, wavelength or magnification here needs no
rebuild — the geometry is fixed; only the optics move.
"""

from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image

from stele.config.profiles import ReaderProfile
from stele.verify.readability import airy_psf, simulate_view, stroke_contrast
from stele.verify.renderback import rasterize_cell_hierarchical

DEFAULT_REGION_UM = (400.0, 300.0)  # (w, h) of the default viewport
SIM_UM_PER_PX = 0.1


def page_cells(layout) -> list[str]:
    """Materialized page cells of a plate, in name order (PAGE_0000, ...)."""
    return sorted(c.name for c in layout.each_cell() if c.name.startswith("PAGE_"))


def default_region(layout, cell_name: str, w_um: float = DEFAULT_REGION_UM[0],
                   h_um: float = DEFAULT_REGION_UM[1]) -> tuple[float, float, float, float]:
    """A viewport (x0, y0, w, h) in page micrometres centered on the cell."""
    cell = layout.cell(cell_name)
    if cell is None:
        raise ValueError(f"cell {cell_name!r} not found")
    b = cell.dbbox()
    return ((b.left + b.right) / 2 - w_um / 2, (b.bottom + b.top) / 2 - h_um / 2, w_um, h_um)


def simulate_region(
    layout,
    cell_name: str,
    region_um: tuple[float, float, float, float],
    reader: ReaderProfile,
    layer: int = 1,
    datatype: int = 0,
    polarity: str = "clear_field",
    defocus_um: float = 0.0,
    um_per_px: float = SIM_UM_PER_PX,
    upscale: int = 4,
) -> dict:
    """Render, blur, measure. Returns the eye-limited view as PNG bytes plus
    the contrast bins and the reader verdict; nothing here reads the PDF."""
    x0, y0, w, h = region_um
    if w <= 0 or h <= 0:
        raise ValueError("the simulation region must have positive width and height")
    if w * h / (um_per_px * um_per_px) > 4e7:
        raise ValueError("the simulation region is too large; keep it under ~2 x 2 mm")
    ink = rasterize_cell_hierarchical(
        layout, cell_name, (x0, y0, x0 + w, y0 + h), 1.0 / um_per_px, layer, datatype
    )
    # contrast is measured on the clear-field-convention image (see
    # readability.contrast_gate): the dark-field image is its complement
    transmission = 1.0 - (ink > 0).astype(np.float32)
    psf = airy_psf(reader.numerical_aperture, reader.wavelength_nm / 1000.0, um_per_px)
    intensity = cv2.filter2D(transmission, -1, psf.astype(np.float32),
                             borderType=cv2.BORDER_REPLICATE)
    contrast = stroke_contrast(ink, intensity, um_per_px)
    crit = reader.contrast_criterion
    legible = [b["stroke_um"] for b in contrast["bins"] if b["michelson"] >= crit]
    eye_view, eye_um = simulate_view(ink, um_per_px, reader, defocus_um, polarity)
    img = Image.fromarray((np.clip(eye_view, 0, 1) * 255).astype(np.uint8))
    if upscale > 1:
        img = img.resize((img.width * upscale, img.height * upscale), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return {
        "png": buf.getvalue(),
        "cell": cell_name,
        "region_um": [round(v, 3) for v in (x0, y0, w, h)],
        "polarity": polarity,
        "reader": {
            "name": reader.name,
            "magnification": reader.magnification,
            "numerical_aperture": reader.numerical_aperture,
            "wavelength_nm": reader.wavelength_nm,
            "contrast_criterion": crit,
        },
        "defocus_um": defocus_um,
        "rayleigh_um": round(reader.rayleigh_resolution_um, 4),
        "eye_um_per_px": round(eye_um, 4),
        "view_px": [img.width // max(1, upscale), img.height // max(1, upscale)],
        "ink_fraction": round(float((ink > 0).mean()), 4),
        "contrast": contrast,
        "legible_bins": legible,
        "assessed": bool(contrast["bins"]),
        "pass": bool(legible),
    }
