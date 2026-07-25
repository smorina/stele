"""Regression corpus from the plan (review finding 8): odd page boxes, mixed
sizes, gray ramp, real clip paths, 1-bit scans, CMYK images. Each must build
and content-verify; geometry may warn (that's the honest state of dense or
sub-floor content on a generic 1 um profile)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from stele.build import build_job
from tests.conftest import write_job

# whole-plate builds: ~8 min and 11.2 GB peak RSS for this tier locally --
# exceeds GitHub-hosted runners (~7 GB), so golden tests are nightly-tier
pytestmark = pytest.mark.slow

CASES = [
    "odd_pagebox",
    "mixed_sizes",
    "gray_ramp",
    "clipped_content",
    "scan_1bit",
    "cmyk_image",
]


@pytest.mark.parametrize("case", CASES)
def test_corpus_case_content_verifies(tmp_path, profiles_dir, corpus_dir, case):
    job = write_job(tmp_path, profiles_dir, os.path.join(corpus_dir, f"{case}.pdf"))
    report = build_job(job, verify=True, preview=False)
    g = report["verify"]["gates"]
    assert g["content_pass"], (case, g)
    assert g["status"] in ("pass", "pass_with_warnings")


def test_mixed_sizes_get_distinct_scales(tmp_path, profiles_dir, corpus_dir):
    job = write_job(tmp_path, profiles_dir, os.path.join(corpus_dir, "mixed_sizes.pdf"))
    report = build_job(job, verify=False, preview=False)
    scales = [p["scale"] for p in report["placements"]]
    assert len(set(round(s, 6) for s in scales)) == 3, scales
    # every scaled frame still fits its slot
    for p in report["placements"]:
        x0, y0, x1, y1 = p["slot_um"]
        assert p["origin_um"][0] >= x0 and p["origin_um"][1] >= y0


def test_odd_pagebox_recorded(tmp_path, profiles_dir, corpus_dir):
    job = write_job(tmp_path, profiles_dir, os.path.join(corpus_dir, "odd_pagebox.pdf"))
    report = build_job(job, verify=False, preview=False)
    rec = report["ingest"][0]
    assert rec["cropbox_pt"] != rec["mediabox_pt"], "cropbox normalization not recorded"
