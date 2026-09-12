"""Discoverability & navigation furniture (M4; patent's own emphasis).

Everything here is generated geometry in plate-scale um, meant for reserved
regions: scale bars, plate-set identity lines, the page map (a human-readable
index of what is where), and tier ladder headers. The patent's discovery
narrative — naked-eye text leading a finder to magnify — is implemented by
rendering the same facts at descending sizes.
"""

from __future__ import annotations

import numpy as np
from shapely.geometry import Polygon

from stele.layout.fiducials import measure_text_width, stroke_text

TEXT_LEADING = 1.5  # line pitch as a multiple of character height


def block_height(n_lines: int, char_h: float, leading: float = TEXT_LEADING) -> float:
    """Vertical extent of an n-line block: the first line's height plus the
    leading of every further line."""
    if n_lines <= 0:
        return 0.0
    return char_h * (1.0 + (n_lines - 1) * leading)


def fit_char_height(
    lines: list[str],
    area: tuple[float, float, float, float],
    max_char_h: float,
    leading: float = TEXT_LEADING,
    min_char_h: float = 1.0,
) -> float:
    """Largest character height <= max_char_h at which every line fits the
    area's width and the whole block fits its height."""
    x0, y0, x1, y1 = area
    n = len(lines)
    if n == 0:
        return max_char_h
    h = min(max_char_h, (y1 - y0) / (1.0 + (n - 1) * leading))
    ref_h = 100.0
    widest = max(measure_text_width(line, ref_h) for line in lines)
    if widest > 0:
        h = min(h, (x1 - x0) * ref_h / widest)
    return max(min_char_h, h)


def text_block_aligned(
    lines: list[str],
    area: tuple[float, float, float, float],
    char_h: float,
    align: str = "center",
    leading: float = TEXT_LEADING,
) -> tuple[list[Polygon], dict]:
    """Multi-line stroke text laid out INSIDE `area`: each line aligned
    horizontally, the block centered vertically. Lines are never allowed to
    run outside the area (the caller sizes char_h with fit_char_height).
    Returns (polygons, placement record)."""
    x0, y0, x1, y1 = area
    n = len(lines)
    polys: list[Polygon] = []
    if n == 0:
        return polys, {"lines": [], "char_height_um": char_h}
    bh = block_height(n, char_h, leading)
    base = y0 + ((y1 - y0) - bh) / 2.0  # lower-left of the LAST line
    y = base + (n - 1) * char_h * leading
    placed: list[dict] = []
    for line in lines:
        width = measure_text_width(line, char_h)
        if align == "left":
            lx = x0
        elif align == "right":
            lx = x1 - width
        else:
            lx = x0 + ((x1 - x0) - width) / 2.0
        p, _ = stroke_text(line, char_h, (lx, y))
        polys.extend(p)
        placed.append({"text": line, "origin_um": (round(lx, 3), round(y, 3)),
                       "width_um": round(width, 3)})
        y -= char_h * leading
    record = {
        "lines": placed,
        "char_height_um": round(char_h, 3),
        "align": align,
        "block_um": (round(x0, 3), round(base, 3), round(x1, 3), round(base + bh, 3)),
    }
    return polys, record


def labeled_scale_bar(
    x0: float,
    y0: float,
    length_um: float = 1000.0,
    tick_um: float = 100.0,
    height_um: float = 60.0,
    label_height_um: float = 300.0,
    gap_um: float = 150.0,
) -> tuple[list[np.ndarray], list[Polygon], float]:
    """A scale bar with its length written beside it ("1 MM"), so a finder
    under the microscope can calibrate without the report. Returns
    (bar rings, label polygons, total width um)."""
    rings = scale_bar(x0, y0, length_um, tick_um, height_um)
    mm = length_um / 1000.0
    label = f"{mm:g} MM" if mm >= 1 else f"{length_um:g} UM"
    polys, w = stroke_text(label, label_height_um, (x0 + length_um + gap_um, y0))
    return rings, polys, length_um + gap_um + w


def scale_bar(
    x0: float, y0: float, length_um: float = 1000.0, tick_um: float = 100.0, height_um: float = 60.0
) -> list[np.ndarray]:
    """A labeled ruler: solid baseline with ticks every tick_um."""
    bars: list[np.ndarray] = []
    bar_h = height_um * 0.25
    bars.append(np.array([[x0, y0], [x0 + length_um, y0], [x0 + length_um, y0 + bar_h], [x0, y0 + bar_h]]))
    n_ticks = int(round(length_um / tick_um))
    for i in range(n_ticks + 1):
        tx = x0 + i * tick_um
        th = height_um if i % 5 == 0 else height_um * 0.6
        bars.append(np.array([[tx, y0], [tx + bar_h, y0], [tx + bar_h, y0 + th], [tx, y0 + th]]))
    return bars


def text_block(
    lines: list[str], origin: tuple[float, float], char_h: float, leading: float = 1.5
) -> list[Polygon]:
    """Multi-line stroke text, top line first, lower-left at origin."""
    polys: list[Polygon] = []
    x0, y0 = origin
    y = y0 + (len(lines) - 1) * char_h * leading
    for line in lines:
        p, _ = stroke_text(line, char_h, (x0, y))
        polys.extend(p)
        y -= char_h * leading
    return polys


def page_map_lines(
    plate_name: str,
    plate_index: int,
    plate_count: int,
    first_page: int,
    last_page: int,
    sources: list[str],
    tiers: list[float] | None = None,
    max_sources: int = 4,
) -> list[str]:
    """The page map: plate-set identity + reading order + content index.

    first_page/last_page are SOURCE page ordinals (0-based), not placement
    counts — a tiered plate repeats every page once per tier, and labeling
    placement ordinals as page ranges misled the reader (review finding 7).
    Multi-tier plates say so explicitly.
    """
    lines = [
        f"{plate_name}  PLATE {plate_index + 1} OF {plate_count}",
        f"PAGES {first_page + 1}-{last_page + 1}  READ ROWS L-R, TOP DOWN",
    ]
    tiers = sorted(set(tiers or []), reverse=True)
    if len(tiers) > 1:
        ladder = " ".join(f"X{t:g}" for t in tiers)
        lines.append(f"EACH PAGE AT SIZES {ladder}  LARGEST FIRST")
    for s in sources[:max_sources]:
        lines.append(f"+ {s[:48].upper()}")
    if len(sources) > max_sources:
        lines.append(f"+ {len(sources) - max_sources} MORE SOURCES")
    return lines


def tier_header(tier_name: str, scale_note: str) -> str:
    return f"{tier_name.upper()} - {scale_note}"
