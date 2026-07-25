"""DRC against the fab profile, run on KLayout's production DRC engine.

Progress-review finding 3: the earlier raster-morphology checks had material
false-negative regions (appendages attached to a surviving component; open
narrow channels whose background touches the border). KLayout's
Region.width_check / space_check are true local edge-pair checks with none of
those blind spots, and they add engine independence (the geometry under test
was written by gdstk).

Occupancy is audited STRUCTURALLY: content pages live in PAGE_* cells, plate
furniture in the FURNITURE cell — so reserved-region intrusions by content are
detectable instead of being masked by legitimate furniture.

Severity: violations are always reported; whether they fail the build is the
fab profile's call (`drc_gate: warn|fail`), surfaced as pass_with_warnings —
never a silent unqualified pass.
"""

from __future__ import annotations

import klayout.db as kdb
import numpy as np

from stele.config.profiles import FabProfile
from stele.layout.engine import PlatePlan, Rect


def _region_of(layout: kdb.Layout, cell: kdb.Cell, layer: int, datatype: int) -> kdb.Region:
    li = layout.layer(layer, datatype)
    region = kdb.Region(cell.begin_shapes_rec(li))
    region.merge()
    return region


def klayout_width_space(
    layout: kdb.Layout,
    cell_name: str,
    layer: int,
    datatype: int,
    min_width_um: float,
    min_space_um: float,
    direct_only: bool = False,
) -> dict:
    """Edge-pair width/space checks on a cell's merged geometry.

    direct_only: check only the cell's own shapes (no hierarchy). Used for
    dithered pages, where the full merged check measured 647 s/page while the
    dither content is DRC-clean BY CONSTRUCTION (full-cell sites at pitch >=
    the fab floor; validated on assembled pages at 1 um and 2 um pitch — 0/0
    violations). Tile cells are checked once per plate; the text<->image
    boundary interaction goes through klayout_width_space_banded.
    """
    cell = layout.cell(cell_name)
    if cell is None:
        raise ValueError(f"cell {cell_name!r} not found")
    dbu = layout.dbu
    if direct_only:
        region = kdb.Region(cell.shapes(layout.layer(layer, datatype)))
        region.merge()
    else:
        region = _region_of(layout, cell, layer, datatype)
    w_pairs = region.width_check(int(round(min_width_um / dbu)))
    s_pairs = region.space_check(int(round(min_space_um / dbu)))
    return {
        "width_violations": w_pairs.count(),
        "space_violations": s_pairs.count(),
        "polygons": region.count(),
    }


def klayout_width_space_banded(
    layout: kdb.Layout,
    cell_name: str,
    layer: int,
    datatype: int,
    min_width_um: float,
    min_space_um: float,
    bands_um: list[tuple[float, float, float, float]],
    margin_um: float = 2.0,
) -> dict:
    """Width/space check restricted to bands (e.g. image-region borders).

    Clipping manufactures cut edges that would false-flag, so the region is
    clipped to the bands EXPANDED by margin and only violations whose markers
    fall inside the unexpanded bands are counted."""
    cell = layout.cell(cell_name)
    dbu = layout.dbu
    region = _region_of(layout, cell, layer, datatype)

    def _boxes(rects, grow):
        r = kdb.Region()
        for x0, y0, x1, y1 in rects:
            r.insert(kdb.Box(
                int((x0 - grow) / dbu), int((y0 - grow) / dbu),
                int((x1 + grow) / dbu), int((y1 + grow) / dbu),
            ))
        r.merge()
        return r

    outer = _boxes(bands_um, margin_um)
    clipped = region & outer
    inner_dbu = [
        (int(x0 / dbu), int(y0 / dbu), int(x1 / dbu), int(y1 / dbu))
        for x0, y0, x1, y1 in bands_um
    ]

    def _count(pairs) -> int:
        n = 0
        for ep in pairs.each():
            b = ep.bbox()
            if any(
                b.right > x0 and b.left < x1 and b.top > y0 and b.bottom < y1
                for x0, y0, x1, y1 in inner_dbu
            ):
                n += 1
        return n

    return {
        "width_violations": _count(clipped.width_check(int(round(min_width_um / dbu)))),
        "space_violations": _count(clipped.space_check(int(round(min_space_um / dbu)))),
    }


def occupancy_audit(
    gds_path: str,
    plan: PlatePlan,
    layer: int,
    datatype: int,
    cell_names: dict[str, str] | None = None,
) -> dict:
    """Structural audit of the CONTENT cell:

    1. no direct (un-referenced) shapes — every polygon must belong to a
       PAGE_* cell or the FURNITURE cell, so planted geometry has no home;
    2. every PAGE_* instance bbox inside the active area and outside every
       reserved rect;
    3. with cell_names given, every PAGE_* instance matched to its PLANNED
       placement and its read-back bbox contained in that placement's slot
       block — escaped geometry (e.g. a bad channel transform) cannot reach a
       neighboring page, and unplanned instances have no home (finding 4);
    4. FURNITURE geometry confined to the reserved rects.
    """
    layout = kdb.Layout()
    layout.read(gds_path)
    li = layout.layer(layer, datatype)
    content = layout.cell("CONTENT")
    result: dict = {"direct_shapes": 0, "page_instance_violations": [],
                    "slot_violations": [], "furniture_violations": 0}
    if content is None:
        result["pass"] = False
        result["error"] = "CONTENT cell missing"
        return result

    result["direct_shapes"] = sum(1 for _ in content.each_shape(li))

    # origins matched at 10 nm: written origins are DBU-snapped (1 nm), and
    # distinct placements differ by whole slots (mm) — no collision risk
    planned: dict[tuple[str, float, float], Rect] = {}
    if cell_names:
        for pl in plan.placements:
            key = (cell_names[pl.page_key], round(pl.origin[0], 2), round(pl.origin[1], 2))
            planned[key] = pl.slot

    reserved = list(plan.reserved.values())
    a = plan.active
    for inst in content.each_inst():
        child = layout.cell(inst.cell_index).name
        if not child.startswith("PAGE_"):
            continue
        b = inst.dbbox()
        r = Rect(b.left, b.bottom, b.right, b.top)
        outside_active = (
            r.x0 < a.x0 - 1e-6 or r.y0 < a.y0 - 1e-6 or r.x1 > a.x1 + 1e-6 or r.y1 > a.y1 + 1e-6
        )
        in_reserved = any(r.intersects(rv) for rv in reserved)
        if outside_active or in_reserved:
            result["page_instance_violations"].append(
                {"cell": child, "bbox_um": r.as_tuple(), "outside_active": outside_active,
                 "in_reserved": in_reserved}
            )
        if planned:
            d = inst.dcplx_trans.disp
            slot = planned.get((child, round(d.x, 2), round(d.y, 2)))
            if slot is None:
                result["slot_violations"].append(
                    {"cell": child, "origin_um": (d.x, d.y), "reason": "unplanned instance"}
                )
            elif not _contains(slot, r, tol=5e-3):
                result["slot_violations"].append(
                    {"cell": child, "bbox_um": r.as_tuple(), "slot_um": slot.as_tuple(),
                     "reason": "geometry outside its planned slot block"}
                )

    furniture = layout.cell("FURNITURE")
    if furniture is not None:
        it = furniture.begin_shapes_rec(li)
        while not it.at_end():
            b = it.shape().dbbox()
            r = Rect(b.left, b.bottom, b.right, b.top)
            if not any(_contains(rv, r) for rv in reserved):
                result["furniture_violations"] += 1
            it.next()

    result["pass"] = bool(
        result["direct_shapes"] == 0
        and not result["page_instance_violations"]
        and not result["slot_violations"]
        and result["furniture_violations"] == 0
    )
    return result


def _contains(outer: Rect, inner: Rect, tol: float = 1e-6) -> bool:
    return (
        inner.x0 >= outer.x0 - tol
        and inner.y0 >= outer.y0 - tol
        and inner.x1 <= outer.x1 + tol
        and inner.y1 <= outer.y1 + tol
    )


def budget_check(fab: FabProfile, gds_size_mb: float, n_cells: int, max_vertices_seen: int) -> dict:
    return {
        "file_size_mb": gds_size_mb,
        "file_size_ok": gds_size_mb <= fab.max_file_size_mb,
        "cells": n_cells,
        "cells_ok": n_cells <= fab.max_cells,
        "max_polygon_vertices": max_vertices_seen,
        "vertices_ok": max_vertices_seen <= fab.max_vertices_per_polygon,
    }


def density_check(plate_binary: np.ndarray, fab: FabProfile, window_px: int = 64) -> dict:
    """Windowed pattern density, GATED against the fab bounds over ink-bearing
    windows (an archive plate is mostly empty; empty windows are not a
    density-uniformity concern for a warn-level generic profile)."""
    m = (plate_binary > 0).astype(np.float32)
    h = (m.shape[0] // window_px) * window_px
    w = (m.shape[1] // window_px) * window_px
    if h == 0 or w == 0:
        return {"min": 0.0, "max": 0.0, "mean": 0.0, "pass": True}
    blocks = m[:h, :w].reshape(h // window_px, window_px, w // window_px, window_px)
    dens = blocks.mean(axis=(1, 3))
    occupied = dens[dens > 0.0]
    dmax = float(dens.max())
    ok = bool(dmax <= fab.density_max and (occupied.size == 0 or occupied.min() >= fab.density_min))
    return {
        "min_occupied": round(float(occupied.min()), 4) if occupied.size else 0.0,
        "max": round(dmax, 4),
        "mean": round(float(dens.mean()), 4),
        "pass": ok,
    }
