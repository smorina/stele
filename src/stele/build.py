"""Build orchestration: manifest -> plate GDS + preview + verified report."""

from __future__ import annotations

import gc
import json
import math
import os
import platform
import time
from collections import OrderedDict
from datetime import datetime
from importlib import metadata
from typing import Callable

import gdstk
import numpy as np

from stele import __version__
from stele.backends.gdsii import GdsStats, page_to_polygons, shapely_to_gdstk
from stele.backends.preview import save_png
from stele.config.manifest import (
    JobManifest,
    load_manifest,
    resolved_config_dump,
    sha256_file,
)
from stele.config.profiles import Profiles
from stele.config.validate import validate_profiles
from stele.frontends.raster import raster_frontend
from stele.ingest.normalize import collect_pages
from stele.ir.model import Orientation, Polarity
from stele.layout.engine import PlatePlan, plan_plates
from stele.layout.fiducials import centered_orientation_glyph, fiducial_cross, stroke_text
from stele.verify.chirality import check_chirality
from stele.verify.drc import (
    budget_check,
    density_check,
    klayout_width_space,
    klayout_width_space_banded,
    occupancy_audit,
)
from stele.verify.geometry_gates import stroke_width_stats
from stele.verify.reference import image_regions_pt, reference_masks
from stele.verify.renderback import (
    max_vertices_per_cell,
    open_layout,
    rasterize_cell_hierarchical,
    render_plate,
)
from stele.verify.xordiff import hysteresis_compare

VERIFY_PX_PER_UM = 2.0  # 0.5 um/px at plate scale
DEFECT_MISMATCH_GATE = 0.02
# Keep only the immediately previous source page. The default one-tier layout
# never reuses a PageIR, while each retained IR can hold dense Shapely geometry
# and, for photos, a ~135 MB 300-DPI float32 RGBA image. A one-entry cache still
# avoids re-ingestion for consecutive reuse without scaling memory with pages.
PAGE_IR_CACHE_CAP = 1
READABILITY_WINDOW_UM = (300.0, 400.0)  # (h, w) text sample per tier


class Cancelled(RuntimeError):
    """Raised at a safe build boundary when a caller requests cancellation."""


ProgressCallback = Callable[[str, int, int, str], object]


def _progress(
    callback: ProgressCallback | None,
    stage: str,
    done: int,
    total: int,
    detail: str,
) -> None:
    """Emit optional progress without changing the command-line build path."""
    if callback is not None and callback(stage, done, total, detail) in (False, "cancel"):
        raise Cancelled("build cancelled")


def aggregate_status(statuses: list[str | None]) -> str:
    """Plate-set status, authoritative over EVERY plate (review finding 1).

    None marks a plate that was never verified: the set is then 'unverified',
    never a synthesized pass. Any failing plate fails the set regardless of
    how the first plate fared.
    """
    if any(s == "fail" for s in statuses):
        return "fail"
    if any(s is None for s in statuses):
        return "unverified"
    if any(s == "pass_with_warnings" for s in statuses):
        return "pass_with_warnings"
    return "pass"


def page_cell_assignments(plan: PlatePlan) -> dict[str, str]:
    """Deterministic page-key -> cell-name mapping (shared by build and verify)."""
    names: dict[str, str] = {}
    for pl in plan.placements:
        if pl.page_key not in names:
            names[pl.page_key] = f"PAGE_{len(names):04d}"
    return names


def build_job(
    manifest_path: str,
    verify: bool = True,
    preview: bool = True,
    progress: ProgressCallback | None = None,
) -> dict:
    t0 = time.time()
    _progress(progress, "preparing", 0, 1, "Reading the job and checking its settings")
    manifest, profiles = load_manifest(manifest_path)
    validation = validate_profiles(profiles)
    if not validation.ok:
        raise SystemExit("validation errors:\n  " + "\n  ".join(validation.errors))

    _progress(progress, "preparing", 0, 1, "Reading document page information")
    jobs = collect_pages(manifest.inputs)
    plans = plan_plates(profiles.fab, profiles.layout, jobs, fit_mode=profiles.content.fit_mode)
    _progress(
        progress,
        "preparing",
        1,
        1,
        f"Planned {len(jobs)} page(s) across {len(plans)} plate(s)",
    )
    orientation = Orientation(
        polarity=Polarity(profiles.layout.polarity),
        mirrored=profiles.layout.mirrored,
    )
    if orientation.polarity is not Polarity.CLEAR_FIELD:
        raise SystemExit("dark_field polarity lands in M4b; clear_field only for now")

    page_ir_cache: OrderedDict[tuple[str, int], object] = OrderedDict()
    plate_reports: list[dict] = []
    for plate_number, plan in enumerate(plans, start=1):
        gds_path = _plate_gds_path(manifest.output.gds, plan)
        plate_reports.append(
            _build_one_plate(
                manifest, profiles, plan, orientation, gds_path, page_ir_cache,
                verify=verify, preview=preview, progress=progress,
                plate_number=plate_number,
            )
        )

    statuses = [
        p.get("verify", {}).get("gates", {}).get("status") for p in plate_reports
    ]
    overall = aggregate_status(statuses)

    report: dict = {
        "stele_version": __version__,
        "maturity": "prototype",
        "handoff_status": (
            "software-verified candidate handoff; not production or fab-ready"
        ),
        "python": platform.python_version(),
        "dependencies": _dep_versions(),
        "config": resolved_config_dump(manifest, profiles),
        "profile_hashes": {
            k: sha256_file(os.path.join(manifest.base_dir, v) if not os.path.isabs(v) else v)
            for k, v in manifest.profiles.items()
        },
        "validation": {
            "warnings": validation.warnings,
            "info": validation.info,
            "unenforced_fields": UNENFORCED_FAB_FIELDS,
        },
        "orientation": {
            "polarity": orientation.polarity.value,
            "mirrored": orientation.mirrored,
            "tone_truth_table": orientation.describe_tone_chain(),
            "fab_tone_assumption": "positive-tone assumed (polygon = chrome retained); "
            "placeholder until a vendor deck states the exposure/tone convention",
        },
        "plate_set": {
            "plates": len(plans),
            "status": overall,
            "gds": [p["gds"]["path"] for p in plate_reports],
        },
        "plates": plate_reports,
    }
    # single-plate alias keys (the common case and the stable report schema)
    first = plate_reports[0]
    for key in ("halftone", "ingest", "placements", "plan", "gds", "verify", "preview"):
        if key in first:
            report[key] = first[key]
    report["timings_s"] = {
        "build": round(sum(p["timings_s"]["build"] for p in plate_reports), 1),
        "verify": round(sum(p["timings_s"].get("verify", 0) for p in plate_reports), 1),
        "preview": round(sum(p["timings_s"].get("preview", 0) for p in plate_reports), 1),
        "total": round(time.time() - t0, 1),
    }
    _progress(progress, "finishing", 0, 1, "Writing the reproducibility report")
    with open(manifest.output.report_path(), "w") as f:
        json.dump(report, f, indent=2, default=str)
    _progress(progress, "finishing", 1, 1, "Build complete")
    return report


def _plate_gds_path(base: str, plan: PlatePlan) -> str:
    if plan.plate_count == 1:
        return base
    root, ext = os.path.splitext(base)
    return f"{root}.p{plan.plate_index + 1:02d}{ext}"


def _build_one_plate(
    manifest: JobManifest,
    profiles: Profiles,
    plan: PlatePlan,
    orientation: Orientation,
    gds_path: str,
    page_ir_cache: dict,
    verify: bool,
    preview: bool,
    progress: ProgressCallback | None = None,
    plate_number: int = 1,
) -> dict:
    t0 = time.time()
    os.makedirs(os.path.dirname(gds_path) or ".", exist_ok=True)
    partial_gds_path = gds_path + ".partial"
    writer = gdstk.GdsWriter(
        partial_gds_path,
        name="STELE",
        unit=1e-6,
        precision=profiles.fab.dbu_nm * 1e-9,
        max_points=min(profiles.fab.max_vertices_per_polygon, 8190),
        timestamp=datetime(2026, 1, 1),
    )
    stats = GdsStats()
    content = gdstk.Cell("CONTENT")
    cell_names = page_cell_assignments(plan)
    cell_by_key: dict[str, str] = {}
    dithering = profiles.content.image_mode == "dither"
    halftone_stats: dict[str, dict] = {}
    tile_cells: dict[bytes, str] = {}  # content key -> streamed cell name
    if dithering:
        from stele.passes.dither import bitmap_to_rects_um
        from stele.passes.halftone import dither_region, load_mask

        ht_mask = load_mask(profiles.content.dither_mask)
        ht_pitch = profiles.content.dither_pitch_um or max(
            profiles.fab.min_feature_um, profiles.fab.min_space_um
        )
        ht_exact_budget = [max(0, profiles.content.max_exact_tiles_per_plate)]
    unique_pages = len({pl.page_key for pl in plan.placements})
    materialized = 0
    _progress(
        progress,
        "building",
        0,
        max(1, unique_pages),
        f"Starting plate {plate_number} of {plan.plate_count}",
    )
    for pl in plan.placements:
        if pl.page_key not in cell_by_key:
            _progress(
                progress,
                "building",
                materialized,
                max(1, unique_pages),
                f"Converting page {pl.job.ordinal + 1} on plate {plate_number}",
            )
            ck = (pl.job.pdf_path, pl.job.page_index)
            if ck not in page_ir_cache:
                page_ir_cache[ck] = raster_frontend(
                    pl.job.pdf_path,
                    pl.job.page_index,
                    dpi=profiles.content.dpi,
                    threshold=profiles.content.threshold,
                    fixed_threshold=profiles.content.fixed_threshold,
                    tag_images=profiles.content.tag_images or dithering,
                    exclude_images_from_trace=dithering,
                    sha256=pl.job.pdf_sha256,
                )
                while len(page_ir_cache) > PAGE_IR_CACHE_CAP:
                    page_ir_cache.popitem(last=False)
            else:
                page_ir_cache.move_to_end(ck)
            page_ir = page_ir_cache[ck]
            cell = gdstk.Cell(cell_names[pl.page_key])
            for p in page_to_polygons(page_ir, pl.scale):
                cell.add(p)
                stats.polygons += 1
                stats.vertices += len(p.points)
            if dithering and page_ir.images:
                n_sites = n_patches = n_exact = n_fallback = n_sub_site = 0
                # patches live in a child cell: they are clean only WITH their
                # tile context (isolated, diagonal patch pairs read as corner
                # contacts — 3,802 false width+space flags measured), so the
                # page's direct-only DRC must see traced text alone
                patch_cell = gdstk.Cell(f"{cell_names[pl.page_key]}_PATCH")
                regions_to_dither = list(page_ir.images)
                triad_records: list[dict] = []
                if profiles.content.color_mode == "rgb_triad":
                    from stele.passes.colorsep import (
                        ChannelTransform,
                        check_triad_containment,
                        registration_cross_um,
                        split_triad,
                        triad_boxes,
                    )

                    xf = {
                        ch: ChannelTransform(**cfg)
                        for ch, cfg in profiles.content.channel_transforms.items()
                    }
                    regions_to_dither = []
                    for region in page_ir.images:
                        check_triad_containment(
                            page_ir.size_um, triad_boxes(region.bbox_um, xf)
                        )
                        for tc in split_triad(region, xf):
                            regions_to_dither.append(tc.region)
                            bb = tc.region.bbox_um
                            triad_records.append(
                                {"channel": tc.channel,
                                 "bbox_um": [round(v, 3) for v in bb],
                                 "transform": tc.transform.to_dict()}
                            )
                            for ring in registration_cross_um(
                                tuple(v * pl.scale for v in bb)
                            ):
                                patch_cell.add(gdstk.Polygon(
                                    ring, layer=profiles.fab.layer,
                                    datatype=profiles.fab.datatype))
                for region in regions_to_dither:
                    geo = dither_region(
                        region, pl.scale, ht_pitch, ht_mask, exact_budget=ht_exact_budget
                    )
                    for key, bm in geo.tile_bitmaps.items():
                        if key not in tile_cells:
                            tile_name = f"HT_{len(tile_cells):05d}"
                            tc = gdstk.Cell(tile_name)
                            for x0, y0, x1, y1 in bitmap_to_rects_um(bm, ht_pitch):
                                tc.add(gdstk.Polygon(
                                    [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                                    layer=profiles.fab.layer, datatype=profiles.fab.datatype))
                            writer.write(tc)
                            tile_cells[key] = tile_name
                    for key, x, y in geo.tile_placements:
                        cell.add(gdstk.Reference(tile_cells[key], (x, y)))
                        stats.references += 1
                    for x0, y0, x1, y1 in geo.patch_squares_um:
                        patch_cell.add(gdstk.Polygon(
                            [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                            layer=profiles.fab.layer, datatype=profiles.fab.datatype))
                        stats.polygons += 1
                    n_sites += geo.n_sites
                    n_patches += geo.n_patches
                    n_exact += geo.n_exact_tiles
                    n_fallback += geo.n_budget_fallbacks
                    n_sub_site += int(geo.skipped_sub_site)
                writer.write(patch_cell)
                cell.add(gdstk.Reference(patch_cell.name, (0.0, 0.0)))
                stats.references += 1
                halftone_stats[cell_names[pl.page_key]] = {
                    "regions": len(page_ir.images), "sites": n_sites, "patches": n_patches,
                    "exact_tiles": n_exact, "budget_fallback_tiles": n_fallback,
                    "sub_site_regions_skipped": n_sub_site,
                    "triads": triad_records,
                }
            writer.write(cell)
            stats.cells += 1
            cell_by_key[pl.page_key] = cell.name
            materialized += 1
            _progress(
                progress,
                "building",
                materialized,
                max(1, unique_pages),
                f"Converted {materialized} of {unique_pages} page(s)",
            )
        content.add(gdstk.Reference(cell_by_key[pl.page_key], pl.origin))
        stats.references += 1

    # --- plate furniture, in its own cell so occupancy audits can tell
    # content from furniture ---
    furniture_cell = gdstk.Cell("FURNITURE")
    furniture: list[np.ndarray] = []
    for key in ("fiducial_sw", "fiducial_se", "fiducial_nw", "fiducial_ne"):
        r = plan.reserved[key]
        furniture.extend(
            fiducial_cross((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2, profiles.layout.fiducial_size_um)
        )
    furniture.extend(
        centered_orientation_glyph(
            plan.reserved["orientation_glyph"].as_tuple(), profiles.layout.orientation_glyph_um
        )
    )
    for ring in furniture:
        furniture_cell.add(
            gdstk.Polygon(ring, layer=profiles.fab.layer, datatype=profiles.fab.datatype)
        )

    base_title = profiles.layout.title_text or manifest.output.plate_name
    title = base_title
    if plan.plate_count > 1:
        title = f"{base_title} {plan.plate_index + 1}/{plan.plate_count}"
    if profiles.layout.title_band_um > 0 and title:
        band = plan.reserved["title_band"]
        text_polys, _ = stroke_text(
            title,
            profiles.layout.title_height_um,
            (band.x0 + profiles.layout.fiducial_size_um * 1.5,
             band.y0 + (band.y1 - band.y0 - profiles.layout.title_height_um) / 2),
        )
        for p in shapely_to_gdstk(text_polys, layer=profiles.fab.layer,
                                  datatype=profiles.fab.datatype):
            furniture_cell.add(p)

    # navigation band (M4): page map + scale bar, all inside the reserved band
    if "nav_band" in plan.reserved and plan.placements:
        from stele.layout.navigation import page_map_lines, scale_bar, text_block

        band = plan.reserved["nav_band"]
        sources = sorted({os.path.basename(pl.job.pdf_path) for pl in plan.placements})
        # SOURCE page ordinals, not placement ordinals: a tiered plate repeats
        # every page once per tier (review finding 7)
        ordinals = [pl.job.ordinal for pl in plan.placements]
        lines = page_map_lines(
            base_title, plan.plate_index, plan.plate_count,
            min(ordinals), max(ordinals), sources,
            tiers=[pl.tier_scale for pl in plan.placements],
        )
        char_h = min(600.0, (band.y1 - band.y0) / (len(lines) * 1.8))
        for p in shapely_to_gdstk(
            text_block(lines, (band.x0 + 200.0, band.y0 + char_h * 0.6), char_h),
            layer=profiles.fab.layer, datatype=profiles.fab.datatype,
        ):
            furniture_cell.add(p)
        for ring in scale_bar(band.x1 - 1400.0, band.y0 + 100.0, 1000.0, 100.0):
            furniture_cell.add(
                gdstk.Polygon(ring, layer=profiles.fab.layer, datatype=profiles.fab.datatype)
            )
    content.add(gdstk.Reference(furniture_cell.name, (0.0, 0.0)))

    # --- top cell, mirroring applied as a whole-plate transform ---
    top = gdstk.Cell("PLATE")
    if orientation.mirrored:
        top.add(gdstk.Reference(content.name, (plan.plate_w_um, 0.0), rotation=math.pi,
                                x_reflection=True))
    else:
        top.add(gdstk.Reference(content.name, (0.0, 0.0)))

    _progress(progress, "writing", 0, 1, f"Finalizing plate {plate_number} manufacturing file")
    # Page and halftone cells were emitted as soon as they were complete. Only
    # the small furniture/top-level reference cells remain in memory here.
    writer.write(furniture_cell)
    writer.write(content)
    writer.write(top)
    writer.close()
    os.replace(partial_gds_path, gds_path)
    gds_size_mb = os.path.getsize(gds_path) / 1e6
    _progress(progress, "writing", 1, 1, f"Wrote {gds_size_mb:.1f} MB GDSII file")

    plate_report: dict = {
        "halftone": (
            {
                "pitch_um": ht_pitch,
                "mask": profiles.content.dither_mask,
                "mask_asset_sha256": __import__(
                    "stele.passes.halftone", fromlist=["mask_asset_sha256"]
                ).mask_asset_sha256(profiles.content.dither_mask),
                "tile_cells": len(tile_cells),
                "exact_tile_cap": profiles.content.max_exact_tiles_per_plate,
                "exact_tiles": profiles.content.max_exact_tiles_per_plate
                - max(0, ht_exact_budget[0]),
                "exact_cap_exhausted": ht_exact_budget[0] <= 0,
                "pages": halftone_stats,
            }
            if dithering
            else None
        ),
        "ingest": [pl.job.record() for pl in plan.placements],
        "placements": [
            {
                "cell": cell_names[pl.page_key],
                "page_key": pl.page_key,
                "slot_um": pl.slot.as_tuple(),
                "origin_um": pl.origin,
                "scale": pl.scale,
                "tier_scale": pl.tier_scale,
            }
            for pl in plan.placements
        ],
        "plan": plan.summary(),
        "gds": {
            "path": gds_path,
            "sha256": sha256_file(gds_path),
            "size_mb": round(gds_size_mb, 2),
            "cells": stats.cells,
            "polygons": stats.polygons,
            "vertices": stats.vertices,
            "references": stats.references,
        },
        "timings_s": {"build": round(time.time() - t0, 1)},
    }

    # The independent verifier reads the just-written GDS into KLayout. Drop
    # the remaining writer-side cells first; page cells have already been
    # streamed and released.
    del top, content, furniture_cell, cell_by_key, tile_cells, writer
    gc.collect()

    if verify:
        t1 = time.time()
        plate_report["verify"] = verify_plate(
            manifest, profiles, plan, orientation, cell_names,
            heatmap_dir=gds_path + ".defects", gds_path=gds_path,
            progress=progress,
        )
        plate_report["timings_s"]["verify"] = round(time.time() - t1, 1)

    if preview:
        t2 = time.time()
        _progress(progress, "previewing", 0, 1, f"Drawing plate {plate_number} preview")
        layout = open_layout(gds_path)
        arr = render_plate(
            layout, (0.0, 0.0, plan.plate_w_um, plan.plate_h_um), 0.02,
            profiles.fab.layer, profiles.fab.datatype, plate_w_um=plan.plate_w_um,
        )
        preview_path = (
            manifest.output.preview_path() if plan.plate_count == 1
            else gds_path + ".preview.png"
        )
        save_png(255 - arr, preview_path)
        plate_report["preview"] = preview_path
        plate_report["timings_s"]["preview"] = round(time.time() - t2, 1)
        _progress(progress, "previewing", 1, 1, "Preview ready")
    return plate_report


# fab-profile fields that are RECORDED but not yet enforced by any gate;
# listed in every validation block so the report never implies otherwise
UNENFORCED_FAB_FIELDS = [
    "allowed GDS elements (only boundaries+SREF are ever emitted)",
    "hierarchy/AREF limits (depth fixed at 2, no AREF until M3)",
    "coordinate limits (guaranteed by construction: 1nm DBU on <=1m plates)",
    "inspection policy (vendor conversation, not software-checkable)",
    "required marks beyond fiducials/plate-ID/orientation glyph",
    "mask exposure tone (positive-tone assumed; see orientation.fab_tone_assumption)",
]


def verify_plate(
    manifest: JobManifest,
    profiles: Profiles,
    plan: PlatePlan,
    orientation: Orientation,
    cell_names: dict[str, str],
    heatmap_dir: str | None = None,
    gds_path: str | None = None,
    progress: ProgressCallback | None = None,
) -> dict:
    """Verification: per-page band-tolerance XOR vs the independent reference
    engine, per-page klayout DRC, measured stroke gates, placement audit,
    occupancy audit, chirality, budgets, density. Produces a three-state
    status: pass | pass_with_warnings | fail — geometry problems under a
    warn-severity fab profile can NEVER surface as an unqualified pass."""
    out: dict = {"pages": {}, "gates": {}}
    gds_path = gds_path or manifest.output.gds
    layout = open_layout(gds_path)
    render_cache: dict = {}  # unique-cell bitmaps shared across pages

    seen: set[str] = set()
    worst_defect_mismatch = 0.0
    total_defects = 0
    total_component_failures = 0
    any_shape_mismatch = False
    stroke_floor_failures: list[str] = []
    total_width_violations = 0
    total_space_violations = 0
    tone_failures: list[str] = []
    tone_unassessed = 0  # image regions no tone superpixel ever gated
    readability: dict[str, dict] = {}  # per-tier recorded legibility verdicts
    heatmaps: list[str] = []
    dithering = profiles.content.image_mode == "dither"
    triad = profiles.content.color_mode == "rgb_triad"

    unique_pages = len({pl.page_key for pl in plan.placements})
    checked = 0
    _progress(progress, "verifying", 0, max(1, unique_pages), "Starting independent checks")
    for pl in plan.placements:
        if pl.page_key in seen:
            continue
        _progress(
            progress,
            "verifying",
            checked,
            max(1, unique_pages),
            f"Checking page {pl.job.ordinal + 1}",
        )
        seen.add(pl.page_key)
        cell = cell_names[pl.page_key]
        w_um = pl.job.frame_pt[0] * 25400.0 / 72.0 * pl.scale
        h_um = pl.job.frame_pt[1] * 25400.0 / 72.0 * pl.scale
        w_px = int(round(w_um * VERIFY_PX_PER_UM))
        cand = rasterize_cell_hierarchical(
            layout, cell, (0.0, 0.0, w_um, h_um), VERIFY_PX_PER_UM,
            profiles.fab.layer, profiles.fab.datatype, _cache=render_cache,
        )
        image_rects = image_regions_pt(pl.job.pdf_path, pl.job.page_index)
        # the reference applies the BUILD's declared threshold policy (an otsu
        # build vs a fixed-threshold reference compares different policies)
        strict, mid, loose, thr_info = reference_masks(
            pl.job.pdf_path,
            pl.job.page_index,
            w_px,
            profiles.content.fixed_threshold,
            binarize_dpi=profiles.content.dpi,
            image_rects_pt=image_rects,
            threshold_mode=profiles.content.threshold,
            threshold_exclude_rects=dithering,
        )
        tone_result = None
        if dithering and image_rects:
            # dither geometry is not band-XOR-comparable to a thresholded
            # render: exclude image rects from the defect path, judge them by
            # tone instead (metric 4)
            from stele.verify.reference import render_reference_gray
            from stele.verify.tone import image_tone_metrics

            ppp = w_px / pl.job.frame_pt[0]  # px per point
            rects_px = [
                (int(y0 * ppp), int(x0 * ppp), int(np.ceil(y1 * ppp)), int(np.ceil(x1 * ppp)))
                for x0, y0, x1, y1 in image_rects
            ]
            cand_full = cand.copy()
            for r0, c0, r1, c1 in rects_px:
                for arr in (cand, strict, mid, loose):
                    arr[max(0, r0) : r1, max(0, c0) : c1] = 0
            ht_pitch_v = profiles.content.dither_pitch_um or max(
                profiles.fab.min_feature_um, profiles.fab.min_space_um
            )
            # 32 dither sites per superpixel: blue-noise sampling noise of an
            # 8-site block is +/-6% — the max over 40k blocks reads as a 22%
            # "defect" on a clean build (measured); 32 sites -> ~1.6% sigma
            superpixel = max(8, int(round(32 * ht_pitch_v * VERIFY_PX_PER_UM)))
            import cv2 as _cv2

            mu_dpi = int(round(w_px / pl.job.frame_pt[0] * 72))
            if profiles.content.color_mode == "rgb_triad":
                from stele.frontends.raster import render_page_rgb as _mupdf_rgb
                from stele.passes.colorsep import ChannelTransform, triad_boxes
                from stele.verify.reference import render_reference_rgb

                rgb_pd = render_reference_rgb(pl.job.pdf_path, pl.job.page_index, w_px)
                rgb_mu = _mupdf_rgb(pl.job.pdf_path, pl.job.page_index, dpi=mu_dpi)
                rgb_mu = _cv2.resize(rgb_mu, (rgb_pd.shape[1], rgb_pd.shape[0]),
                                     interpolation=_cv2.INTER_AREA)
                xf = {
                    ch: ChannelTransform(**cfg)
                    for ch, cfg in profiles.content.channel_transforms.items()
                }
                pt2um = 25400.0 / 72.0
                px_per_doc_um = w_px / (pl.job.frame_pt[0] * pt2um)
                page_h_um = pl.job.frame_pt[1] * pt2um
                channels: dict[str, dict] = {}
                skipped_triad = 0
                for x0, y0, x1, y1 in image_rects:  # pt, y-down
                    bbox_doc = (x0 * pt2um, (pl.job.frame_pt[1] - y1) * pt2um,
                                x1 * pt2um, (pl.job.frame_pt[1] - y0) * pt2um)
                    # full image rect in comparison px (the SOURCE content)
                    fr0 = max(0, int(y0 * ppp))
                    fr1 = min(cand_full.shape[0], int(np.ceil(y1 * ppp)))
                    fc0 = max(0, int(x0 * ppp))
                    fc1 = min(cand_full.shape[1], int(np.ceil(x1 * ppp)))
                    for ch_name, _t, bb in triad_boxes(bbox_doc, xf):
                        # doc um (y-up) -> comparison px (y-down)
                        r0 = int((page_h_um - bb[3]) * px_per_doc_um)
                        r1 = int(np.ceil((page_h_um - bb[1]) * px_per_doc_um))
                        c0 = int(bb[0] * px_per_doc_um)
                        c1 = int(np.ceil(bb[2] * px_per_doc_um))
                        ci = "RGB".index(ch_name)
                        # the candidate squeezes the FULL image into this
                        # sub-rect; the page render at the sub-rect shows only
                        # a third of the source (measured: 59% max "error") —
                        # so the tone target is the source rect RESAMPLED into
                        # the sub-rect for both engines
                        def synth(full: np.ndarray) -> np.ndarray:
                            out_arr = full.copy()
                            out_arr[max(0, r0) : r1, max(0, c0) : c1] = _cv2.resize(
                                full[fr0:fr1, fc0:fc1],
                                (min(c1, full.shape[1]) - max(0, c0),
                                 min(r1, full.shape[0]) - max(0, r0)),
                                interpolation=_cv2.INTER_AREA,
                            )
                            return out_arr

                        m = image_tone_metrics(
                            cand_full, synth(rgb_pd[..., ci]), [(r0, c0, r1, c1)],
                            superpixel,
                            ref_gray_build_engine=synth(rgb_mu[..., ci]),
                        )
                        skipped_triad += m.get("regions_skipped", 0)
                        prev = channels.get(ch_name)
                        if prev is None or m["max_err"] > prev["max_err"]:
                            channels[ch_name] = m
                tone_result = {
                    "channels": channels,
                    "mean_err": round(max(m["mean_err"] for m in channels.values()), 4),
                    "max_err": round(max(m["max_err"] for m in channels.values()), 4),
                    "superpixels": sum(m["superpixels"] for m in channels.values()),
                    "regions_skipped": skipped_triad,
                    "assessed": any(m.get("assessed") for m in channels.values()),
                    "pass": all(m["pass"] for m in channels.values()),
                }
            else:
                from stele.frontends.raster import render_page_gray as _mupdf_gray

                ref_gray = render_reference_gray(pl.job.pdf_path, pl.job.page_index, w_px)
                g_mu = _mupdf_gray(pl.job.pdf_path, pl.job.page_index, dpi=mu_dpi)
                g_mu = _cv2.resize(g_mu, (ref_gray.shape[1], ref_gray.shape[0]),
                                   interpolation=_cv2.INTER_AREA)
                tone_result = image_tone_metrics(
                    cand_full, ref_gray, rects_px, superpixel, ref_gray_build_engine=g_mu
                )
        # band tolerances scale with the effective build pixel at the
        # comparison grid: a 4x tier page's build pixel is ~2 comparison px,
        # and reference/candidate edges quantize to it (measured: 2,693 false
        # sliver defects at a fixed 1-px band)
        build_px_comp = (25400.0 / profiles.content.dpi) * pl.scale * VERIFY_PX_PER_UM
        tol_missing = max(1, int(np.ceil(build_px_comp)))
        res = hysteresis_compare(
            cand, strict, mid, loose,
            tol_px=tol_missing, tol_extra_px=tol_missing + 1,
            keep_map=heatmap_dir is not None,
        )
        page_result = res.to_dict()
        page_result["reference_threshold"] = thr_info
        if tone_result is not None:
            page_result["image_tone"] = tone_result
            tone_unassessed += tone_result.get("regions_skipped", 0)
        elif image_rects:
            page_result["image_regions_wide_hysteresis"] = len(image_rects)

        # measured-geometry gate: stroke widths on the FINAL plate-scale
        # geometry at a fine grid, vs writer min feature + process bias
        fine_um_per_px = 0.25
        fine = rasterize_cell_hierarchical(
            layout, cell, (0.0, 0.0, w_um, h_um), 1.0 / fine_um_per_px,
            profiles.fab.layer, profiles.fab.datatype, _cache=render_cache,
        )
        sw = stroke_width_stats(fine, fine_um_per_px)

        # per-tier readability: the SIMULATED reader verdict, recorded and
        # gated (review finding 2 — the sim was a demo, not a gate). Sampled
        # once per tier from the densest text window; image rects are
        # excluded (dither screens are tone, not strokes to read).
        tier_key = f"x{pl.tier_scale:g}"
        if not readability.get(tier_key, {}).get("assessed"):
            from stele.verify.readability import contrast_gate, densest_ink_window

            win = (
                int(READABILITY_WINDOW_UM[0] / fine_um_per_px),
                int(READABILITY_WINDOW_UM[1] / fine_um_per_px),
            )
            ppf = fine.shape[1] / pl.job.frame_pt[0]  # fine px per point
            rects_fine = [
                (int(y0 * ppf), int(x0 * ppf), int(np.ceil(y1 * ppf)), int(np.ceil(x1 * ppf)))
                for x0, y0, x1, y1 in image_rects
            ]
            loc = densest_ink_window(fine, win, rects_fine)
            if loc is None:
                readability.setdefault(tier_key, {"assessed": False, "pass": True})
            else:
                r0, c0 = loc
                window = fine[r0 : r0 + win[0], c0 : c0 + win[1]]
                rres = contrast_gate(window, fine_um_per_px, profiles.reader)
                rres["cell"] = cell
                rres["window_um"] = [round(c0 * fine_um_per_px, 1),
                                     round(r0 * fine_um_per_px, 1),
                                     READABILITY_WINDOW_UM[1], READABILITY_WINDOW_UM[0]]
                if triad:
                    from stele.passes.colorsep import CHANNEL_WAVELENGTHS_NM

                    rres["channels"] = {
                        ch: contrast_gate(window, fine_um_per_px, profiles.reader, wl)
                        for ch, wl in CHANNEL_WAVELENGTHS_NM.items()
                    }
                readability[tier_key] = rres
        del fine
        page_result["stroke_widths_um"] = {
            k: round(v, 3) for k, v in sw.items() if isinstance(v, float)
        }
        floor = profiles.fab.min_feature_um + profiles.fab.process_bias_um
        stroke_ok = bool(sw.get("p05_um", 0.0) >= floor)
        page_result["stroke_p05_above_writer_floor"] = stroke_ok
        if not stroke_ok:
            stroke_floor_failures.append(cell)

        # true local width/space DRC on klayout's engine. Dithered pages:
        # direct shapes (text+patches) fully + text<->image boundary bands;
        # dither interiors are DRC-clean by construction (full-cell sites at
        # pitch >= fab floor; measured 0/0 on assembled pages at 1 & 2 um —
        # the full merged check costs 647 s/page and re-proves a theorem)
        page_cell_obj = layout.cell(cell)
        has_dither = any(
            layout.cell(i.cell_index).name.startswith("HT_")
            for i in page_cell_obj.each_inst()
        )
        if has_dither:
            drc = klayout_width_space(
                layout, cell, profiles.fab.layer, profiles.fab.datatype,
                profiles.fab.min_feature_um, profiles.fab.min_space_um, direct_only=True,
            )
            pt2um = 25400.0 / 72.0 * pl.scale
            frame_h_pt = pl.job.frame_pt[1]
            bands = []
            for x0, y0, x1, y1 in image_rects:  # pt, y-down -> page um, y-up
                bx0, bx1 = x0 * pt2um, x1 * pt2um
                by0, by1 = (frame_h_pt - y1) * pt2um, (frame_h_pt - y0) * pt2um
                s = 5.0
                bands += [
                    (bx0 - s, by0 - s, bx1 + s, by0 + s),
                    (bx0 - s, by1 - s, bx1 + s, by1 + s),
                    (bx0 - s, by0 - s, bx0 + s, by1 + s),
                    (bx1 - s, by0 - s, bx1 + s, by1 + s),
                ]
            banded = klayout_width_space_banded(
                layout, cell, profiles.fab.layer, profiles.fab.datatype,
                profiles.fab.min_feature_um, profiles.fab.min_space_um, bands,
            )
            drc["width_violations"] += banded["width_violations"]
            drc["space_violations"] += banded["space_violations"]
            drc["mode"] = "direct+boundary-bands (dither interior clean by construction)"
        else:
            drc = klayout_width_space(
                layout, cell, profiles.fab.layer, profiles.fab.datatype,
                profiles.fab.min_feature_um, profiles.fab.min_space_um,
            )
        page_result["drc"] = drc
        total_width_violations += drc["width_violations"]
        total_space_violations += drc["space_violations"]

        if heatmap_dir and res.defect_map is not None and res.page_fail:
            os.makedirs(heatmap_dir, exist_ok=True)
            path = os.path.join(heatmap_dir, f"{cell}.png")
            _save_heatmap(path, cand, mid, res.defect_map)
            page_result["heatmap"] = path
            heatmaps.append(path)

        out["pages"][cell] = page_result
        worst_defect_mismatch = max(worst_defect_mismatch, res.defect_mismatch)
        total_defects += res.structural_defects
        total_component_failures += res.component_failures
        any_shape_mismatch = any_shape_mismatch or res.shape_mismatch
        if tone_result is not None and not tone_result["pass"]:
            tone_failures.append(cell)
        checked += 1
        _progress(
            progress,
            "verifying",
            checked,
            max(1, unique_pages),
            f"Checked {checked} of {unique_pages} page(s)",
        )

    # unique dither tile cells: checked ONCE per plate, packed into a single
    # spaced scratch region (500 separate Region inits cost ~tens of seconds)
    _progress(progress, "verifying", checked, max(1, unique_pages), "Checking the whole plate")
    import klayout.db as kdb

    tile_drc = {"cells": 0, "width_violations": 0, "space_violations": 0}
    li = layout.layer(profiles.fab.layer, profiles.fab.datatype)
    scratch = kdb.Region()
    dbu = layout.dbu
    col = 0
    for ci in layout.each_cell():
        if not ci.name.startswith("HT_"):
            continue
        b = ci.bbox(li)
        step = int(50.0 / dbu)  # 50 um spacing: no cross-tile interaction
        off_x = col * step - b.left
        for shape in ci.each_shape(li):
            p = shape.polygon
            if p is not None:
                scratch.insert(p.moved(off_x, 0))
        tile_drc["cells"] += 1
        col += 1
    if tile_drc["cells"]:
        scratch.merge()
        min_w = int(round(profiles.fab.min_feature_um / dbu))
        min_s = int(round(profiles.fab.min_space_um / dbu))
        tile_drc["width_violations"] = scratch.width_check(min_w).count()
        tile_drc["space_violations"] = scratch.space_check(min_s).count()
    total_width_violations += tile_drc["width_violations"]
    total_space_violations += tile_drc["space_violations"]
    out["drc_tile_cells"] = tile_drc

    # placement audit via klayout instance enumeration
    content_cell = layout.cell("CONTENT")
    n_refs = sum(
        1 for inst in content_cell.each_inst()
        if layout.cell(inst.cell_index).name.startswith("PAGE_")
    )

    # chirality machine-check: glyph region rendered from the GDS, top
    # transform read from the file (never inferred from the declaration)
    chirality = check_chirality(
        layout, plan, profiles.layout.orientation_glyph_um, orientation.mirrored,
        profiles.fab.layer, profiles.fab.datatype,
    )

    occupancy = occupancy_audit(
        gds_path, plan, profiles.fab.layer, profiles.fab.datatype, cell_names=cell_names
    )
    budget = budget_check(
        profiles.fab,
        round(os.path.getsize(gds_path) / 1e6, 2),
        n_cells=layout.cells(),
        max_vertices_seen=max_vertices_per_cell(
            layout, profiles.fab.layer, profiles.fab.datatype
        ),
    )
    density = density_check(
        render_plate(
            layout, (0, 0, plan.plate_w_um, plan.plate_h_um), 0.02,
            profiles.fab.layer, profiles.fab.datatype, plate_w_um=plan.plate_w_um,
            cache=render_cache,
        ),
        profiles.fab,
    )

    # tiers whose placements never yielded an assessable text window: recorded
    # explicitly so "no verdict" is never mistaken for a passing verdict
    for t in sorted({pl.tier_scale for pl in plan.placements}, reverse=True):
        readability.setdefault(f"x{t:g}", {"assessed": False, "pass": True})
    readability_failing = sorted(
        k for k, r in readability.items()
        if (r.get("assessed") and not r["pass"])
        or any(
            c.get("assessed") and not c["pass"] for c in r.get("channels", {}).values()
        )
    )

    out["chirality"] = chirality
    out["readability"] = readability
    out["drc_plate"] = {"occupancy": occupancy, "budget": budget, "density": density}
    out["heatmaps"] = heatmaps

    # occupancy is OUR layout contract (reserved-region intrusion = build bug),
    # so it binds unconditionally; width/space/stroke/density/budgets are the
    # VENDOR's manufacturability contract, gated per fab drc_gate severity.
    # readability is the READER's contract and unassessed tone is unverified
    # content: both block an unqualified pass (warnings), and neither can
    # falsify content correctness on their own.
    content_pass = bool(
        worst_defect_mismatch <= DEFECT_MISMATCH_GATE
        and total_defects == 0
        and total_component_failures == 0
        and not any_shape_mismatch
        and n_refs == len(plan.placements)
        and chirality["pass"]
        and occupancy["pass"]
        and not tone_failures
    )
    geometry_pass = bool(
        not stroke_floor_failures
        and total_width_violations == 0
        and total_space_violations == 0
        and budget["file_size_ok"]
        and budget["cells_ok"]
        and budget["vertices_ok"]
        and density["pass"]
    )
    warned = bool(readability_failing) or tone_unassessed > 0
    if not content_pass or (not geometry_pass and profiles.fab.drc_gate == "fail"):
        status = "fail"
    elif not geometry_pass or warned:
        status = "pass_with_warnings"
    else:
        status = "pass"

    out["gates"] = {
        "worst_defect_mismatch": round(worst_defect_mismatch, 6),
        "structural_defects": total_defects,
        "component_failures": total_component_failures,
        "shape_mismatch": any_shape_mismatch,
        "placements_expected": len(plan.placements),
        "placements_found": n_refs,
        "chirality": chirality["pass"],
        "stroke_floor_failing_pages": stroke_floor_failures,
        "image_tone_failing_pages": tone_failures,
        "image_tone_unassessed_regions": tone_unassessed,
        "readability_failing_tiers": readability_failing,
        "width_violations": total_width_violations,
        "space_violations": total_space_violations,
        "content_pass": content_pass,
        "geometry_pass": geometry_pass,
        "drc_severity": profiles.fab.drc_gate,
        "status": status,
        "all_pass": status == "pass",
    }
    return out


def _save_heatmap(path: str, cand: np.ndarray, ref: np.ndarray, defect_map: np.ndarray) -> None:
    """Defects in red over the reference (gray) and candidate (light blue)."""
    from PIL import Image

    h = min(cand.shape[0], ref.shape[0], defect_map.shape[0])
    w = min(cand.shape[1], ref.shape[1], defect_map.shape[1])
    rgb = np.full((h, w, 3), 255, dtype=np.uint8)
    ref_ink = ref[:h, :w] > 0
    cand_ink = cand[:h, :w] > 0
    rgb[ref_ink] = (190, 190, 190)
    rgb[cand_ink & ~ref_ink] = (170, 200, 255)
    rgb[defect_map[:h, :w] > 0] = (220, 0, 0)
    Image.fromarray(rgb).save(path)


def _dep_versions() -> dict:
    deps = {}
    for name in ("gdstk", "pymupdf", "pypdfium2", "opencv-python-headless", "shapely", "klayout",
                 "numpy", "pillow", "pydantic"):
        try:
            deps[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            deps[name] = "unknown"
    return deps
