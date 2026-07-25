"""Intermediate representation.

The IR lives in DOCUMENT SPACE: micrometers at paper 1:1 scale, y-up.
Frontends and scale-agnostic passes never see the reduction ratio; the layout
engine assigns each placement its scale, and scale-aware materialization
happens per unique (page, scale) pair. The GDS backend snaps to DBU once, last.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from shapely.geometry import Polygon

UM_PER_INCH = 25400.0
LETTER_W_UM = 8.5 * UM_PER_INCH  # 215900.0
LETTER_H_UM = 11.0 * UM_PER_INCH  # 279400.0


@dataclass(frozen=True)
class SourceRef:
    """Provenance of a page: which file, which page, which frontend produced it."""

    pdf_path: str
    page_index: int  # 0-based
    frontend: str  # e.g. "raster@900dpi"
    sha256: str = ""  # input file hash (filled by ingest)


class Polarity(enum.Enum):
    """Which regions become chrome (opaque) on the plate.

    DARK_FIELD: source ink -> clear apertures in a chrome background.
    CLEAR_FIELD: source ink -> chrome features on a clear background.
    """

    CLEAR_FIELD = "clear_field"
    DARK_FIELD = "dark_field"


@dataclass(frozen=True)
class Orientation:
    """Explicit, typed orientation contract carried from manifest to report.

    Never inferred. The verify stage machine-checks chirality against this
    declaration using the plate's non-symmetric orientation glyph.
    """

    polarity: Polarity = Polarity.CLEAR_FIELD
    mirrored: bool = False  # True when geometry is mirrored for chrome-side-down viewing
    chrome_side_toward_viewer: bool = True

    def describe_tone_chain(self) -> list[str]:
        """The tone truth table: source ink -> GDS -> plate -> viewed appearance."""
        gds = "GDS polygon drawn where source has ink"
        if self.polarity is Polarity.CLEAR_FIELD:
            plate = "polygon = chrome retained (ink becomes opaque chrome on clear glass)"
            viewed = "dark text on bright background (transmitted light)"
        else:
            plate = "polygon = chrome removed (ink becomes clear aperture in chrome)"
            viewed = "bright text on dark background (transmitted light)"
        mirror = (
            "geometry mirrored (chrome side away from viewer reads correctly)"
            if self.mirrored
            else "geometry unmirrored (reads correctly with chrome side toward viewer)"
        )
        return [f"source ink -> {gds}", f"writer: {plate}", mirror, f"viewed: {viewed}"]


@dataclass
class ImageRegion:
    """A tagged continuous-tone region, preserved as composited color.

    RGBA float32 in [0, 1] plus source color-space metadata is kept from day
    one (review finding 5): grayscale is *derived*, never the stored form, so
    the color-separation route needs no re-ingestion.
    """

    bbox_um: tuple[float, float, float, float]  # (xmin, ymin, xmax, ymax), y-up doc space
    rgba: np.ndarray  # (H, W, 4) float32 in [0,1], composited against white
    colorspace: str = "DeviceRGB"

    def gray(self) -> np.ndarray:
        """Luminance (Rec. 709), alpha-composited over white."""
        rgb = self.rgba[..., :3]
        a = self.rgba[..., 3:4]
        comp = rgb * a + (1.0 - a)
        return (0.2126 * comp[..., 0] + 0.7152 * comp[..., 1] + 0.0722 * comp[..., 2]).astype(
            np.float32
        )


@dataclass
class PageIR:
    """One normalized page: filled binary geometry + tagged image regions.

    Invariant at every pass boundary: `ink` polygons are shapely-valid,
    positive-area; holes are represented as polygon interiors (resolved to
    hole-free geometry only at the GDS backend).
    """

    source: SourceRef
    size_um: tuple[float, float]
    ink: list[Polygon] = field(default_factory=list)
    images: list[ImageRegion] = field(default_factory=list)
    tags: dict[str, Any] = field(default_factory=dict)

    def ink_area_um2(self) -> float:
        return float(sum(p.area for p in self.ink))
