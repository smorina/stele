"""Dark-field polarity: same geometry, flipped tone instruction, preview and
simulation as viewed, readability gate unchanged."""

from __future__ import annotations

import os
import sys

import fitz
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from stele.build import build_job
from stele.config.profiles import ReaderProfile
from stele.verify.readability import DARK_FIELD_GATE_NOTE, contrast_gate, simulate_view
from tests.conftest import write_job


def _pdf(path: str) -> str:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "DARK FIELD PLATE", fontsize=36)
    page.draw_rect(fitz.Rect(72, 200, 400, 260), color=(0, 0, 0), fill=(0, 0, 0))
    doc.save(path)
    doc.close()
    return path


def test_simulation_polarity_is_the_complement_and_gate_is_identical():
    ink = np.zeros((300, 300), dtype=np.uint8)
    ink[100:120, :] = 255  # a 2 um stroke at 0.1 um/px
    reader = ReaderProfile(name="t")
    clear, eye = simulate_view(ink, 0.1, reader, polarity="clear_field")
    dark, eye2 = simulate_view(ink, 0.1, reader, polarity="dark_field")
    assert eye == eye2
    np.testing.assert_allclose(clear + dark, 1.0, atol=1e-4)
    g_clear = contrast_gate(ink, 0.1, reader)
    g_dark = contrast_gate(ink, 0.1, reader, polarity="dark_field")
    assert g_clear["bins"] == g_dark["bins"]
    assert g_dark["polarity"] == "dark_field"
    assert g_dark["note"] == DARK_FIELD_GATE_NOTE
    assert "polarity" in g_clear and "note" not in g_clear


def test_dark_field_build_writes_identical_geometry_and_viewed_preview(tmp_path, profiles_dir):
    pdf = _pdf(str(tmp_path / "df.pdf"))
    (tmp_path / "clear").mkdir()
    (tmp_path / "dark").mkdir()
    clear_job = write_job(
        tmp_path / "clear", profiles_dir, pdf, gds_name="clear.gds",
        content_overrides={"dpi": 300},
    )
    dark_job = write_job(
        tmp_path / "dark", profiles_dir, pdf, gds_name="dark.gds",
        content_overrides={"dpi": 300},
        layout_overrides={"polarity": "dark_field", "nav_band_um": 5000.0,
                          "nav_text": "Bright text in chrome"},
    )
    clear = build_job(clear_job, verify=False, preview=True)
    dark = build_job(dark_job, verify=False, preview=True)

    assert clear["gds"]["polygons"] == dark["gds"]["polygons"]
    assert clear["gds"]["vertices"] == dark["gds"]["vertices"]
    assert dark["orientation"]["polarity"] == "dark_field"
    assert "chrome removed" in dark["orientation"]["tone_truth_table"][1]
    assert "clear aperture" in dark["orientation"]["fab_tone_assumption"]
    assert "chrome retained" in clear["orientation"]["fab_tone_assumption"]
    assert dark["plate_set"]["status"] == "unverified"

    clear_png = np.asarray(Image.open(clear["preview"]).convert("L"))
    dark_png = np.asarray(Image.open(dark["preview"]).convert("L"))
    assert clear_png.mean() > 200  # dark features on a bright field
    assert dark_png.mean() < 60  # bright features in a dark field
    # the two previews are complements of each other (same geometry)
    assert np.mean((255 - clear_png) == dark_png) > 0.99

    furniture = dark["furniture"]
    assert furniture["polarity"] == "dark_field"
    assert furniture["title"]["align"] == "center"
    assert not furniture["title"]["shrunk_to_fit"]
    nav = furniture["nav_band"]
    assert any(line["text"] == "Bright text in chrome" for line in nav["lines"])
    assert nav["scale_bar"] is not None
    assert "text_areas" in dark["plan"] and "nav_band" in dark["plan"]["text_areas"]
    assert dark["ingest"][0]["fonts_not_embedded"] == ["Helvetica"]
