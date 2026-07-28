"""M5: RGB triad color separation (projector route, patent FIG. 14)."""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from stele.build import build_job
from tests.conftest import write_job

# Native geometry and verification make these integration tests a nightly tier;
# explicit RSS regression budgets live in test_build_golden.py.
pytestmark = pytest.mark.slow

TRIAD = {"image_mode": "dither", "tag_images": True, "color_mode": "rgb_triad"}


@pytest.fixture(scope="module")
def color_pdf(tmp_path_factory):
    """A saturated color test image: channel content differs strongly."""
    import io

    import fitz
    from PIL import Image

    tmp = tmp_path_factory.mktemp("color")
    yy, xx = np.mgrid[0:600, 0:450]
    r = (255 * (xx / 450)).astype(np.uint8)
    g = (255 * (yy / 600)).astype(np.uint8)
    b = np.full_like(r, 60)
    disk = np.hypot(yy - 300, xx - 225) < 120
    r[disk], g[disk], b[disk] = 220, 40, 160
    img = Image.merge("RGB", [Image.fromarray(a) for a in (r, g, b)])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(fitz.Rect(106, 96, 506, 696), stream=buf.getvalue())
    path = str(tmp / "color.pdf")
    doc.set_metadata({})
    doc.save(path)
    doc.close()
    return path


def test_triad_channels_tone_verify(tmp_path, profiles_dir, color_pdf):
    job = write_job(tmp_path, profiles_dir, color_pdf, content_overrides=TRIAD)
    report = build_job(job, verify=True, preview=False)
    page = report["verify"]["pages"]["PAGE_0000"]
    tone = page["image_tone"]
    assert set(tone["channels"]) == {"R", "G", "B"}
    for ch, m in tone["channels"].items():
        assert m["pass"], (ch, m)
    assert report["verify"]["gates"]["content_pass"], report["verify"]["gates"]
    # triad provenance: three sub-images with identity transforms recorded
    triads = report["halftone"]["pages"]["PAGE_0000"]["triads"]
    assert [t["channel"] for t in triads] == ["R", "G", "B"]
    assert all(t["transform"]["scale"] == 1.0 for t in triads)


def test_triad_transform_offsets_recorded_and_applied(tmp_path, profiles_dir, color_pdf):
    xf = {"G": {"dx_um": 5000.0, "dy_um": 0.0, "scale": 1.0, "rotation_deg": 0.0}}
    job = write_job(
        tmp_path, profiles_dir, color_pdf,
        content_overrides={**TRIAD, "channel_transforms": xf},
    )
    report = build_job(job, verify=False, preview=False)
    triads = report["halftone"]["pages"]["PAGE_0000"]["triads"]
    g = next(t for t in triads if t["channel"] == "G")
    r = next(t for t in triads if t["channel"] == "R")
    assert g["transform"]["dx_um"] == 5000.0
    # the G sub-image landed 5 mm right of the R column (doc um)
    assert g["bbox_um"][0] - r["bbox_um"][0] == pytest.approx(5000.0, abs=1e-6)


def test_recombination_demo(tmp_path, profiles_dir, color_pdf):
    """Digital recombination of the three channel coverages approximates the
    source image — the projector-output demo artifact."""
    import cv2
    from PIL import Image

    from stele.config.manifest import load_manifest
    from stele.ingest.normalize import collect_pages
    from stele.layout.engine import plan_plates
    from stele.passes.colorsep import triad_boxes
    from stele.verify.renderback import open_layout, rasterize_cell_hierarchical

    job = write_job(tmp_path, profiles_dir, color_pdf, content_overrides=TRIAD)
    report = build_job(job, verify=False, preview=False)
    manifest, profiles = load_manifest(job)
    jobs = collect_pages(manifest.inputs)
    plan = plan_plates(profiles.fab, profiles.layout, jobs)[0]
    pl = plan.placements[0]
    layout = open_layout(report["gds"]["path"])
    w_um = pl.job.frame_pt[0] * 25400 / 72 * pl.scale
    h_um = pl.job.frame_pt[1] * 25400 / 72 * pl.scale
    cand = rasterize_cell_hierarchical(
        layout, "PAGE_0000", (0, 0, w_um, h_um), 2.0, 1, 0
    )
    # channel sub-rects in comparison px
    pt2um = 25400.0 / 72.0
    page_h_um = pl.job.frame_pt[1] * pt2um
    px_per_doc_um = cand.shape[1] / (pl.job.frame_pt[0] * pt2um)
    rect_pt = (106.0, 96.0, 506.0, 696.0)
    bbox_doc = (rect_pt[0] * pt2um, page_h_um - rect_pt[3] * pt2um,
                rect_pt[2] * pt2um, page_h_um - rect_pt[1] * pt2um)
    chans = {}
    for ch, _t, bb in triad_boxes(bbox_doc):
        r0 = int((page_h_um - bb[3]) * px_per_doc_um)
        r1 = int((page_h_um - bb[1]) * px_per_doc_um)
        c0 = int(bb[0] * px_per_doc_um)
        c1 = int(bb[2] * px_per_doc_um)
        cov = (cand[r0:r1, c0:c1] > 0).astype(np.float32)
        k = 32
        cov = cv2.blur(cov, (k, k))  # local coverage ~= darkness
        chans[ch] = 1.0 - cov  # intensity
    h = min(c.shape[0] for c in chans.values())
    w = min(c.shape[1] for c in chans.values())
    rgb = np.stack([chans[c][:h, :w] for c in "RGB"], axis=-1)
    out = str(tmp_path / "recombined.png")
    Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8)).save(out)
    assert os.path.exists(out)
    # the pink disk must recombine pink-ish: R high, G low
    cy, cx = int(h * 0.5), int(w * 0.5)
    patch = rgb[cy - 20 : cy + 20, cx - 20 : cx + 20]
    assert patch[..., 0].mean() > 0.7  # R bright
    assert patch[..., 1].mean() < 0.35  # G dark
