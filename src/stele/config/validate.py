"""Cross-profile validation.

Per review finding 2: the closed-form legibility check is an EARLY WARNING
heuristic only — point size does not determine stroke width, and Rayleigh
resolution is not legibility. Hard gates are measured on final plate-scale
geometry (verify.geometry_gates) and by the readability simulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from stele.config.profiles import Profiles

# Heuristic: typical serif body-text stroke ~= 25 um per point of nominal size
# (Times-like faces; varies +/-40% across fonts — hence WARNING, not gate).
STROKE_UM_PER_PT = 25.0


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_profiles(profiles: Profiles) -> ValidationResult:
    r = ValidationResult()
    fab, reader, content, layout = (
        profiles.fab,
        profiles.reader,
        profiles.content,
        profiles.layout,
    )

    # -- geometry sanity (errors) --
    active_w = fab.plate_width_mm * 1000 - 2 * fab.edge_exclusion_mm * 1000
    active_h = fab.plate_height_mm * 1000 - 2 * fab.edge_exclusion_mm * 1000
    if active_w < layout.pitch_x_um or active_h < layout.pitch_y_um:
        r.errors.append(
            f"active area {active_w:.0f}x{active_h:.0f} um cannot fit a single "
            f"pseudopage slot ({layout.pitch_x_um}x{layout.pitch_y_um} um)"
        )
    if layout.title_band_um > 0 and layout.title_height_um > layout.title_band_um:
        r.errors.append(
            f"title text height ({layout.title_height_um} um) exceeds reserved "
            f"title band ({layout.title_band_um} um)"
        )
    if fab.min_feature_um <= 0:
        r.errors.append("fab min_feature_um must be positive")

    # -- closed-form legibility EARLY WARNING (never a gate) --
    # Letter page fit into the pseudopage box determines the nominal reduction.
    from stele.ir.model import LETTER_H_UM, LETTER_W_UM

    reduction = 1.0 / min(
        layout.pseudopage_width_um / LETTER_W_UM, layout.pseudopage_height_um / LETTER_H_UM
    )
    est_stroke_doc_um = STROKE_UM_PER_PT * content.expected_min_text_pt
    est_stroke_plate_um = est_stroke_doc_um / reduction
    resolvable_um = reader.rayleigh_resolution_um
    r.info.append(
        f"nominal reduction {reduction:.1f}:1; estimated {content.expected_min_text_pt:.0f}pt "
        f"stroke ~{est_stroke_plate_um:.2f} um on plate; reader Rayleigh limit "
        f"{resolvable_um:.2f} um (NA {reader.numerical_aperture}, {reader.wavelength_nm:.0f} nm)"
    )
    if est_stroke_plate_um < resolvable_um:
        r.warnings.append(
            f"HEURISTIC: {content.expected_min_text_pt:.0f}pt text at {reduction:.1f}:1 gives "
            f"~{est_stroke_plate_um:.2f} um strokes, below the reader's ~{resolvable_um:.2f} um "
            f"Rayleigh estimate — expect illegible text; hard verdict comes from the "
            f"measured-geometry gate and readability simulation"
        )
    if est_stroke_plate_um < fab.min_feature_um + fab.process_bias_um:
        r.warnings.append(
            f"HEURISTIC: estimated {est_stroke_plate_um:.2f} um strokes are below the writer's "
            f"min feature + bias ({fab.min_feature_um + fab.process_bias_um:.2f} um) — "
            f"strokes may not print; measured gate will decide"
        )

    # honesty about placeholder contract fields (progress-review finding 5)
    r.info.append(
        "unenforced fab-profile fields (recorded, not gated): allowed GDS elements; "
        "hierarchy/AREF limits; coordinate limits (safe by construction); inspection "
        "policy; required marks beyond fiducials/ID/glyph; mask exposure tone "
        "(positive assumed). content.unsupported_features is a placeholder until "
        "the vector frontend exists (raster path composites everything)."
    )

    return r
