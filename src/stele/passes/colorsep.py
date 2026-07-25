"""Color separation for the projector route (M5, patent FIG. 14).

A color image region becomes a TRIAD of monochrome halftone sub-images —
R, G, B — stacked within the region's footprint. The patent's projector
gathers each through its color filter and recombines them optically.

Channel transform model (review finding 5): per-channel affine —
translation + scale (+ rotation, RECORDED but not applied to geometry:
FIG. 14 corrects focus/size/steering in the collimator optics, and rotating
dither geometry would break its DRC-clean-by-construction property; the
recorded transform is the projector's alignment contract, anchored by the
per-channel registration crosses).

Each channel's darkness = 1 - channel_intensity: bright red content must be
TRANSPARENT on the R-filtered mask.

Anaglyph 3-D (the patent's red/blue option) is DEFERRED: it is an authoring
concern — the source must supply a stereo pair (left eye -> R, right eye ->
B, G blanked), which no single flat PDF image provides. The triad machinery
already carries it once stereo inputs exist; tracked in docs/reference.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from stele.ir.model import ImageRegion

CHANNELS = ("R", "G", "B")
TRIAD_GAP_FRACTION = 0.04  # vertical gap between channel sub-images
# projector filter passbands (patent FIG. 14) for per-channel readability sim
CHANNEL_WAVELENGTHS_NM = {"R": 650.0, "G": 550.0, "B": 450.0}


@dataclass
class ChannelTransform:
    dx_um: float = 0.0
    dy_um: float = 0.0
    scale: float = 1.0
    rotation_deg: float = 0.0  # recorded for the projector; never applied to geometry

    def to_dict(self) -> dict:
        return {
            "dx_um": self.dx_um,
            "dy_um": self.dy_um,
            "scale": self.scale,
            "rotation_deg": self.rotation_deg,
        }


@dataclass
class TriadChannel:
    channel: str
    region: ImageRegion  # monochrome region carrying this channel's darkness
    transform: ChannelTransform = field(default_factory=ChannelTransform)


def triad_boxes(
    bbox_um: tuple[float, float, float, float],
    transforms: dict[str, ChannelTransform] | None = None,
) -> list[tuple[str, ChannelTransform, tuple[float, float, float, float]]]:
    """Pure geometry of the triad stack (shared by build and verify):
    R on top, G middle, B bottom."""
    transforms = transforms or {}
    x0, y0, x1, y1 = bbox_um
    h = y1 - y0
    gap = h * TRIAD_GAP_FRACTION
    sub_h = (h - 2 * gap) / 3.0
    out = []
    for i, ch in enumerate(CHANNELS):
        t = transforms.get(ch, ChannelTransform())
        top = y1 - i * (sub_h + gap)
        sub_y0 = top - sub_h
        w = (x1 - x0) * t.scale
        hh = sub_h * t.scale
        bx0 = x0 + t.dx_um
        by0 = sub_y0 + t.dy_um
        out.append((ch, t, (bx0, by0, bx0 + w, by0 + hh)))
    return out


def check_triad_containment(
    page_size_um: tuple[float, float],
    boxes: list[tuple[str, ChannelTransform, tuple[float, float, float, float]]],
) -> None:
    """Reject channel transforms whose sub-images leave the page frame.

    Offsets may move a sub-image outside its source region (the projector
    alignment use case) — the XOR gate then compares it against the page — but
    geometry beyond the frame would silently land in a neighboring page's slot
    (review finding 4). The occupancy audit independently re-checks the built
    plate; this is the actionable error at build time.
    """
    w, h = page_size_um
    for ch, t, (x0, y0, x1, y1) in boxes:
        if x0 < -1e-6 or y0 < -1e-6 or x1 > w + 1e-6 or y1 > h + 1e-6:
            raise ValueError(
                f"channel_transforms[{ch}] (dx={t.dx_um} um, dy={t.dy_um} um, "
                f"scale={t.scale}) pushes the {ch} sub-image to "
                f"({x0:.0f}, {y0:.0f})..({x1:.0f}, {y1:.0f}) um, outside the "
                f"{w:.0f} x {h:.0f} um page frame"
            )


def split_triad(
    region: ImageRegion,
    transforms: dict[str, ChannelTransform] | None = None,
) -> list[TriadChannel]:
    """Split a color region into three stacked monochrome channel regions,
    each carrying channel intensity as gray so the halftone pass needs no
    color awareness."""
    rgb = region.rgba[..., :3]
    a = region.rgba[..., 3:4]
    comp = rgb * a + (1.0 - a)  # composited over white

    out: list[TriadChannel] = []
    for i, (ch, t, bb) in enumerate(triad_boxes(region.bbox_um, transforms)):
        v = comp[..., i : i + 1]
        rgba = np.concatenate([v, v, v, np.ones_like(v)], axis=-1).astype(np.float32)
        out.append(
            TriadChannel(
                channel=ch,
                region=ImageRegion(bbox_um=bb, rgba=rgba, colorspace=f"channel:{ch}"),
                transform=t,
            )
        )
    return out


def registration_cross_um(
    bbox_um: tuple[float, float, float, float], size_um: float = 60.0
) -> list[np.ndarray]:
    """A small registration cross INSIDE a channel sub-image's top-left corner
    — the projector's per-channel alignment anchor. Inside, because geometry
    outside the image rect would read as phantom ink against the reference."""
    x0, _, _, y1 = bbox_um
    cx, cy = x0 + size_um, y1 - size_um
    b = size_um / 6.0
    return [
        np.array([[cx - size_um / 2, cy - b / 2], [cx + size_um / 2, cy - b / 2],
                  [cx + size_um / 2, cy + b / 2], [cx - size_um / 2, cy + b / 2]]),
        np.array([[cx - b / 2, cy - size_um / 2], [cx + b / 2, cy - size_um / 2],
                  [cx + b / 2, cy + size_um / 2], [cx - b / 2, cy + size_um / 2]]),
    ]
