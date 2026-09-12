"""Pre-build planning for the local UI: capacity, layout sketch, optics.

Everything here is cheap (no rendering, no verification) and re-run on every
settings change, so the page can show the plate before any expensive work.
The numbers come from the same planner and validator the build uses; the
optics lines are the closed-form HEURISTICS from config.validate and are
labeled as such — the measured verdict still comes from the build.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from stele.config.manifest import InputSpec
from stele.config.profiles import Profiles
from stele.config.validate import STROKE_UM_PER_PT, validate_profiles
from stele.ingest.normalize import PageJob
from stele.ir.model import LETTER_H_UM, LETTER_W_UM
from stele.layout.engine import PlatePlan, band_text_area, plan_plates
from stele.layout.fiducials import measure_text_width
from stele.layout.navigation import (
    TEXT_LEADING,
    block_height,
    fit_char_height,
    page_map_lines,
)
from stele.ui.presets import (
    MIN_GUTTER_UM,
    PLATE_SIZES,
    normalized_settings,
    profiles_from_settings,
)
from stele.verify.readability import EYE_RESOLUTION_UM_AT_250MM

if TYPE_CHECKING:  # runs.py imports plan_from_report; annotations only here
    from stele.ui.runs import UploadStore

SKETCH_PLACEMENT_CAP = 800  # slot rectangles drawn in the page's plate sketch


def selected_page_jobs(store: UploadStore, selections: list[dict[str, Any]]) -> tuple[list[PageJob], int]:
    """PageJobs for the documents/pages the page has selected, plus the count
    of source pages. Raises ValueError with UI wording on bad selections."""
    if not selections:
        raise ValueError("Choose at least one PDF")
    jobs: list[PageJob] = []
    source_pages = 0
    ordinal = 0
    for item in selections:
        document = store.get(str(item.get("id", "")))
        pages = str(item.get("pages", "all")).strip()
        if not pages:
            raise ValueError(
                f"{document.name}: check All pages or enter page numbers such as 1-5, 8"
            )
        indices = InputSpec(path=str(document.path), pages=pages).page_indices(
            document.page_count
        )
        if not indices:
            raise ValueError(f"{document.name}: the page selection is empty")
        source_pages += len(indices)
        for index in indices:
            page = document.pages[index]
            jobs.append(
                PageJob(
                    pdf_path=str(document.path),
                    pdf_sha256=document.sha256,
                    page_index=index,
                    ordinal=ordinal,
                    mediabox_pt=tuple(page["mediabox_pt"]),
                    cropbox_pt=tuple(page["cropbox_pt"]),
                    rotation_deg=int(page["rotation_deg"]),
                    frame_pt=(float(page["width_pt"]), float(page["height_pt"])),
                    fonts_not_embedded=tuple(page.get("fonts_not_embedded", ())),
                )
            )
            ordinal += 1
    return jobs, source_pages


def title_preview(profiles: Profiles, plan: PlatePlan, plate_name: str, plate_count: int) -> dict | None:
    """Where the title will land on the first plate, using the same fit rule as
    the build (shrink to the free span between the corner fiducials)."""
    layout = profiles.layout
    if layout.title_band_um <= 0:
        return None
    base = layout.title_text or plate_name
    text = f"{base} {plate_count}/{plate_count}" if plate_count > 1 else base
    area = band_text_area(plan, "title_band")
    if area is None or not text:
        return None
    span_w = area.x1 - area.x0
    height = min(layout.title_height_um, area.y1 - area.y0)
    width = measure_text_width(text, height)
    shrunk = False
    if width > span_w and width > 0:
        height *= span_w / width
        width = span_w
        shrunk = True
    if layout.title_align == "left":
        x0 = area.x0
    elif layout.title_align == "right":
        x0 = area.x1 - width
    else:
        x0 = area.x0 + (span_w - width) / 2
    y0 = area.y0 + ((area.y1 - area.y0) - height) / 2
    return {
        "text": text,
        "align": layout.title_align,
        "requested_height_um": layout.title_height_um,
        "height_um": round(height, 1),
        "width_um": round(width, 1),
        "rect_um": [round(x0, 1), round(y0, 1), round(x0 + width, 1), round(y0 + height, 1)],
        "shrunk_to_fit": shrunk,
    }


def nav_preview(profiles: Profiles, plan: PlatePlan, plate_name: str, n_pages: int, sources: list[str]) -> dict | None:
    """Where the guide-band text block will land (first plate)."""
    layout = profiles.layout
    if layout.nav_band_um <= 0:
        return None
    area = band_text_area(plan, "nav_band")
    if area is None:
        return None
    lines = page_map_lines(
        layout.title_text or plate_name, 0, plan.plate_count, 0, max(0, n_pages - 1),
        sources, tiers=list(layout.tier_scales),
    )
    lines += [ln.strip() for ln in layout.nav_text.splitlines() if ln.strip()]
    band_h = area.y1 - area.y0
    pad = min(200.0, band_h * 0.08)
    bar_label_h = max(60.0, min(300.0, band_h * 0.12))
    bar_zone = 1000.0 + 150.0 + measure_text_width("1 MM", bar_label_h) + 2 * pad
    with_bar = (area.x1 - area.x0) - bar_zone >= 20 * pad
    text_x1 = area.x1 - bar_zone if with_bar else area.x1
    text_area = (area.x0 + pad, area.y0 + pad, text_x1 - pad, area.y1 - pad)
    requested = layout.nav_text_height_um
    char_h = fit_char_height(lines, text_area, requested or 600.0)
    bh = block_height(len(lines), char_h, TEXT_LEADING)
    base_y = text_area[1] + ((text_area[3] - text_area[1]) - bh) / 2
    return {
        "lines": lines,
        "char_height_um": round(char_h, 1),
        "requested_char_height_um": requested,
        "shrunk_to_fit": bool(requested and char_h < requested - 1e-6),
        "align": layout.nav_align,
        "block_um": [round(text_area[0], 1), round(base_y, 1), round(text_area[2], 1),
                     round(base_y + bh, 1)],
        "scale_bar": with_bar,
    }


def optics_summary(profiles: Profiles, reduction: float | None) -> dict[str, Any]:
    """Closed-form optics lines for the page (heuristics, labeled as such)."""
    reader = profiles.reader
    fab = profiles.fab
    red = reduction or 1.0 / min(
        profiles.layout.pseudopage_width_um / LETTER_W_UM,
        profiles.layout.pseudopage_height_um / LETTER_H_UM,
    )
    rayleigh = reader.rayleigh_resolution_um
    eye_um = EYE_RESOLUTION_UM_AT_250MM / reader.magnification
    writer_floor = fab.min_feature_um + fab.process_bias_um
    return {
        "reduction": round(red, 1),
        "rayleigh_um": round(rayleigh, 3),
        "eye_limited_um": round(eye_um, 3),
        "eye_limits_before_optics": eye_um > rayleigh,
        "magnification_for_rayleigh": round(EYE_RESOLUTION_UM_AT_250MM / rayleigh, 0),
        # heuristic point sizes: stroke ~ 25 um per point on paper (validate.py)
        "reader_floor_pt": round(rayleigh * red / STROKE_UM_PER_PT, 1),
        "writer_floor_pt": round(writer_floor * red / STROKE_UM_PER_PT, 1),
        "writer_floor_um": writer_floor,
        "polarity": profiles.layout.polarity,
        "heuristic": (
            "Point sizes assume ~25 µm of stroke per point (serif body text); fonts vary "
            "±40%. The measured verdict comes from the build's readability simulation."
        ),
    }


def plan_payload(store: UploadStore, body: dict[str, Any]) -> dict[str, Any]:
    jobs, source_pages = selected_page_jobs(store, body.get("documents") or [])
    settings = normalized_settings(body.get("settings"))
    profiles = profiles_from_settings(settings)
    validation = validate_profiles(profiles)
    if not validation.ok:
        raise ValueError("; ".join(validation.errors))
    plans = plan_plates(
        profiles.fab, profiles.layout, jobs, fit_mode=profiles.content.fit_mode
    )
    first = plans[0]
    for band in ("title_band", "nav_band"):
        if band in first.reserved and band_text_area(first, band) is None:
            raise ValueError(
                f"The {band.replace('_band', '')} band leaves no room for text between the "
                f"corner marks; enlarge the plate or turn the band off"
            )
    plate_name = str(body.get("plate_name") or "").strip() or "STELE-0001"
    first_placement = first.placements[0] if first.placements else None
    page_size = None
    reduction = None
    if first_placement is not None:
        rect = first_placement.content_rect()
        page_size = [
            round((rect.x1 - rect.x0) / 1000, 2),
            round((rect.y1 - rect.y0) / 1000, 2),
        ]
        reduction = round(1.0 / first_placement.scale, 1)
    layout = profiles.layout
    fab = profiles.fab
    active_w = fab.plate_width_mm - 2 * fab.edge_exclusion_mm
    active_h = fab.plate_height_mm - 2 * fab.edge_exclusion_mm
    cols = int(active_w * 1000 // layout.pitch_x_um)
    rows = int(active_h * 1000 // layout.pitch_y_um)
    sources = sorted({doc.name for doc in (store.get(str(i.get("id", ""))) for i in body.get("documents") or [])})
    fonts: dict[str, list[str]] = {}
    for doc in (store.get(str(i.get("id", ""))) for i in body.get("documents") or []):
        if doc.fonts_not_embedded:
            fonts[doc.name] = list(doc.fonts_not_embedded)
    title = title_preview(profiles, first, plate_name, len(plans))
    nav = nav_preview(profiles, first, plate_name, source_pages, sources)
    warnings = list(validation.warnings)
    if title and title["shrunk_to_fit"]:
        warnings.append(
            f"The title is too wide for the band between the corner marks at "
            f"{title['requested_height_um'] / 1000:.2f} mm and will be etched at "
            f"{title['height_um'] / 1000:.2f} mm instead. Shorten it or lower the title height."
        )
    if nav and nav["shrunk_to_fit"]:
        warnings.append(
            f"The guide-band text does not fit at {nav['requested_char_height_um']:.0f} µm and "
            f"will be etched at {nav['char_height_um']:.0f} µm."
        )
    if fonts:
        names = sorted({name for values in fonts.values() for name in values})
        warnings.append(
            f"{len(fonts)} document(s) use fonts that are not embedded ({', '.join(names[:4])}"
            f"{', …' if len(names) > 4 else ''}). Different PDF engines substitute different "
            f"fonts for these, so the independent check may report glyph differences that are "
            f"not lost content. Embedding fonts before upload gives a clean verification."
        )
    return {
        "source_pages": source_pages,
        "placements": sum(len(plan.placements) for plan in plans),
        "plates": len(plans),
        "usable_slots_first_plate": first.capacity(),
        "theoretical_slots_first_plate": first.theoretical_slots,
        "utilization": round(len(first.placements) / max(1, first.capacity()), 4),
        "reduction": reduction,
        "pseudopage_mm": page_size,
        "plate": {
            "size_id": settings["plate_size"],
            "width_mm": fab.plate_width_mm,
            "height_mm": fab.plate_height_mm,
            "edge_exclusion_mm": fab.edge_exclusion_mm,
            "active_mm": [round(active_w, 2), round(active_h, 2)],
            "rows": rows,
            "cols": cols,
            "pitch_um": [layout.pitch_x_um, layout.pitch_y_um],
            "gutter_um": [
                round(layout.pitch_x_um - layout.pseudopage_width_um, 3),
                round(layout.pitch_y_um - layout.pseudopage_height_um, 3),
            ],
            "min_gutter_um": MIN_GUTTER_UM,
            "title_band_mm": layout.title_band_um / 1000,
            "nav_band_mm": layout.nav_band_um / 1000,
            "polarity": layout.polarity,
            "mirrored": layout.mirrored,
            "tiers": list(layout.tier_scales),
        },
        "sketch": {
            "plate_um": [first.plate_w_um, first.plate_h_um],
            "active_um": list(first.active.as_tuple()),
            "reserved": {k: list(v.as_tuple()) for k, v in first.reserved.items()},
            "text_areas": {
                band: list(area.as_tuple())
                for band in ("title_band", "nav_band")
                if (area := band_text_area(first, band)) is not None
            },
            "title_rect_um": title["rect_um"] if title else None,
            "nav_block_um": nav["block_um"] if nav else None,
            "placements": [list(pl.slot.as_tuple()) for pl in first.placements[:SKETCH_PLACEMENT_CAP]],
            "placements_total": len(first.placements),
            "pitch_um": [layout.pitch_x_um, layout.pitch_y_um],
        },
        "title": title,
        "nav": nav,
        "optics": optics_summary(profiles, reduction),
        "fonts_not_embedded": fonts,
        "warnings": warnings,
        "info": validation.info,
        "estimate": (
            "Verification can take several minutes and several GB of memory even for "
            "one page. Complete the required trial before starting the full corpus."
        ),
    }


def plan_from_report(report: dict[str, Any]) -> dict[str, Any] | None:
    """The 'what you'll get' payload for a FINISHED run, rebuilt from its
    report so an old run can be reopened with its plate sketch and numbers
    even when the page cannot re-plan it (no documents loaded). Same shape as
    plan_payload; the recorded plan is authoritative, nothing is re-planned."""
    plates = report.get("plates") or []
    if not plates:
        return None
    first = plates[0]
    plan = first.get("plan") or {}
    cfg = report.get("config", {}).get("profiles", {})
    fab = cfg.get("fab") or {}
    layout = cfg.get("layout") or {}
    placements = first.get("placements") or []
    ingest = first.get("ingest") or []
    furniture = first.get("furniture") or {}

    source_pages = len(
        {(rec.get("pdf"), rec.get("page")) for plate in plates for rec in plate.get("ingest") or []}
    )
    total_placements = sum(len(plate.get("placements") or []) for plate in plates)
    reduction = None
    page_size = None
    if placements and ingest:
        scale = float(placements[0]["scale"])
        frame = ingest[0].get("frame_pt") or [612.0, 792.0]
        reduction = round(1.0 / scale, 1)
        page_size = [
            round(frame[0] * 25400.0 / 72.0 * scale / 1000, 2),
            round(frame[1] * 25400.0 / 72.0 * scale / 1000, 2),
        ]

    width = float(fab.get("plate_width_mm", 152.4))
    height = float(fab.get("plate_height_mm", 152.4))
    edge = float(fab.get("edge_exclusion_mm", 3.0))
    pitch_x = float(layout.get("pitch_x_um", 2100.0))
    pitch_y = float(layout.get("pitch_y_um", 2600.0))
    page_w = float(layout.get("pseudopage_width_um", 1980.0))
    page_h = float(layout.get("pseudopage_height_um", 2560.0))
    active_w = width - 2 * edge
    active_h = height - 2 * edge
    size_id = next(
        (
            key
            for key, size in PLATE_SIZES.items()
            if size["width_mm"] is not None
            and abs(size["width_mm"] - width) < 1e-6
            and abs(size["height_mm"] - height) < 1e-6
        ),
        "custom",
    )

    title_rec = furniture.get("title") or None
    title = None
    if title_rec:
        x0, y0 = title_rec.get("origin_um", (0.0, 0.0))
        title = {
            "text": title_rec.get("text", ""),
            "align": title_rec.get("align", "center"),
            "requested_height_um": title_rec.get("requested_height_um"),
            "height_um": title_rec.get("height_um"),
            "width_um": title_rec.get("width_um"),
            "rect_um": [x0, y0, x0 + float(title_rec.get("width_um", 0.0)),
                        y0 + float(title_rec.get("height_um", 0.0))],
            "shrunk_to_fit": bool(title_rec.get("shrunk_to_fit")),
        }
    nav_rec = furniture.get("nav_band") or None
    nav = None
    if nav_rec:
        nav = {
            "lines": [line.get("text", "") for line in nav_rec.get("lines") or []],
            "char_height_um": nav_rec.get("char_height_um"),
            "requested_char_height_um": nav_rec.get("requested_char_height_um", 0.0),
            "shrunk_to_fit": bool(nav_rec.get("shrunk_to_fit")),
            "align": nav_rec.get("align", "center"),
            "block_um": list(nav_rec.get("block_um") or []) or None,
            "scale_bar": nav_rec.get("scale_bar") is not None,
        }

    optics: dict[str, Any] | None = None
    try:
        profiles = Profiles.model_validate(
            {"fab": fab, "reader": cfg.get("reader") or {}, "content": cfg.get("content") or {},
             "layout": layout}
        )
        optics = optics_summary(profiles, reduction)
    except Exception:  # an old report with a schema this version cannot load
        optics = None

    fonts: dict[str, list[str]] = {}
    for plate in plates:
        for rec in plate.get("ingest") or []:
            names = rec.get("fonts_not_embedded") or []
            if names:
                key = os.path.basename(str(rec.get("pdf", "")))
                fonts[key] = sorted(set(fonts.get(key, [])) | set(names))

    usable = int(plan.get("usable_slots", 0))
    return {
        "from_report": True,
        "source_pages": source_pages,
        "placements": total_placements,
        "plates": len(plates),
        "usable_slots_first_plate": usable,
        "theoretical_slots_first_plate": int(plan.get("theoretical_slots_pre_reservation", 0)),
        "utilization": round(len(placements) / max(1, usable), 4),
        "reduction": reduction,
        "pseudopage_mm": page_size,
        "plate": {
            "size_id": size_id,
            "width_mm": width,
            "height_mm": height,
            "edge_exclusion_mm": edge,
            "active_mm": [round(active_w, 2), round(active_h, 2)],
            "rows": int(active_h * 1000 // pitch_y) if pitch_y > 0 else 0,
            "cols": int(active_w * 1000 // pitch_x) if pitch_x > 0 else 0,
            "pitch_um": [pitch_x, pitch_y],
            "gutter_um": [round(pitch_x - page_w, 3), round(pitch_y - page_h, 3)],
            "min_gutter_um": MIN_GUTTER_UM,
            "title_band_mm": float(layout.get("title_band_um", 0.0)) / 1000,
            "nav_band_mm": float(layout.get("nav_band_um", 0.0)) / 1000,
            "polarity": layout.get("polarity", "clear_field"),
            "mirrored": bool(layout.get("mirrored", False)),
            "tiers": list(layout.get("tier_scales") or [1.0]),
        },
        "sketch": {
            "plate_um": list(plan.get("plate_um") or [width * 1000, height * 1000]),
            "active_um": list(plan.get("active_um") or [edge * 1000, edge * 1000,
                                                        (width - edge) * 1000,
                                                        (height - edge) * 1000]),
            "reserved": {k: list(v) for k, v in (plan.get("reserved") or {}).items()},
            "text_areas": {k: list(v) for k, v in (plan.get("text_areas") or {}).items()},
            "title_rect_um": title["rect_um"] if title else None,
            "nav_block_um": nav["block_um"] if nav else None,
            "placements": [list(pl["slot_um"]) for pl in placements[:SKETCH_PLACEMENT_CAP]],
            "placements_total": len(placements),
            "pitch_um": [pitch_x, pitch_y],
        },
        "title": title,
        "nav": nav,
        "optics": optics,
        "fonts_not_embedded": fonts,
        "warnings": list(report.get("validation", {}).get("warnings") or []),
        "info": list(report.get("validation", {}).get("info") or []),
        "estimate": (
            "This is the plan recorded in the run's report. Re-add the PDFs (Open a "
            "previous run does this when its files are still in the run folder) to plan "
            "and build again."
        ),
    }
