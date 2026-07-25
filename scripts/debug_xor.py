"""Debug the XOR mismatch: dump candidate/reference/xor bitmaps for one page."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np
from PIL import Image

from stele.backends.preview import rasterize
from stele.verify.reference import reference_ink_mask
from stele.verify.renderback import read_polygons_um
from stele.verify.xordiff import xor_compare

GDS = os.path.join(os.path.dirname(__file__), "..", "out", "patent_demo.gds")
PDF = os.path.join(os.path.dirname(__file__), "..", "..", "document.pdf")
OUT = os.path.join(os.path.dirname(__file__), "..", "out")

PX_PER_UM = 2.0
SCALE = 0.009162  # from report
W_UM = 215900.0 * SCALE
H_UM = 279400.0 * SCALE

cand_polys, bbox = read_polygons_um(GDS, cell_name="PAGE_0000")
print("candidate content bbox:", [round(v, 1) for v in bbox])
cand = rasterize(cand_polys, (0.0, 0.0, W_UM, H_UM), PX_PER_UM, supersample=2)
w_px = int(round(W_UM * PX_PER_UM))
ref = reference_ink_mask(PDF, 0, w_px).astype(np.uint8) * 255
print("cand shape:", cand.shape, "ref shape:", ref.shape)
print("cand ink:", (cand > 0).sum(), "ref ink:", (ref > 0).sum())

res = xor_compare(cand, ref, keep_map=True)
print("metrics:", res.to_dict())

h = min(cand.shape[0], ref.shape[0])
w = min(cand.shape[1], ref.shape[1])
rgb = np.zeros((h, w, 3), dtype=np.uint8)
rgb[..., 0] = 255 - cand[:h, :w]  # candidate in cyan-ish absence
rgb[..., 1] = 255 - ref[:h, :w]
rgb[..., 2] = 255 - ref[:h, :w]
Image.fromarray(rgb).save(os.path.join(OUT, "debug_overlay.png"))
# crop of the title area for detail
crop = rgb[0 : h // 6, w // 4 : w]
Image.fromarray(crop).resize((crop.shape[1] * 2, crop.shape[0] * 2), Image.NEAREST).save(
    os.path.join(OUT, "debug_overlay_crop.png")
)
print("wrote debug_overlay.png / debug_overlay_crop.png")
