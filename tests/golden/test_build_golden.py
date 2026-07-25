"""End-to-end build on the synthetic corpus: gates, determinism, mirroring."""

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from stele.build import build_job
from tests.conftest import write_job

# whole-plate builds: ~8 min and 11.2 GB peak RSS for this tier locally --
# exceeds GitHub-hosted runners (~7 GB), so golden tests are nightly-tier
pytestmark = pytest.mark.slow


def _sha(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_mixed_page_gates_pass(tmp_path, profiles_dir, corpus_dir):
    job = write_job(tmp_path, profiles_dir, os.path.join(corpus_dir, "mixed_page.pdf"))
    report = build_job(job, verify=True, preview=False)
    g = report["verify"]["gates"]
    assert g["content_pass"], g
    assert g["status"] in ("pass", "pass_with_warnings"), g
    assert report["plan"]["usable_slots"] == 3723


def test_build_is_deterministic(tmp_path, profiles_dir, corpus_dir):
    pdf = os.path.join(corpus_dir, "checkerboard.pdf")
    job1 = write_job(tmp_path, profiles_dir, pdf, gds_name="a.gds")
    job2 = write_job(tmp_path, profiles_dir, pdf, gds_name="b.gds")
    r1 = build_job(job1, verify=False, preview=False)
    r2 = build_job(job2, verify=False, preview=False)
    assert _sha(r1["gds"]["path"]) == _sha(r2["gds"]["path"])


def test_pathological_page_builds(tmp_path, profiles_dir, corpus_dir):
    """Rotated page, even-odd star, zero-width strokes: must not crash, must verify."""
    job = write_job(tmp_path, profiles_dir, os.path.join(corpus_dir, "pathological.pdf"))
    report = build_job(job, verify=True, preview=False)
    assert report["verify"]["gates"]["structural_defects"] == 0
    # rotation was applied and recorded
    assert report["ingest"][0]["rotation_deg"] == 90


def test_mirrored_build_verifies(tmp_path, profiles_dir, corpus_dir):
    job = write_job(
        tmp_path,
        profiles_dir,
        os.path.join(corpus_dir, "text_page.pdf"),
        layout_overrides={"mirrored": True},
    )
    report = build_job(job, verify=True, preview=False)
    assert report["verify"]["gates"]["chirality"]
    assert report["verify"]["gates"]["content_pass"]
    assert report["orientation"]["mirrored"] is True


def test_font_ladder_stroke_report(tmp_path, profiles_dir, corpus_dir):
    """The ladder page must produce a stroke-width distribution reaching below
    the writer floor (4 pt exists) — the measured gate must notice."""
    job = write_job(tmp_path, profiles_dir, os.path.join(corpus_dir, "font_ladder.pdf"))
    report = build_job(job, verify=True, preview=False)
    page = report["verify"]["pages"]["PAGE_0000"]
    assert page["stroke_widths_um"]["min_um"] < 1.0  # 4-6pt strokes measured on plate
    assert page["stroke_widths_um"]["p95_um"] > 3.0  # large text present
    assert page["structural_defects"] == 0


def test_report_provenance_complete(tmp_path, profiles_dir, corpus_dir):
    job = write_job(tmp_path, profiles_dir, os.path.join(corpus_dir, "text_page.pdf"))
    report = build_job(job, verify=False, preview=False)
    assert report["ingest"][0]["sha256"]
    assert report["config"]["profiles"]["fab"]["name"]
    assert report["dependencies"]["gdstk"] != "unknown"
    assert len(report["orientation"]["tone_truth_table"]) == 4
    with open(report["gds"]["path"] + ".report.json") as f:
        assert "stele_version" in f.read()


@pytest.mark.slow
def test_stress_plate_budgets(tmp_path, profiles_dir):
    """120 unique dense pages with EXECUTABLE resource budgets (review
    finding 7). Runs in a SUBPROCESS: ru_maxrss is a process-wide high-water
    mark, so measuring in the pytest process would count every earlier test's
    memory. The full-plate (~3,700 page) <30 min / <4 GB budget is an M6
    acceptance criterion; this asserts the pro-rated envelope."""
    import json
    import subprocess

    import synthetic

    pdf = synthetic.stress_corpus(str(tmp_path / "stress.pdf"), 120)
    job = write_job(tmp_path, profiles_dir, pdf)
    script = (
        "import json, resource, sys, time\n"
        "from stele.build import build_job\n"
        "t0 = time.time()\n"
        f"report = build_job({str(job)!r}, verify=False, preview=False)\n"
        "peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
        "peak_gb = peak / 1e9 if sys.platform == 'darwin' else peak / 1e6\n"
        "print(json.dumps({'elapsed': time.time() - t0, 'peak_gb': peak_gb,\n"
        "                  'placed': report['plan']['placed_pages'],\n"
        "                  'size_mb': report['gds']['size_mb']}))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=600
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    m = json.loads(proc.stdout.strip().splitlines()[-1])
    assert m["placed"] == 120
    assert m["elapsed"] < 240, f"120-page build took {m['elapsed']:.0f}s (budget 240s)"
    assert m["size_mb"] < 400, m["size_mb"]
    assert m["peak_gb"] < 4.0, f"peak RSS {m['peak_gb']:.2f} GB (budget 4 GB)"
