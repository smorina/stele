"""The deliberately small settings surface used by the first-run UI."""

from __future__ import annotations

from typing import Any

from stele.config.profiles import ContentPolicy, FabProfile, LayoutSpec, Profiles, ReaderProfile

DEFAULT_SETTINGS = {
    "dpi": 900,
    "content_kind": "digital",
    "min_feature_um": 1.0,
    "nav_band": True,
}


def normalized_settings(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = {**DEFAULT_SETTINGS, **(raw or {})}
    dpi = int(settings["dpi"])
    if dpi < 300 or dpi > 1200:
        raise ValueError("Scan detail must be between 300 and 1200 DPI")
    content_kind = str(settings["content_kind"])
    if content_kind not in {"digital", "scan", "photos"}:
        raise ValueError("Document type must be digital, scan, or photos")
    min_feature = float(settings["min_feature_um"])
    if not 0.25 <= min_feature <= 25:
        raise ValueError("Writer minimum line must be between 0.25 and 25 µm")
    return {
        "dpi": dpi,
        "content_kind": content_kind,
        "min_feature_um": min_feature,
        "nav_band": bool(settings["nav_band"]),
    }


def profiles_from_settings(raw: dict[str, Any] | None = None) -> Profiles:
    settings = normalized_settings(raw)
    kind = settings["content_kind"]
    return Profiles(
        fab=FabProfile(
            name="generic-laserwriter",
            min_feature_um=settings["min_feature_um"],
            min_space_um=settings["min_feature_um"],
            notes=(
                "Engineering defaults, not a confirmed vendor rule deck. "
                "Confirm these limits and the exposure tone with the mask shop."
            ),
        ),
        reader=ReaderProfile(
            name="na025-580nm",
            notes="Engineering-default microscope profile; confirm the actual reader optics.",
        ),
        content=ContentPolicy(
            name="ui-default",
            dpi=settings["dpi"],
            threshold="fixed" if kind == "digital" else "otsu",
            tag_images=kind == "photos",
            image_mode="dither" if kind == "photos" else "threshold",
        ),
        layout=LayoutSpec(
            name="pseudopage-6in",
            nav_band_um=5000.0 if settings["nav_band"] else 0.0,
        ),
    )


def preset_payload() -> dict[str, Any]:
    return {
        "id": "six-inch-generic",
        "name": "6-inch plate · standard microscope",
        "description": (
            "A 152.4 mm chrome-on-glass plate using unconfirmed, conservative "
            "engineering defaults for a 1 µm laser writer."
        ),
        "settings": DEFAULT_SETTINGS,
        "assumptions": [
            "The writer can hold 1.0 µm lines and gaps.",
            "The reader uses a 100×, NA 0.25 objective around 580 nm.",
            "The mask shop accepts GDS layer 1/0 and positive tone.",
        ],
    }
