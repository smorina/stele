"""Planted-defect calibration (review findings 2-4): the verification gates
are trusted only because each defect class planted into a REAL built GDS is
caught while the clean build passes.

Defect classes: single dropped glyph polygon, dropped region, phantom blob,
shifted text, a MISSING ~1 um stroke at the writer floor, a severely thinned
stroke, and a reserved-region intrusion. Heatmap artifacts must be produced.
"""

import os
import sys

import gdstk
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from stele.build import build_job
from tests.conftest import write_job

# Native geometry and verification make these integration tests a nightly tier;
# explicit RSS regression budgets live in test_build_golden.py.
pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def built(tmp_path_factory, profiles_dir):
    """One clean single-page text build shared by the text-defect tests."""
    import synthetic

    tmp = tmp_path_factory.mktemp("planted")
    pdf = synthetic.text_page(str(tmp / "text.pdf"))
    job = write_job(tmp, profiles_dir, pdf, gds_name="clean.gds")
    report = build_job(job, verify=True, preview=False)
    assert report["verify"]["gates"]["content_pass"], "clean build must pass before planting"
    assert report["gds"]["polygons"] > 500, "page rendered empty — vacuous pass"
    return tmp, job, report


@pytest.fixture(scope="module")
def built_wedge(tmp_path_factory, profiles_dir):
    """Stroke-wedge build: horizontal rules from 8 pt down to 0.05 pt, giving
    known strokes at and below the writer floor."""
    import synthetic

    tmp = tmp_path_factory.mktemp("wedge")
    pdf = synthetic.stroke_wedge(str(tmp / "wedge.pdf"))
    job = write_job(tmp, profiles_dir, pdf, gds_name="wedge.gds")
    report = build_job(job, verify=True, preview=False)
    assert report["verify"]["gates"]["content_pass"]
    return tmp, job, report


def _mutate_gds(src: str, dst: str, mutate) -> None:
    lib = gdstk.read_gds(src)
    cells = {c.name: c for c in lib.cells}
    mutate(cells)
    lib.write_gds(dst)


def _reverify(job_path: str, gds_path: str, heatmaps: bool = True) -> dict:
    from stele.build import page_cell_assignments, verify_plate
    from stele.config.manifest import load_manifest
    from stele.ingest.normalize import collect_pages
    from stele.ir.model import Orientation
    from stele.layout.engine import plan_plate

    manifest, profiles = load_manifest(job_path)
    manifest.output.gds = gds_path
    jobs = collect_pages(manifest.inputs)
    plan = plan_plate(profiles.fab, profiles.layout, jobs)
    return verify_plate(
        manifest, profiles, plan, Orientation(), page_cell_assignments(plan),
        heatmap_dir=(gds_path + ".defects") if heatmaps else None,
    )


def _window_polys(page, x_range, y_range):
    out = []
    for p in page.polygons:
        (x0, y0), _ = p.bounding_box()
        if x_range[0] < x0 < x_range[1] and y_range[0] < y0 < y_range[1]:
            out.append(p)
    return out


def test_single_dropped_polygon_detected(built):
    """One glyph piece — the finest-grained dropout."""
    tmp, job, report = built
    bad = str(tmp / "dropped_one.gds")

    def drop(cells):
        page = cells["PAGE_0000"]
        hits = _window_polys(page, (400, 1400), (1000, 2000))
        assert hits, "no polygon found in probe window"
        biggest = max(hits, key=lambda p: p.area())
        assert biggest.area() > 30.0, f"probe polygon too small: {biggest.area()}"
        page.remove(biggest)

    _mutate_gds(report["gds"]["path"], bad, drop)
    v = _reverify(job, bad)
    assert v["gates"]["structural_defects"] >= 1
    assert v["gates"]["status"] == "fail"


def test_dropped_region_detected(built):
    tmp, job, report = built
    bad = str(tmp / "dropped_region.gds")

    def drop(cells):
        page = cells["PAGE_0000"]
        hits = _window_polys(page, (400, 800), (1000, 1600))
        assert len(hits) >= 3
        page.remove(*hits)

    _mutate_gds(report["gds"]["path"], bad, drop)
    v = _reverify(job, bad)
    assert v["gates"]["structural_defects"] >= 1


def test_phantom_blob_detected(built):
    tmp, job, report = built
    bad = str(tmp / "phantom.gds")

    def add_blob(cells):
        cells["PAGE_0000"].add(gdstk.rectangle((900.0, 30.0), (905.0, 35.0), layer=1))

    _mutate_gds(report["gds"]["path"], bad, add_blob)
    v = _reverify(job, bad)
    assert v["gates"]["structural_defects"] >= 1


def test_shifted_text_detected(built):
    tmp, job, report = built
    bad = str(tmp / "shifted.gds")

    def shift(cells):
        page = cells["PAGE_0000"]
        for p in _window_polys(page, (400, 800), (1000, 1600)):
            p.translate(8.0, 0.0)

    _mutate_gds(report["gds"]["path"], bad, shift)
    v = _reverify(job, bad)
    assert v["gates"]["structural_defects"] >= 1


def test_missing_writer_floor_stroke_detected(built_wedge):
    """Checkpoint criterion: deleting a ~1 um-wide rule must be detected.
    The 0.35 pt rule is ~1.1 um on plate."""
    tmp, job, report = built_wedge
    bad = str(tmp / "missing_stroke.gds")

    def drop_thin_rule(cells):
        page = cells["PAGE_0000"]
        # rules thinner than 2 um plate-scale, longer than 500 um: the wedge tail
        thin = [
            p for p in page.polygons
            if (p.bounding_box()[1][0] - p.bounding_box()[0][0]) > 500.0
            and (p.bounding_box()[1][1] - p.bounding_box()[0][1]) < 2.0
        ]
        assert thin, "no writer-floor rule found to delete"
        page.remove(*thin[:1])

    _mutate_gds(report["gds"]["path"], bad, drop_thin_rule)
    v = _reverify(job, bad)
    assert v["gates"]["structural_defects"] >= 1, "missing 1 um stroke must be detected"
    assert v["gates"]["status"] == "fail"


def test_thinned_stroke_detected(built_wedge):
    """Thin the 8 pt rule (~26 um plate) to ~1 um: the lost rim is a defect."""
    tmp, job, report = built_wedge
    bad = str(tmp / "thinned.gds")

    def thin(cells):
        page = cells["PAGE_0000"]
        thick = [
            p for p in page.polygons
            if (p.bounding_box()[1][0] - p.bounding_box()[0][0]) > 500.0
            and (p.bounding_box()[1][1] - p.bounding_box()[0][1]) > 20.0
        ]
        assert thick, "no thick rule found"
        target = thick[0]
        (x0, y0), (x1, y1) = target.bounding_box()
        cy = (y0 + y1) / 2
        page.remove(target)
        page.add(gdstk.rectangle((x0, cy - 0.5), (x1, cy + 0.5), layer=1))

    _mutate_gds(report["gds"]["path"], bad, thin)
    v = _reverify(job, bad)
    assert v["gates"]["structural_defects"] >= 1, "severe thinning must be detected"


def test_reserved_region_intrusion_detected(built):
    """Review probe: a polygon planted inside the reserved title band passed
    the old occupancy check. Direct CONTENT shapes now have no legitimate home."""
    tmp, job, report = built
    bad = str(tmp / "intrusion.gds")

    def intrude(cells):
        # title band spans the top of the active area (y ~ 145400..149400 um)
        cells["CONTENT"].add(gdstk.rectangle((70000.0, 146000.0), (72000.0, 148000.0), layer=1))

    _mutate_gds(report["gds"]["path"], bad, intrude)
    v = _reverify(job, bad)
    assert v["drc_plate"]["occupancy"]["direct_shapes"] >= 1
    assert not v["drc_plate"]["occupancy"]["pass"]
    assert v["gates"]["status"] == "fail", "reserved intrusion must fail unconditionally"


def test_heatmaps_written_on_failure(built):
    tmp, job, report = built
    bad = str(tmp / "for_heatmap.gds")

    def drop(cells):
        page = cells["PAGE_0000"]
        hits = _window_polys(page, (400, 800), (1000, 1600))
        page.remove(*hits)

    _mutate_gds(report["gds"]["path"], bad, drop)
    v = _reverify(job, bad, heatmaps=True)
    assert v["heatmaps"], "failing pages must produce heatmap artifacts"
    assert os.path.exists(v["heatmaps"][0])


def test_clean_build_passes(built):
    _, _, report = built
    g = report["verify"]["gates"]
    assert g["content_pass"]
    assert g["status"] in ("pass", "pass_with_warnings")
    assert g["worst_defect_mismatch"] <= 0.02
