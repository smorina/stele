"""Layout planning: slots, reserved regions, tiers, multi-plate placement.

Planning runs BEFORE materialization (review finding 1): every placement gets
its reduction scale here, so scale-aware passes can materialize each unique
(page, scale) with correct plate-scale parameters.

Tier ladder (M4): each page is placed once per tier scale, largest first —
the patent's "naked-eye text leads the finder to magnify" narrative. Tiered
placements span rectangular blocks of slots on an occupancy grid.

Multi-plate (M4): plan_plates() fills plates greedily in reading order and
stamps each with plate-set identity (k of n). plan_plate() keeps the strict
single-plate contract and raises on overflow.

Plate space: micrometers, y-up, origin at plate lower-left.
Reading order: row-major, top row first, left to right (recorded in report).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from stele.config.profiles import FabProfile, LayoutSpec
from stele.ingest.normalize import PageJob
from stele.ir.model import UM_PER_INCH

PT_TO_UM = UM_PER_INCH / 72.0


@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    def intersects(self, other: Rect) -> bool:
        return not (
            self.x1 <= other.x0 or other.x1 <= self.x0 or self.y1 <= other.y0 or other.y1 <= self.y0
        )

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


@dataclass(frozen=True)
class Placement:
    """One pseudopage instance: which page, where, at what scale."""

    page_key: str  # unique materialization key, e.g. "sha[:12]:p3@s0.009162"
    job: PageJob
    slot: Rect  # slot block on the plate (pitch-multiple sized)
    origin: tuple[float, float]  # lower-left of the scaled page frame
    scale: float  # doc um -> plate um (fit scale x tier scale)
    tier_scale: float = 1.0

    def content_rect(self) -> Rect:
        w = self.job.frame_pt[0] * PT_TO_UM * self.scale
        h = self.job.frame_pt[1] * PT_TO_UM * self.scale
        return Rect(self.origin[0], self.origin[1], self.origin[0] + w, self.origin[1] + h)


@dataclass
class PlatePlan:
    plate_w_um: float
    plate_h_um: float
    active: Rect
    reserved: dict[str, Rect] = field(default_factory=dict)
    slots: list[Rect] = field(default_factory=list)  # usable 1x1 slots, reading order
    placements: list[Placement] = field(default_factory=list)
    theoretical_slots: int = 0
    plate_index: int = 0
    plate_count: int = 1
    page_offset: int = 0  # global ordinal of this plate's first placement

    def capacity(self) -> int:
        return len(self.slots)

    def summary(self) -> dict:
        return {
            "plate_um": (self.plate_w_um, self.plate_h_um),
            "active_um": self.active.as_tuple(),
            "reserved": {k: v.as_tuple() for k, v in self.reserved.items()},
            "theoretical_slots_pre_reservation": self.theoretical_slots,
            "usable_slots": self.capacity(),
            "placed_pages": len(self.placements),
            "plate_index": self.plate_index,
            "plate_count": self.plate_count,
            "page_offset": self.page_offset,
            "tiers": sorted({pl.tier_scale for pl in self.placements}, reverse=True),
            "reading_order": "row-major, top row first, left-to-right",
        }


class OverflowError_(ValueError):
    pass


def _plate_frame(fab: FabProfile, layout: LayoutSpec) -> PlatePlan:
    plate_w = fab.plate_width_mm * 1000.0
    plate_h = fab.plate_height_mm * 1000.0
    excl = fab.edge_exclusion_mm * 1000.0
    active = Rect(excl, excl, plate_w - excl, plate_h - excl)
    plan = PlatePlan(plate_w_um=plate_w, plate_h_um=plate_h, active=active)

    if layout.title_band_um > 0:
        plan.reserved["title_band"] = Rect(
            active.x0, active.y1 - layout.title_band_um, active.x1, active.y1
        )
    if layout.nav_band_um > 0:
        plan.reserved["nav_band"] = Rect(
            active.x0, active.y0, active.x1, active.y0 + layout.nav_band_um
        )
    f = layout.fiducial_size_um
    m = f * 0.25
    plan.reserved.update(
        {
            "fiducial_sw": Rect(active.x0, active.y0, active.x0 + f + m, active.y0 + f + m),
            "fiducial_se": Rect(active.x1 - f - m, active.y0, active.x1, active.y0 + f + m),
            "fiducial_nw": Rect(active.x0, active.y1 - f - m, active.x0 + f + m, active.y1),
            "fiducial_ne": Rect(active.x1 - f - m, active.y1 - f - m, active.x1, active.y1),
        }
    )
    g_h = layout.orientation_glyph_um
    g_w = 0.6 * g_h
    plan.reserved["orientation_glyph"] = Rect(
        active.x0 + f + 2 * m,
        active.y0,
        active.x0 + f + 2 * m + g_w * 1.5,
        active.y0 + g_h * 1.2,
    )
    return plan


def _grid(plan: PlatePlan, layout: LayoutSpec) -> tuple[np.ndarray, int, int]:
    """Occupancy grid over slot cells; reserved-intersecting cells pre-marked."""
    active = plan.active
    cols = int((active.x1 - active.x0) // layout.pitch_x_um)
    rows = int((active.y1 - active.y0) // layout.pitch_y_um)
    plan.theoretical_slots = cols * rows
    occupied = np.zeros((rows, cols), dtype=bool)
    reserved = list(plan.reserved.values())
    for r_i in range(rows):
        y1 = active.y1 - r_i * layout.pitch_y_um
        y0 = y1 - layout.pitch_y_um
        for c_i in range(cols):
            x0 = active.x0 + c_i * layout.pitch_x_um
            slot = Rect(x0, y0, x0 + layout.pitch_x_um, y1)
            if any(slot.intersects(rv) for rv in reserved):
                occupied[r_i, c_i] = True
            else:
                plan.slots.append(slot)
    return occupied, rows, cols


def _find_block(occupied: np.ndarray, span_r: int, span_c: int, start: int = 0) -> tuple[int, int] | None:
    rows, cols = occupied.shape
    for r in range(0, rows - span_r + 1):
        for c in range(0, cols - span_c + 1):
            if not occupied[r : r + span_r, c : c + span_c].any():
                return r, c
    return None


def _units(jobs: list[PageJob], layout: LayoutSpec) -> list[tuple[PageJob, float]]:
    """Placement units: every page once per tier scale, LARGEST tier first."""
    tiers = sorted(set(layout.tier_scales or [1.0]), reverse=True)
    return [(job, t) for t in tiers for job in jobs]


def _fill(
    fab: FabProfile,
    layout: LayoutSpec,
    units: list[tuple[PageJob, float]],
    fit_mode: str,
) -> tuple[PlatePlan, int]:
    """Fill one plate; returns (plan, units consumed). Stops at the first unit
    that no longer fits (reading order is preserved — no backfilling)."""
    plan = _plate_frame(fab, layout)
    occupied, rows, cols = _grid(plan, layout)
    active = plan.active
    consumed = 0
    for job, tier in units:
        fw_um = job.frame_pt[0] * PT_TO_UM
        fh_um = job.frame_pt[1] * PT_TO_UM
        s0 = min(layout.pseudopage_width_um / fw_um, layout.pseudopage_height_um / fh_um)
        if fit_mode == "strict" and abs(fw_um - 215900.0) + abs(fh_um - 279400.0) > 1.0:
            raise ValueError(
                f"strict fit mode: page {job.ordinal} frame {fw_um:.0f}x{fh_um:.0f} um "
                f"is not US Letter"
            )
        scale = s0 * tier
        span_c = int(np.ceil(fw_um * scale / layout.pitch_x_um))
        span_r = int(np.ceil(fh_um * scale / layout.pitch_y_um))
        if span_r > rows or span_c > cols:
            raise OverflowError_(
                f"tier x{tier:g} page needs a {span_c}x{span_r}-slot block; "
                f"the plate grid is only {cols}x{rows}"
            )
        pos = _find_block(occupied, span_r, span_c)
        if pos is None:
            break
        r, c = pos
        occupied[r : r + span_r, c : c + span_c] = True
        x0 = active.x0 + c * layout.pitch_x_um
        y1 = active.y1 - r * layout.pitch_y_um
        block = Rect(x0, y1 - span_r * layout.pitch_y_um, x0 + span_c * layout.pitch_x_um, y1)
        gx = block.x0 + (block.x1 - block.x0 - fw_um * scale) / 2.0
        gy = block.y0 + (block.y1 - block.y0 - fh_um * scale) / 2.0
        plan.placements.append(
            Placement(
                page_key=f"{job.pdf_sha256[:12]}:p{job.page_index}@s{scale:.6f}",
                job=job,
                slot=block,
                origin=(gx, gy),
                scale=scale,
                tier_scale=tier,
            )
        )
        consumed += 1
    return plan, consumed


def plan_plate(
    fab: FabProfile, layout: LayoutSpec, jobs: list[PageJob], fit_mode: str = "fit"
) -> PlatePlan:
    """Single-plate contract: everything must fit, else OverflowError_."""
    units = _units(jobs, layout)
    plan, consumed = _fill(fab, layout, units, fit_mode)
    if consumed < len(units):
        raise OverflowError_(
            f"{len(units)} placements exceed usable plate capacity of "
            f"{plan.capacity()} slots ({plan.theoretical_slots} theoretical before "
            f"reserved regions); {consumed} fit. Use plan_plates() / the multi-plate "
            f"build for plate sets, or reduce the page selection."
        )
    return plan


def plan_plates(
    fab: FabProfile, layout: LayoutSpec, jobs: list[PageJob], fit_mode: str = "fit"
) -> list[PlatePlan]:
    """Greedy plate-set planning in reading order, with set identity stamped."""
    units = _units(jobs, layout)
    if not units:
        plan, _ = _fill(fab, layout, [], fit_mode)
        return [plan]
    plans: list[PlatePlan] = []
    offset = 0
    while units:
        plan, consumed = _fill(fab, layout, units, fit_mode)
        if consumed == 0:
            raise OverflowError_(
                "a single placement does not fit an empty plate — tier scale too "
                "large for the plate/pitch"
            )
        plan.page_offset = offset
        plans.append(plan)
        units = units[consumed:]
        offset += consumed
    for k, plan in enumerate(plans):
        plan.plate_index = k
        plan.plate_count = len(plans)
    return plans
