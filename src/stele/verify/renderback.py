"""Independent GDS read-back via KLayout (different codebase from the gdstk writer)."""

from __future__ import annotations

import klayout.db as kdb
import numpy as np


def open_layout(gds_path: str) -> "kdb.Layout":
    layout = kdb.Layout()
    layout.read(gds_path)
    return layout


def read_polygons_um(
    gds_path: str | "kdb.Layout",
    cell_name: str | None = None,
    layer: int = 1,
    datatype: int = 0,
) -> tuple[list[np.ndarray], tuple[float, float, float, float]]:
    """Read flattened polygons (um, y-up) from a GDS via klayout.

    Accepts a path or an already-open Layout (one read shared across many
    cells). Returns (polygons, content_bbox_um). Polygons are hole-free
    (holes are resolved to keyhole cuts if present). Raises on read errors.
    """
    if isinstance(gds_path, kdb.Layout):
        layout = gds_path
    else:
        layout = open_layout(gds_path)
    cell = layout.cell(cell_name) if cell_name else layout.top_cell()
    if cell is None:
        raise ValueError(f"cell {cell_name!r} not found in {gds_path}")
    li = layout.layer(layer, datatype)
    dbu = layout.dbu  # um per database unit
    polys: list[np.ndarray] = []
    it = cell.begin_shapes_rec(li)
    while not it.at_end():
        shape = it.shape()
        if shape.is_polygon() or shape.is_box() or shape.is_path():
            poly = shape.polygon.transformed(it.trans())
            if poly.holes() > 0:
                poly = poly.resolved_holes()
            pts = np.array([[pt.x * dbu, pt.y * dbu] for pt in poly.each_point_hull()])
            if len(pts) >= 3:
                polys.append(pts)
        it.next()
    b = cell.dbbox()
    return polys, (b.left, b.bottom, b.right, b.top)


def rasterize_cell_hierarchical(
    layout: "kdb.Layout",
    cell_name: str,
    bbox_um: tuple[float, float, float, float],
    px_per_um: float,
    layer: int = 1,
    datatype: int = 0,
    supersample: int = 2,
    _cache: dict | None = None,
) -> np.ndarray:
    """Binary render of a cell exploiting hierarchy: each unique child cell is
    rendered and blitted at its instance offsets (translation-only instances;
    anything else falls back to flattening that instance). Only shared
    halftone tile cells are retained across calls. Page-specific patch cells
    and page cells can be tens of MB per resolution, so caching them makes
    verification memory grow with page count.

    Flattening a dithered page means a Python loop over ~10^6 polygons
    (minutes, measured); hierarchical rendering visits ~10^3 unique tile
    cells + ~10^4 instances (seconds). Still reads only the GDS.
    """
    from stele.backends.preview import rasterize

    cell = layout.cell(cell_name)
    if cell is None:
        raise ValueError(f"cell {cell_name!r} not found")
    li = layout.layer(layer, datatype)
    dbu = layout.dbu
    cache: dict = _cache if _cache is not None else {}

    xmin, ymin, xmax, ymax = bbox_um
    w = max(1, int(round((xmax - xmin) * px_per_um)))
    h = max(1, int(round((ymax - ymin) * px_per_um)))
    out = np.zeros((h, w), dtype=np.uint8)

    # direct shapes of this cell only
    direct: list[np.ndarray] = []
    for shape in cell.each_shape(li):
        poly = shape.polygon
        if poly is None:
            continue
        if poly.holes() > 0:
            poly = poly.resolved_holes()
        pts = np.array([[pt.x * dbu, pt.y * dbu] for pt in poly.each_point_hull()])
        if len(pts) >= 3:
            direct.append(pts)
    if direct:
        out |= rasterize(direct, bbox_um, px_per_um, supersample=supersample) > 0

    for inst in cell.each_inst():
        child = layout.cell(inst.cell_index)
        t = inst.dcplx_trans
        if t.is_mirror() or abs(t.angle) > 1e-9 or abs(t.mag - 1.0) > 1e-9:
            # non-translation: flatten just this instance
            polys, _ = read_polygons_um(layout, cell_name=child.name, layer=layer,
                                        datatype=datatype)
            moved = [p + np.array([t.disp.x, t.disp.y]) for p in polys]
            out |= rasterize(moved, bbox_um, px_per_um, supersample=supersample) > 0
            continue
        b = child.dbbox()
        key = (child.cell_index(), round(px_per_um, 6))
        cacheable = child.name.startswith("HT_")
        if cacheable and key in cache:
            child_bm, cb = cache[key]
        else:
            cb = (b.left, b.bottom, b.right, b.top)
            child_bm = rasterize_cell_hierarchical(
                layout, child.name, cb, px_per_um, layer, datatype, supersample, cache
            )
            if cacheable:
                cache[key] = (child_bm, cb)
        ox = (t.disp.x + cb[0] - xmin) * px_per_um
        oy_top = (ymax - (t.disp.y + cb[3])) * px_per_um
        c0, r0 = int(round(ox)), int(round(oy_top))
        ch, cw = child_bm.shape
        rr0, cc0 = max(0, r0), max(0, c0)
        rr1, cc1 = min(h, r0 + ch), min(w, c0 + cw)
        if rr1 > rr0 and cc1 > cc0:
            out[rr0:rr1, cc0:cc1] |= child_bm[rr0 - r0 : rr1 - r0, cc0 - c0 : cc1 - c0]
    return (out > 0).astype(np.uint8) * 255


def top_transform_mirrored(layout: "kdb.Layout") -> bool:
    """Whether the top cell's single content instance mirrors the plate —
    derived from the GDS itself (never from the declaration; catching a wrong
    transform is the point of the chirality check)."""
    top = layout.top_cell()
    insts = list(top.each_inst())
    if not insts:
        return False
    return bool(insts[0].dcplx_trans.is_mirror())


def render_plate(
    layout: "kdb.Layout",
    bbox_um: tuple[float, float, float, float],
    px_per_um: float,
    layer: int = 1,
    datatype: int = 0,
    plate_w_um: float | None = None,
    cache: dict | None = None,
) -> np.ndarray:
    """Render the plate as manufactured: CONTENT hierarchically (translation-
    only tree), then the top-level mirror transform applied to the bitmap.
    bbox_um is in PLATE coordinates."""
    mirrored = top_transform_mirrored(layout)
    if mirrored:
        assert plate_w_um is not None, "plate width required to unmirror the viewport"
        content_bbox = (plate_w_um - bbox_um[2], bbox_um[1], plate_w_um - bbox_um[0], bbox_um[3])
    else:
        content_bbox = bbox_um
    bm = rasterize_cell_hierarchical(
        layout, "CONTENT", content_bbox, px_per_um, layer, datatype, _cache=cache
    )
    return bm[:, ::-1].copy() if mirrored else bm


def max_vertices_per_cell(layout: "kdb.Layout", layer: int = 1, datatype: int = 0) -> int:
    """Max polygon vertex count across all cells' DIRECT shapes — instances
    don't change vertex counts, so no flattening (a dithered plate flattens to
    ~10^6 polygons; the unique-cell walk is ~10^5)."""
    li = layout.layer(layer, datatype)
    worst = 0
    for ci in layout.each_cell():
        for shape in ci.each_shape(li):
            poly = shape.polygon
            if poly is not None:
                worst = max(worst, poly.num_points_hull())
    return worst


def gds_summary(gds_path: str) -> dict:
    layout = kdb.Layout()
    layout.read(gds_path)
    top = layout.top_cell()
    return {
        "cells": layout.cells(),
        "top_cell": top.name if top else None,
        "dbu_um": layout.dbu,
        "bbox_um": tuple(
            round(v, 4)
            for v in (top.dbbox().left, top.dbbox().bottom, top.dbbox().right, top.dbbox().top)
        )
        if top
        else None,
    }
