"""GDSII backend: PlateIR-ish placement plans -> gdstk library.

Holes are resolved here (GDS polygons cannot carry interiors): each page's
shapely polygons become hole-free polygon sets via gdstk boolean NOT.
Coordinates snap to DBU exactly once, in gdstk's write path
(unit = 1 um, precision = 1 nm).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import gdstk
import numpy as np

from stele.ir.model import PageIR

LAYER = 1
DATATYPE = 0


@dataclass
class GdsStats:
    cells: int = 0
    references: int = 0
    polygons: int = 0
    vertices: int = 0
    notes: list[str] = field(default_factory=list)


def shapely_to_gdstk(
    polys, scale: float = 1.0, layer: int = LAYER, datatype: int = DATATYPE
) -> list[gdstk.Polygon]:
    """Shapely polygons (holes allowed) -> hole-free gdstk polygons.

    Holes are subtracted PER POLYGON. A global outers-NOT-holes boolean would
    also erase unrelated geometry nested inside another polygon's hole (e.g.
    halftone dots inside the white gaps of a hatched region — cv2's CCOMP
    hierarchy reports those as new outer contours). Measured on the patent's
    scanned drawing sheets, the global form lost ~80% of the ink.
    """
    out: list[gdstk.Polygon] = []
    for p in polys:
        ext = gdstk.Polygon(
            np.asarray(p.exterior.coords)[:-1] * scale, layer=layer, datatype=datatype
        )
        if not p.interiors:
            out.append(ext)
            continue
        holes = [
            gdstk.Polygon(np.asarray(r.coords)[:-1] * scale, layer=layer, datatype=datatype)
            for r in p.interiors
        ]
        out.extend(gdstk.boolean([ext], holes, "not", layer=layer, datatype=datatype))
    return out


def page_to_polygons(page: PageIR, scale: float) -> list[gdstk.Polygon]:
    """Materialize one page at a plate scale factor (e.g. 1/109.1), hole-free."""
    return shapely_to_gdstk(page.ink, scale=scale)


def build_library(name: str = "STELE", precision: float = 1e-9) -> gdstk.Library:
    # unit = 1 um user units; precision (DBU) from the fab profile, default 1 nm
    return gdstk.Library(name=name, unit=1e-6, precision=precision)


def add_page_cell(
    lib: gdstk.Library, cell_name: str, page: PageIR, scale: float, stats: GdsStats
) -> gdstk.Cell:
    cell = lib.new_cell(cell_name)
    polys = page_to_polygons(page, scale)
    for p in polys:
        cell.add(p)
        stats.polygons += 1
        stats.vertices += len(p.points)
    stats.cells += 1
    return cell


def write_gds(lib: gdstk.Library, path: str) -> None:
    lib.write_gds(path)
