"""Layered profiles (pydantic v2). Values carry provenance in the shipped YAML:
`# source: patent example` vs `# assumption: engineering default` — the patent
fixes pseudopage-size examples and a handling allowance, NOT plate size,
wavelength, NA, or capacity (review finding 6)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class FabProfile(BaseModel):
    """The vendor contract. Unconfirmed fields are engineering defaults until a
    real rule deck is encoded; output stays a software-verified candidate handoff."""

    name: str
    plate_width_mm: float = 152.4
    plate_height_mm: float = 152.4
    edge_exclusion_mm: float = 3.0
    min_feature_um: float = 1.0
    min_space_um: float = 1.0
    process_bias_um: float = 0.0  # applied symmetric bias budget for measured gates
    dbu_nm: float = 1.0
    layer: int = 1
    datatype: int = 0
    # 4000 keeps every GDS XY record under 0x7FFF bytes: readers that treat
    # record lengths as SIGNED 16-bit (some fab tools) reject longer records
    # (klayout warns; measured). The format's hard ceiling is 8190.
    max_vertices_per_polygon: int = 4000
    max_file_size_mb: float = 2000.0
    max_cells: int = 100000
    density_min: float = 0.0
    density_max: float = 1.0
    # warn: geometry-gate failures -> status "pass_with_warnings" (never an
    # unqualified pass); fail: they fail the build outright
    drc_gate: Literal["warn", "fail"] = "warn"
    notes: str = ""


class ReaderProfile(BaseModel):
    """The bundled reading optics; the binding constraint on text size."""

    name: str
    magnification: float = 100.0
    numerical_aperture: float = 0.25
    wavelength_nm: float = 580.0
    contrast_criterion: float = 0.26  # Michelson, on thinnest strokes
    notes: str = ""

    @property
    def rayleigh_resolution_um(self) -> float:
        return 0.61 * (self.wavelength_nm / 1000.0) / self.numerical_aperture


class ContentPolicy(BaseModel):
    """How PDFs are ingested and binarized."""

    name: str
    dpi: int = 900
    threshold: Literal["fixed", "otsu"] = "fixed"
    fixed_threshold: int = 128
    fit_mode: Literal["fit", "strict"] = "fit"
    # placeholder until the vector frontend (M6): the raster path composites
    # everything, so there is nothing to detect yet — validation says so
    unsupported_features: Literal["raster", "error"] = "raster"
    expected_min_text_pt: float = 8.0  # for the closed-form WARNING only
    tag_images: bool = False
    # continuous-tone handling (C1 design): "threshold" binarizes images with
    # the page; "dither" excludes image regions from the trace and emits
    # full-cell ordered-dither geometry (DRC-clean by construction)
    image_mode: Literal["threshold", "dither"] = "threshold"
    dither_pitch_um: float = 0.0  # 0 = auto: max(fab min_feature, min_space)
    dither_mask: Literal["bluenoise", "clustered"] = "bluenoise"
    # per-plate cap on exact (high-variance) halftone tile patterns: each
    # unique pattern is its own GDS cell, so noisy content is otherwise
    # unbounded; past the cap tiles degrade to the bounded quantized-tone
    # library (deterministic; the tone gate still verifies the result)
    max_exact_tiles_per_plate: int = 20000
    # projector route (FIG. 14): "rgb_triad" splits each color image region
    # into stacked R/G/B halftone sub-images with registration crosses;
    # requires image_mode: dither. Channel transforms are translation + scale
    # APPLIED to geometry; rotation is RECORDED for the projector optics only
    # (the narrowed affine contract — rotating dither geometry would break its
    # DRC-clean-by-construction property).
    color_mode: Literal["mono", "rgb_triad"] = "mono"
    channel_transforms: dict[str, dict[str, float]] = {}

    @model_validator(mode="after")
    def _color_config_valid(self) -> ContentPolicy:
        import math

        if self.color_mode == "rgb_triad" and self.image_mode != "dither":
            raise ValueError("color_mode rgb_triad requires image_mode: dither")
        allowed_keys = {"dx_um", "dy_um", "scale", "rotation_deg"}
        for ch, cfg in self.channel_transforms.items():
            if ch not in ("R", "G", "B"):
                raise ValueError(f"channel_transforms key {ch!r}: channels are R, G, B")
            unknown = set(cfg) - allowed_keys
            if unknown:
                raise ValueError(
                    f"channel_transforms[{ch}]: unknown fields {sorted(unknown)}; "
                    f"allowed: {sorted(allowed_keys)}"
                )
            for k, v in cfg.items():
                if not math.isfinite(v):
                    raise ValueError(f"channel_transforms[{ch}].{k} must be finite, got {v}")
            s = cfg.get("scale", 1.0)
            if not (0.0 < s <= 2.0):
                raise ValueError(
                    f"channel_transforms[{ch}].scale must be in (0, 2], got {s}: "
                    f"zero/negative scales produce degenerate geometry"
                )
        return self


class LayoutSpec(BaseModel):
    """Plate layout: pseudopage geometry, tiling, reserved furniture."""

    name: str
    pseudopage_width_um: float = 1980.0  # source: patent example (FIG. 12)
    pseudopage_height_um: float = 2560.0  # source: patent example (FIG. 12)
    pitch_x_um: float = 2100.0
    pitch_y_um: float = 2600.0
    title_text: str = ""
    title_height_um: float = 2500.0  # naked-eye tier: ~2.5 mm (patent brick-text example)
    title_band_um: float = 4000.0  # reserved strip at plate top
    fiducial_size_um: float = 1500.0
    orientation_glyph_um: float = 1200.0  # height of the chirality "F"
    mirrored: bool = False
    polarity: str = "clear_field"  # clear_field | dark_field (pass lands in M4)
    # tier ladder (patent's discovery narrative): every page is placed once
    # per scale multiplier, largest first — naked-eye tiers lead the finder
    # to magnification. 1.0 = the standard pseudopage.
    tier_scales: list[float] = [1.0]
    # navigation furniture (page map, scale bar) reserved band at plate bottom
    nav_band_um: float = 0.0

    @model_validator(mode="after")
    def _pitch_fits(self) -> LayoutSpec:
        if self.pitch_x_um < self.pseudopage_width_um:
            raise ValueError(
                f"pitch_x ({self.pitch_x_um} um) < pseudopage width "
                f"({self.pseudopage_width_um} um): pseudopages would overlap"
            )
        if self.pitch_y_um < self.pseudopage_height_um:
            raise ValueError(
                f"pitch_y ({self.pitch_y_um} um) < pseudopage height "
                f"({self.pseudopage_height_um} um): pseudopages would overlap"
            )
        return self


class Profiles(BaseModel):
    fab: FabProfile
    reader: ReaderProfile
    content: ContentPolicy
    layout: LayoutSpec = Field(alias="layout")

    model_config = {"populate_by_name": True}
