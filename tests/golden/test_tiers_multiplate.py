"""M4: tier ladder, navigation band, multi-plate sets, readability CLI."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from stele.build import build_job
from stele.cli import main as cli_main
from tests.conftest import write_job

# Native geometry and verification make these integration tests a nightly tier;
# explicit RSS regression budgets live in test_build_golden.py.
pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def text_pdf(tmp_path_factory):
    import synthetic

    tmp = tmp_path_factory.mktemp("m4")
    return synthetic.text_page(str(tmp / "text.pdf"))


def test_tier_ladder_places_both_scales(tmp_path, profiles_dir, text_pdf):
    job = write_job(
        tmp_path, profiles_dir, text_pdf,
        layout_overrides={"tier_scales": [4.0, 1.0]},
    )
    report = build_job(job, verify=True, preview=False)
    tiers = {p["tier_scale"] for p in report["placements"]}
    assert tiers == {4.0, 1.0}
    # the 4x placement spans a 4x4 slot block
    big = next(p for p in report["placements"] if p["tier_scale"] == 4.0)
    x0, y0, x1, y1 = big["slot_um"]
    assert (x1 - x0) >= 4 * 2100 - 1
    # both tiers content-verify (each is its own materialization)
    assert report["verify"]["gates"]["content_pass"], report["verify"]["gates"]
    assert len(report["verify"]["pages"]) == 2


def test_tier_contrast_ordering(tmp_path, profiles_dir, text_pdf):
    """The readability sim must show the 4x tier MORE legible than 1x."""
    import cv2
    import numpy as np

    from stele.config.manifest import load_manifest
    from stele.verify.readability import airy_psf, stroke_contrast
    from stele.verify.renderback import open_layout, rasterize_cell_hierarchical

    job = write_job(
        tmp_path, profiles_dir, text_pdf,
        layout_overrides={"tier_scales": [4.0, 1.0]},
    )
    report = build_job(job, verify=False, preview=False)
    manifest, profiles = load_manifest(job)
    layout = open_layout(report["gds"]["path"])
    um_per_px = 0.1
    contrasts = {}
    for p in report["placements"]:
        cell = layout.cell(p["cell"])
        b = cell.dbbox()
        w, h = 400.0, 300.0
        x0 = (b.left + b.right) / 2 - w / 2
        y0 = (b.bottom + b.top) / 2 - h / 2
        ink = rasterize_cell_hierarchical(
            layout, p["cell"], (x0, y0, x0 + w, y0 + h), 1.0 / um_per_px, 1, 0
        )
        transmission = 1.0 - (ink > 0).astype(np.float32)
        psf = airy_psf(0.25, 0.58, um_per_px).astype(np.float32)
        intensity = cv2.filter2D(transmission, -1, psf)
        c = stroke_contrast(ink, intensity, um_per_px)
        contrasts[p["tier_scale"]] = c["min_stroke_contrast"]
    assert contrasts[4.0] is not None and contrasts[1.0] is not None
    assert contrasts[4.0] > contrasts[1.0], contrasts


def test_multi_plate_set(tmp_path, profiles_dir):
    """More pages than one small plate holds -> a plate set with identity."""
    import synthetic

    n_pages = 90  # a 30 mm plate holds ~60 usable slots -> 2 plates
    pdf = synthetic.stress_corpus(str(tmp_path / "many.pdf"), n_pages)
    job = write_job(
        tmp_path, profiles_dir, pdf,
        fab_overrides={"plate_width_mm": 30.0, "plate_height_mm": 30.0},
        layout_overrides={"title_band_um": 3000.0, "title_height_um": 1800.0},
    )
    report = build_job(job, verify=False, preview=False)
    assert report["plate_set"]["plates"] > 1
    paths = report["plate_set"]["gds"]
    assert all(os.path.exists(p) for p in paths)
    assert ".p01" in paths[0] and ".p02" in paths[1]
    total = sum(len(p["placements"]) for p in report["plates"])
    assert total == n_pages
    # reading order continuity across plates
    offsets = [p["plan"]["page_offset"] for p in report["plates"]]
    assert offsets == sorted(offsets)
    for p in report["plates"]:
        assert p["plan"]["plate_count"] == report["plate_set"]["plates"]


def test_nav_band_furniture_verifies(tmp_path, profiles_dir, text_pdf):
    job = write_job(
        tmp_path, profiles_dir, text_pdf,
        layout_overrides={"nav_band_um": 4000.0},
    )
    report = build_job(job, verify=True, preview=False)
    assert "nav_band" in report["plan"]["reserved"]
    occ = report["verify"]["drc_plate"]["occupancy"]
    assert occ["pass"], occ  # page map + scale bar stay inside the reserved band
    assert report["verify"]["gates"]["content_pass"]


def test_simulate_cli(tmp_path, profiles_dir, text_pdf, capsys):
    job = write_job(tmp_path, profiles_dir, text_pdf)
    build_job(job, verify=False, preview=False)
    rc = cli_main(["simulate", job])
    assert rc == 0
    outp = capsys.readouterr().out
    assert "simulated view" in outp
    assert "legible stroke bins" in outp
    # the sim PNG exists
    png = [line for line in outp.splitlines() if line.endswith(".sim.png")]
    assert png and os.path.exists(png[0].split(": ")[-1])
    data = json.loads(outp[outp.index("{") : outp.rindex("}") + 1])
    assert data["bins"]
