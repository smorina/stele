"""M0 demo + acceptance check: one patent page -> pseudopage GDS.

Run: uv run python scripts/m0_demo.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import gdstk

from stele.backends.gdsii import GdsStats, add_page_cell, build_library
from stele.backends.preview import rasterize, save_png
from stele.frontends.raster import raster_frontend
from stele.verify.geometry_gates import stroke_width_stats
from stele.verify.renderback import gds_summary, read_polygons_um

PDF = os.path.join(os.path.dirname(__file__), "..", "..", "document.pdf")
OUT = os.path.join(os.path.dirname(__file__), "..", "out")

# Target pseudopage 1.98 x 2.56 mm from 8.5 x 11 in (patent FIG. 12 example).
PSEUDO_W_UM, PSEUDO_H_UM = 1980.0, 2560.0


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    page = raster_frontend(PDF, page_index=0, dpi=900, threshold="fixed")
    t_frontend = time.time() - t0

    scale = min(PSEUDO_W_UM / page.size_um[0], PSEUDO_H_UM / page.size_um[1])
    print(f"page size: {page.size_um[0]:.0f} x {page.size_um[1]:.0f} um doc space")
    print(f"reduction: {1/scale:.2f}:1  | ink polygons: {len(page.ink)}")

    t1 = time.time()
    lib = build_library()
    stats = GdsStats()
    cell = add_page_cell(lib, "PAGE_0001", page, scale, stats)
    top = lib.new_cell("PLATE")
    top.add(gdstk.Reference(cell, (0.0, 0.0)))
    gds_path = os.path.join(OUT, "m0_page1.gds")
    lib.write_gds(gds_path)
    t_backend = time.time() - t1

    size_mb = os.path.getsize(gds_path) / 1e6
    print(f"gds polygons: {stats.polygons}  vertices: {stats.vertices}  size: {size_mb:.2f} MB")
    print(f"frontend {t_frontend:.1f}s  backend {t_backend:.1f}s  total {time.time()-t0:.1f}s")

    # --- independent read-back (klayout) ---
    summary = gds_summary(gds_path)
    print(f"klayout summary: {summary}")
    polys, bbox = read_polygons_um(gds_path)
    w_mm = (bbox[2] - bbox[0]) / 1000.0
    h_mm = (bbox[3] - bbox[1]) / 1000.0
    print(f"read-back content bbox: {w_mm:.3f} x {h_mm:.3f} mm  ({len(polys)} polygons)")

    # pseudopage extent = scaled page frame; content (with margins) must fit inside it
    pseudo_w = page.size_um[0] * scale
    pseudo_h = page.size_um[1] * scale
    print(f"pseudopage extent: {pseudo_w/1000:.3f} x {pseudo_h/1000:.3f} mm")
    ok_extent = (
        abs(pseudo_w - PSEUDO_W_UM) / PSEUDO_W_UM < 0.01
        and abs(pseudo_h - PSEUDO_H_UM) / PSEUDO_H_UM < 0.01
    )
    ok_fits = (
        bbox[0] >= -1e-6
        and bbox[1] >= -1e-6
        and bbox[2] <= pseudo_w + 1e-6
        and bbox[3] <= pseudo_h + 1e-6
    )
    ok_size = size_mb < 5.0
    ok_time = (time.time() - t0) < 30.0

    # --- previews (from the independent read-back, not the writer's data) ---
    full = rasterize(polys, bbox, px_per_um=2.0)
    save_png(255 - full, os.path.join(OUT, "m0_pseudopage.png"))
    # zoom: title area, upper ~15% of the page
    zx0, zy1 = bbox[0] + 0.28 * (bbox[2] - bbox[0]), bbox[3] - 0.02 * (bbox[3] - bbox[1])
    zoom_box = (zx0, zy1 - 220.0, zx0 + 900.0, zy1)
    zoom = rasterize(polys, zoom_box, px_per_um=20.0)
    save_png(255 - zoom, os.path.join(OUT, "m0_zoom_title.png"))

    # --- measured stroke widths on plate-scale geometry ---
    um_per_px = 0.1
    hi = rasterize(polys, bbox, px_per_um=1.0 / um_per_px)
    sw = stroke_width_stats(hi, um_per_px)
    print(f"stroke widths (um): {sw}")

    # M0 acceptance: body-text strokes measure 2-3 um on plate (median as proxy)
    ok_strokes = 1.5 <= sw.get("p50_um", 0.0) <= 3.5
    checks = {
        "pseudopage extent 1.98x2.56mm +/-1% (uniform reduction)": ok_extent,
        "body-text strokes ~2-3um (p50 in [1.5, 3.5])": ok_strokes,
        "content fits inside pseudopage": ok_fits,
        "gds < 5 MB": ok_size,
        "build < 30 s": ok_time,
        "read-back has polygons": len(polys) > 100,
    }
    print("\nM0 acceptance:")
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
