"""The settings surface used by the local UI, mapped onto the profile models.

Every setting is optional: missing keys take the defaults below, so an older
page or a saved run's settings.json keeps working. The keys mirror the
plain-language groups on the page — document capture, mask writer, plate,
page grid, title, guide band, orientation, microscope — and every value is
range-checked here with a plain-language message before a profile is built.
Fields Dean flagged as knobs nobody turns (process bias, vertex limits,
fiducial and glyph sizes) stay in the YAML profiles and the CLI.
"""

from __future__ import annotations

from typing import Any

from stele.config.profiles import ContentPolicy, FabProfile, LayoutSpec, Profiles, ReaderProfile

# Common square photomask blanks. Widths and heights in mm.
PLATE_SIZES: dict[str, dict[str, Any]] = {
    "6in": {
        "label": "6-inch square · 152.4 mm",
        "width_mm": 152.4,
        "height_mm": 152.4,
        "note": "The standard 6\" photomask blank (the patent's example plate).",
    },
    "5in": {
        "label": "5-inch square · 127 mm",
        "width_mm": 127.0,
        "height_mm": 127.0,
        "note": "The other common photomask blank size.",
    },
    "150mm": {
        "label": "150 mm square",
        "width_mm": 150.0,
        "height_mm": 150.0,
        "note": "Metric 150 mm blank.",
    },
    "125mm": {
        "label": "125 mm square",
        "width_mm": 125.0,
        "height_mm": 125.0,
        "note": "Metric 125 mm blank.",
    },
    "4in": {
        "label": "4-inch square · 101.6 mm",
        "width_mm": 101.6,
        "height_mm": 101.6,
        "note": "Small blank; useful for trials.",
    },
    "custom": {
        "label": "Custom size",
        "width_mm": None,
        "height_mm": None,
        "note": "Enter the blank's width and height in millimetres.",
    },
}

# Pages abut when the gutter is 0 — the pages' own margins separate the text.
# A few micrometres are always kept so the traced ink footprint (half a build
# pixel past the page frame) never crosses into the neighbouring slot, which
# the placement audit would flag.
MIN_GUTTER_UM = 4.0

CONTENT_KINDS = ("digital", "scan", "photos")
ALIGNMENTS = ("left", "center", "right")
POLARITIES = ("clear_field", "dark_field")
TIER_OPTIONS: dict[str, list[float]] = {
    "1": [1.0],
    "4,1": [4.0, 1.0],
    "16,4,1": [16.0, 4.0, 1.0],
}

DEFAULT_SETTINGS: dict[str, Any] = {
    # document capture
    "dpi": 900,
    "content_kind": "digital",
    "expected_min_text_pt": 8.0,
    # mask writer
    "min_feature_um": 1.0,
    # plate
    "plate_size": "6in",
    "plate_width_mm": 152.4,
    "plate_height_mm": 152.4,
    "edge_exclusion_mm": 3.0,
    # page grid (patent FIG. 12 example page; no gutters — decision 2026-09)
    "pseudopage_width_um": 1980.0,
    "pseudopage_height_um": 2560.0,
    "gutter_x_um": 0.0,
    "gutter_y_um": 0.0,
    # title band at the top of the active area
    "title_text": "",
    "title_height_mm": 2.5,
    "title_band_mm": 4.0,
    "title_align": "center",
    # guide (navigation) band at the bottom of the active area
    "nav_band": True,
    "nav_band_mm": 5.0,
    "nav_text": "",
    "nav_text_height_um": 0.0,
    "nav_align": "center",
    # orientation
    "polarity": "clear_field",
    "mirrored": False,
    "tiers": "1",
    # microscope
    "magnification": 100.0,
    "numerical_aperture": 0.25,
    "wavelength_nm": 580.0,
    "contrast_criterion": 0.26,
}


def _number(settings: dict[str, Any], key: str, lo: float, hi: float, label: str,
            unit: str = "", integer: bool = False) -> float | int:
    raw = settings.get(key, DEFAULT_SETTINGS[key])
    try:
        value = int(raw) if integer else float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number") from exc
    if value != value or value < lo or value > hi:  # NaN fails both bounds
        suffix = f" {unit}" if unit else ""
        raise ValueError(f"{label} must be between {lo:g} and {hi:g}{suffix}")
    return value


def _choice(settings: dict[str, Any], key: str, choices: tuple[str, ...] | dict, label: str) -> str:
    value = str(settings.get(key, DEFAULT_SETTINGS[key]))
    if value not in choices:
        raise ValueError(f"{label} must be one of: {', '.join(choices)}")
    return value


def _text(settings: dict[str, Any], key: str, max_len: int) -> str:
    value = settings.get(key, DEFAULT_SETTINGS[key])
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    # keep printable characters only; the stroke font has no control glyphs
    text = "".join(ch for ch in text if ch == "\n" or ch.isprintable())
    return text.strip()[:max_len]


def normalized_settings(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    """Type, range-check and fill in every UI setting."""
    settings = {**DEFAULT_SETTINGS, **(raw or {})}
    out: dict[str, Any] = {}

    out["dpi"] = _number(settings, "dpi", 300, 1200, "Capture detail", "DPI", integer=True)
    out["content_kind"] = _choice(settings, "content_kind", CONTENT_KINDS, "Document type")
    out["expected_min_text_pt"] = _number(
        settings, "expected_min_text_pt", 1, 72, "Smallest text in the document", "pt"
    )
    out["min_feature_um"] = _number(
        settings, "min_feature_um", 0.25, 25, "Writer minimum line", "µm"
    )

    plate_size = _choice(settings, "plate_size", tuple(PLATE_SIZES), "Plate size")
    out["plate_size"] = plate_size
    preset = PLATE_SIZES[plate_size]
    if preset["width_mm"] is not None:
        out["plate_width_mm"] = float(preset["width_mm"])
        out["plate_height_mm"] = float(preset["height_mm"])
    else:
        out["plate_width_mm"] = _number(settings, "plate_width_mm", 10, 1000, "Plate width", "mm")
        out["plate_height_mm"] = _number(
            settings, "plate_height_mm", 10, 1000, "Plate height", "mm"
        )
    out["edge_exclusion_mm"] = _number(
        settings, "edge_exclusion_mm", 0, 25, "Unusable border", "mm"
    )
    if 2 * out["edge_exclusion_mm"] >= min(out["plate_width_mm"], out["plate_height_mm"]):
        raise ValueError("The unusable border leaves no room on the plate")

    out["pseudopage_width_um"] = _number(
        settings, "pseudopage_width_um", 200, 100000, "Page width on glass", "µm"
    )
    out["pseudopage_height_um"] = _number(
        settings, "pseudopage_height_um", 200, 100000, "Page height on glass", "µm"
    )
    out["gutter_x_um"] = _number(settings, "gutter_x_um", 0, 50000, "Horizontal gutter", "µm")
    out["gutter_y_um"] = _number(settings, "gutter_y_um", 0, 50000, "Vertical gutter", "µm")

    out["title_text"] = _text(settings, "title_text", 120).replace("\n", " ")
    out["title_height_mm"] = _number(settings, "title_height_mm", 0.2, 30, "Title height", "mm")
    out["title_band_mm"] = _number(settings, "title_band_mm", 0, 40, "Title band", "mm")
    if 0 < out["title_band_mm"] < out["title_height_mm"]:
        raise ValueError("The title band must be at least as tall as the title text")
    out["title_align"] = _choice(settings, "title_align", ALIGNMENTS, "Title alignment")

    out["nav_band"] = bool(settings.get("nav_band", DEFAULT_SETTINGS["nav_band"]))
    out["nav_band_mm"] = _number(settings, "nav_band_mm", 1, 40, "Guide band", "mm")
    out["nav_text"] = _text(settings, "nav_text", 600)
    if out["nav_text"].count("\n") > 5:
        raise ValueError("The plate description can have at most 6 lines")
    out["nav_text_height_um"] = _number(
        settings, "nav_text_height_um", 0, 5000, "Description text height", "µm"
    )
    out["nav_align"] = _choice(settings, "nav_align", ALIGNMENTS, "Description alignment")

    out["polarity"] = _choice(settings, "polarity", POLARITIES, "Plate tone")
    out["mirrored"] = bool(settings.get("mirrored", DEFAULT_SETTINGS["mirrored"]))
    out["tiers"] = _choice(settings, "tiers", TIER_OPTIONS, "Magnification ladder")

    out["magnification"] = _number(settings, "magnification", 1, 2000, "Magnification", "×")
    out["numerical_aperture"] = _number(
        settings, "numerical_aperture", 0.02, 1.4, "Numerical aperture"
    )
    out["wavelength_nm"] = _number(settings, "wavelength_nm", 350, 1100, "Wavelength", "nm")
    out["contrast_criterion"] = _number(
        settings, "contrast_criterion", 0.01, 0.99, "Minimum readable contrast"
    )
    return out


def profiles_from_settings(raw: dict[str, Any] | None = None) -> Profiles:
    settings = normalized_settings(raw)
    kind = settings["content_kind"]
    pitch_x = settings["pseudopage_width_um"] + max(settings["gutter_x_um"], MIN_GUTTER_UM)
    pitch_y = settings["pseudopage_height_um"] + max(settings["gutter_y_um"], MIN_GUTTER_UM)
    return Profiles(
        fab=FabProfile(
            name=f"generic-laserwriter-{settings['plate_size']}",
            plate_width_mm=settings["plate_width_mm"],
            plate_height_mm=settings["plate_height_mm"],
            edge_exclusion_mm=settings["edge_exclusion_mm"],
            min_feature_um=settings["min_feature_um"],
            min_space_um=settings["min_feature_um"],
            notes=(
                "Engineering defaults, not a confirmed vendor rule deck. "
                "Confirm these limits and the exposure tone with the mask shop."
            ),
        ),
        reader=ReaderProfile(
            name="ui-reader",
            magnification=settings["magnification"],
            numerical_aperture=settings["numerical_aperture"],
            wavelength_nm=settings["wavelength_nm"],
            contrast_criterion=settings["contrast_criterion"],
            notes=(
                "Microscope optics entered in the local UI; the readability simulation "
                "uses them, and the microscope panel can vary them after a build."
            ),
        ),
        content=ContentPolicy(
            name="ui-default",
            dpi=settings["dpi"],
            threshold="fixed" if kind == "digital" else "otsu",
            tag_images=kind == "photos",
            image_mode="dither" if kind == "photos" else "threshold",
            expected_min_text_pt=settings["expected_min_text_pt"],
        ),
        layout=LayoutSpec(
            name="ui-layout",
            pseudopage_width_um=settings["pseudopage_width_um"],
            pseudopage_height_um=settings["pseudopage_height_um"],
            pitch_x_um=pitch_x,
            pitch_y_um=pitch_y,
            title_text=settings["title_text"],
            title_height_um=settings["title_height_mm"] * 1000.0,
            title_band_um=settings["title_band_mm"] * 1000.0,
            title_align=settings["title_align"],
            nav_band_um=settings["nav_band_mm"] * 1000.0 if settings["nav_band"] else 0.0,
            nav_text=settings["nav_text"],
            nav_text_height_um=settings["nav_text_height_um"],
            nav_align=settings["nav_align"],
            polarity=settings["polarity"],
            mirrored=settings["mirrored"],
            tier_scales=list(TIER_OPTIONS[settings["tiers"]]),
        ),
    )


def preset_payload() -> dict[str, Any]:
    """Defaults plus the choice lists, so the page and the server never
    disagree about what a setting may be."""
    return {
        "id": "six-inch-generic",
        "name": "6-inch plate · standard microscope",
        "description": (
            "A 152.4 mm chrome-on-glass plate using unconfirmed, conservative "
            "engineering defaults for a 1 µm laser writer and a 100×, NA 0.25 reader."
        ),
        "settings": DEFAULT_SETTINGS,
        "choices": {
            "plate_sizes": [
                {"id": key, **value} for key, value in PLATE_SIZES.items()
            ],
            "tiers": [
                {"id": key, "scales": scales} for key, scales in TIER_OPTIONS.items()
            ],
            "alignments": list(ALIGNMENTS),
            "polarities": [
                {
                    "id": "clear_field",
                    "label": "Clear field · dark text on bright glass",
                    "help": "Drawn shapes become chrome. The writer exposes everything except the text.",
                },
                {
                    "id": "dark_field",
                    "label": "Dark field · bright text in chrome",
                    "help": (
                        "Drawn shapes become clear windows in a chrome field. The writer exposes "
                        "only the text (usually faster), and bright-on-dark reading can be easier "
                        "on the eyes. Geometry and verification are identical; only the tone "
                        "instruction to the shop, the preview, and the microscope view change."
                    ),
                },
            ],
            "min_gutter_um": MIN_GUTTER_UM,
        },
        "assumptions": [
            "The writer can hold the chosen minimum line and gap (1.0 µm by default).",
            "The reader optics are as entered (100×, NA 0.25, 580 nm by default).",
            "The mask shop accepts GDS layer 1/0 and the chosen data tone.",
        ],
    }


def settings_from_profiles(profiles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Best-effort inverse of profiles_from_settings, for runs made before
    settings.json existed: recover the page's settings from the profile YAMLs
    the run was built with. Fields the profiles cannot express keep their
    defaults; the result is normalized like any other settings dict."""
    fab = profiles.get("fab") or {}
    reader = profiles.get("reader") or {}
    content = profiles.get("content") or {}
    layout = profiles.get("layout") or {}
    raw: dict[str, Any] = dict(DEFAULT_SETTINGS)

    width, height = fab.get("plate_width_mm"), fab.get("plate_height_mm")
    if width is not None and height is not None:
        match = next(
            (
                key
                for key, size in PLATE_SIZES.items()
                if size["width_mm"] is not None
                and abs(size["width_mm"] - float(width)) < 1e-6
                and abs(size["height_mm"] - float(height)) < 1e-6
            ),
            "custom",
        )
        raw.update(plate_size=match, plate_width_mm=float(width), plate_height_mm=float(height))
    for key in ("edge_exclusion_mm", "min_feature_um"):
        if fab.get(key) is not None:
            raw[key] = fab[key]

    if content.get("dpi") is not None:
        raw["dpi"] = content["dpi"]
    if content.get("image_mode") == "dither":
        raw["content_kind"] = "photos"
    elif content.get("threshold") == "otsu":
        raw["content_kind"] = "scan"
    else:
        raw["content_kind"] = "digital"
    if content.get("expected_min_text_pt") is not None:
        raw["expected_min_text_pt"] = content["expected_min_text_pt"]

    page_w = layout.get("pseudopage_width_um")
    page_h = layout.get("pseudopage_height_um")
    if page_w is not None and page_h is not None:
        raw.update(pseudopage_width_um=float(page_w), pseudopage_height_um=float(page_h))
        for axis, key in (("x", "pitch_x_um"), ("y", "pitch_y_um")):
            pitch = layout.get(key)
            if pitch is None:
                continue
            gap = float(pitch) - float(page_w if axis == "x" else page_h)
            # page + safety gap means "no gutter"; anything wider is a real gutter
            raw[f"gutter_{axis}_um"] = 0.0 if gap <= MIN_GUTTER_UM + 1e-9 else round(gap, 3)
    if layout.get("title_text") is not None:
        raw["title_text"] = layout["title_text"]
    if layout.get("title_height_um") is not None:
        raw["title_height_mm"] = float(layout["title_height_um"]) / 1000.0
    if layout.get("title_band_um") is not None:
        raw["title_band_mm"] = float(layout["title_band_um"]) / 1000.0
    if layout.get("title_align") in ALIGNMENTS:
        raw["title_align"] = layout["title_align"]
    nav_um = layout.get("nav_band_um")
    if nav_um is not None:
        raw["nav_band"] = float(nav_um) > 0
        if float(nav_um) > 0:
            raw["nav_band_mm"] = float(nav_um) / 1000.0
    if layout.get("nav_text") is not None:
        raw["nav_text"] = layout["nav_text"]
    if layout.get("nav_text_height_um") is not None:
        raw["nav_text_height_um"] = layout["nav_text_height_um"]
    if layout.get("nav_align") in ALIGNMENTS:
        raw["nav_align"] = layout["nav_align"]
    if layout.get("polarity") in POLARITIES:
        raw["polarity"] = layout["polarity"]
    if layout.get("mirrored") is not None:
        raw["mirrored"] = bool(layout["mirrored"])
    scales = layout.get("tier_scales")
    if scales:
        wanted = [float(s) for s in scales]
        raw["tiers"] = next(
            (key for key, option in TIER_OPTIONS.items() if option == wanted), "1"
        )

    for key in ("magnification", "numerical_aperture", "wavelength_nm", "contrast_criterion"):
        if reader.get(key) is not None:
            raw[key] = reader[key]
    return normalized_settings(raw)
