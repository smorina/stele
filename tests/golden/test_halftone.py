"""M3: dithered image regions — tone fidelity, DRC-cleanliness, cell sharing."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from stele.build import build_job
from tests.conftest import write_job

# whole-plate builds: ~8 min and 11.2 GB peak RSS for this tier locally --
# exceeds GitHub-hosted runners (~7 GB), so golden tests are nightly-tier
pytestmark = pytest.mark.slow

DITHER = {"image_mode": "dither", "tag_images": True}


@pytest.fixture(scope="module")
def photo_pdf(tmp_path_factory):
    """A PDF with an embedded continuous-tone photo (gradient + shapes)."""
    import io

    import fitz
    import numpy as np
    from PIL import Image

    tmp = tmp_path_factory.mktemp("photo")
    yy, xx = np.mgrid[0:800, 0:600]
    g = 40 + 170 * (xx / 600)
    g[np.hypot(yy - 300, xx - 200) < 110] = 210
    g[np.hypot(yy - 520, xx - 380) < 80] = 70
    img = Image.fromarray(g.astype("uint8"), mode="L")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(fitz.Rect(80, 120, 530, 720), stream=buf.getvalue())
    path = str(tmp / "photo.pdf")
    doc.set_metadata({})
    doc.save(path)
    doc.close()
    return path


def test_dithered_photo_tone_and_gates(tmp_path, profiles_dir, photo_pdf):
    job = write_job(tmp_path, profiles_dir, photo_pdf, content_overrides=DITHER)
    report = build_job(job, verify=True, preview=False)
    g = report["verify"]["gates"]
    page = report["verify"]["pages"]["PAGE_0000"]
    assert report["halftone"]["tile_cells"] > 0
    assert page["image_tone"]["superpixels"] > 100
    assert page["image_tone"]["pass"], page["image_tone"]
    assert g["content_pass"], g
    # dither geometry itself is DRC-clean by construction: the image-bearing
    # page must not add width/space violations beyond an all-text page's own
    assert page["drc"]["width_violations"] == 0, page["drc"]
    assert page["drc"]["space_violations"] == 0, page["drc"]
    assert g["status"] == "pass"


def test_dither_report_provenance(tmp_path, profiles_dir, photo_pdf):
    job = write_job(tmp_path, profiles_dir, photo_pdf, content_overrides=DITHER)
    report = build_job(job, verify=False, preview=False)
    ht = report["halftone"]
    assert ht["mask"] == "bluenoise"
    assert ht["pitch_um"] == 1.0  # max(min_feature, min_space) of the generic profile
    assert len(ht["mask_asset_sha256"]) == 64
    assert ht["pages"]["PAGE_0000"]["sites"] > 100000


def test_gray_ramp_dithered(tmp_path, profiles_dir, corpus_dir):
    """The ramp exercises every tone level; must stay in tone budget."""
    job = write_job(
        tmp_path, profiles_dir, os.path.join(corpus_dir, "cmyk_image.pdf"),
        content_overrides=DITHER,
    )
    report = build_job(job, verify=True, preview=False)
    page = report["verify"]["pages"]["PAGE_0000"]
    assert page["image_tone"]["pass"], page["image_tone"]
    assert report["verify"]["gates"]["content_pass"]
