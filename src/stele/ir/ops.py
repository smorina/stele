"""Geometry helpers on the IR (shapely in document space)."""

from __future__ import annotations

import numpy as np
from shapely.geometry import MultiPolygon, Polygon
from shapely.validation import make_valid


def ensure_valid(polys: list[Polygon]) -> list[Polygon]:
    """Enforce the IR invariant: valid, positive-area polygons only."""
    out: list[Polygon] = []
    for p in polys:
        if p.is_empty:
            continue
        if not p.is_valid:
            fixed = make_valid(p)
            out.extend(_polygons_of(fixed))
        else:
            out.append(p)
    return [p for p in out if p.area > 0.0]


def _polygons_of(geom) -> list[Polygon]:
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    if hasattr(geom, "geoms"):  # GeometryCollection
        out: list[Polygon] = []
        for g in geom.geoms:
            out.extend(_polygons_of(g))
        return out
    return []


def rings_of(polys: list[Polygon]) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Split polygons into exterior rings and hole rings as (N,2) float arrays."""
    outers: list[np.ndarray] = []
    holes: list[np.ndarray] = []
    for p in polys:
        outers.append(np.asarray(p.exterior.coords)[:-1])
        for ring in p.interiors:
            holes.append(np.asarray(ring.coords)[:-1])
    return outers, holes


def total_vertex_count(polys: list[Polygon]) -> int:
    n = 0
    for p in polys:
        n += len(p.exterior.coords) - 1
        for ring in p.interiors:
            n += len(ring.coords) - 1
    return n
