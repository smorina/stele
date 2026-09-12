from __future__ import annotations

import http.client
import io
import json
import threading
from pathlib import Path

import fitz
import pytest

from stele.build import Cancelled, build_job
from stele.config.manifest import load_manifest
from stele.ui.runs import UploadStore, create_workspace
from stele.ui.server import _page_plan, create_server
from stele.ui.verdict import CANDIDATE_HANDOFF, summarize_report


def _pdf_bytes(pages: int = 2) -> bytes:
    doc = fitz.open()
    for number in range(1, pages + 1):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), f"Archive page {number}", fontsize=18)
    data = doc.tobytes()
    doc.close()
    return data


def _upload(store: UploadStore, pages: int = 2):
    data = _pdf_bytes(pages)
    return store.save(io.BytesIO(data), len(data), "records.pdf")


def test_upload_inspects_pdf_and_workspace_round_trips(tmp_path):
    store = UploadStore(tmp_path / "stele-home")
    document = _upload(store, pages=3)
    assert document.page_count == 3
    assert document.name == "records.pdf"

    workspace = create_workspace(
        store,
        [{"id": document.id, "pages": "1-2"}],
        "ARCHIVE-01",
        {"dpi": 600, "content_kind": "scan", "nav_band": True},
    )
    manifest, profiles = load_manifest(str(workspace.manifest))
    assert manifest.inputs[0].page_indices(3) == [0, 1]
    assert Path(manifest.inputs[0].path).is_file()
    assert profiles.content.dpi == 600
    assert profiles.content.threshold == "otsu"
    assert profiles.layout.nav_band_um > 0
    assert manifest.output.plate_name == "ARCHIVE-01"
    assert "-full-" in workspace.manifest.name
    assert workspace.manifest.name.endswith(".manifest.yaml")
    assert Path(manifest.output.gds).name.startswith(workspace.artifact_base)
    assert Path(manifest.output.gds).name.endswith(".gds")
    second_workspace = create_workspace(
        store,
        [{"id": document.id, "pages": "1-2"}],
        "ARCHIVE-01",
        {"dpi": 600, "content_kind": "scan", "nav_band": True},
    )
    assert second_workspace.manifest.name != workspace.manifest.name


def test_trial_workspace_uses_first_selected_page(tmp_path):
    store = UploadStore(tmp_path)
    document = _upload(store, pages=4)
    workspace = create_workspace(
        store,
        [{"id": document.id, "pages": "2-4"}],
        "TRIAL",
        None,
        trial=True,
    )
    manifest, _ = load_manifest(str(workspace.manifest))
    assert manifest.inputs[0].pages == "2"
    assert "-trial-" in workspace.manifest.name
    assert "-trial-" in Path(manifest.output.gds).name


def test_capacity_plan_uses_actual_selected_pages(tmp_path):
    store = UploadStore(tmp_path)
    document = _upload(store, pages=4)
    plan = _page_plan(
        store,
        {
            "documents": [{"id": document.id, "pages": "2-3"}],
            "settings": {"dpi": 600},
        },
    )
    assert plan["source_pages"] == 2
    assert plan["placements"] == 2
    assert plan["plates"] == 1
    assert plan["reduction"] > 100
    assert plan["pseudopage_mm"] == [1.98, 2.56]
    with pytest.raises(ValueError, match="check All pages"):
        _page_plan(
            store,
            {
                "documents": [{"id": document.id, "pages": ""}],
                "settings": {"dpi": 600},
            },
        )


def test_verdict_never_hides_warnings_or_unenforced_checks():
    report = {
        "plate_set": {"status": "pass_with_warnings", "plates": 1},
        "plates": [
            {
                "verify": {
                    "gates": {
                        "status": "pass_with_warnings",
                        "worst_defect_mismatch": 0,
                        "structural_defects": 0,
                        "component_failures": 0,
                        "placements_expected": 2,
                        "placements_found": 2,
                        "chirality": True,
                        "stroke_floor_failing_pages": ["PAGE_0001"],
                        "width_violations": 0,
                        "space_violations": 0,
                    }
                }
            }
        ],
        "validation": {"warnings": [], "unenforced_fields": ["mask exposure tone"]},
    }
    result = summarize_report(report)
    assert result["status"] == "pass_with_warnings"
    assert result["headline"] == "Built with warnings"
    assert any("thinner" in finding["text"] for finding in result["findings"])
    assert result["unenforced"] == ["mask exposure tone"]
    assert "prototype" in CANDIDATE_HANDOFF.lower()
    assert "not fab-ready" in CANDIDATE_HANDOFF


def test_failed_verdict_points_to_findings_without_mask_shop_command():
    result = summarize_report(
        {
            "plate_set": {"status": "fail", "plates": 0},
            "plates": [],
            "validation": {"warnings": [], "unenforced_fields": []},
        }
    )
    assert result["meaning"] == (
        "The output did not survive every required software check. Review the findings below."
    )
    assert "mask shop" not in result["meaning"]


def test_progress_can_cancel_before_any_build_work():
    with pytest.raises(Cancelled):
        build_job(
            "does-not-need-to-exist.yaml",
            progress=lambda stage, done, total, detail: "cancel",
        )


def test_local_server_auth_upload_and_capacity(tmp_path):
    server = create_server(home=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    connection = http.client.HTTPConnection(host, port, timeout=10)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        response.read()
        assert response.status == 403

        connection.close()
        connection = http.client.HTTPConnection(host, port, timeout=10)
        connection.request("GET", f"/?token={server.token}")
        response = connection.getresponse()
        html = response.read().decode()
        assert response.status == 200
        assert "Put a document" in html
        assert "Prototype" in html
        assert "All ${doc.page_count} pages" in html
        assert "single page numbers or inclusive ranges" in html
        assert "Why the “not checked” list is here" in html
        assert 'id="quit-button"' in html
        assert 'api("/api/quit"' in html
        assert 'id="cancel-button" type="button" hidden' in html
        assert "setCancelButtonVisible(true);" in html
        assert html.count("setCancelButtonVisible(false);") == 4
        assert "https://" not in html

        data = _pdf_bytes(2)
        connection.request(
            "POST",
            "/api/upload",
            body=data,
            headers={
                "Content-Type": "application/pdf",
                "Content-Length": str(len(data)),
                "X-Filename": "two-pages.pdf",
                "X-Stele-Token": server.token,
                "Origin": server.origin,
            },
        )
        response = connection.getresponse()
        uploaded = json.loads(response.read())
        assert response.status == 201
        assert uploaded["page_count"] == 2

        body = json.dumps(
            {"documents": [{"id": uploaded["id"], "pages": "2"}], "settings": {"dpi": 600}}
        )
        connection.request(
            "POST",
            "/api/calc",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "X-Stele-Token": server.token,
                "Origin": server.origin,
            },
        )
        response = connection.getresponse()
        plan = json.loads(response.read())
        assert response.status == 200
        assert plan["source_pages"] == 1

        connection.request(
            "POST",
            "/api/calc",
            body="{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": "2",
                "X-Stele-Token": server.token,
                "Origin": "https://attacker.example",
            },
        )
        response = connection.getresponse()
        response.read()
        assert response.status == 403
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_quit_refuses_active_build_then_stops_server(tmp_path):
    server = create_server(home=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    connection = http.client.HTTPConnection(host, port, timeout=10)
    headers = {
        "Content-Type": "application/json",
        "Content-Length": "2",
        "X-Stele-Token": server.token,
        "Origin": server.origin,
    }
    try:
        server.runs.has_active_run = lambda: True
        connection.request("POST", "/api/quit", body="{}", headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 409
        assert "still running" in payload["error"]
        assert thread.is_alive()

        server.runs.has_active_run = lambda: False
        connection.request("POST", "/api/quit", body="{}", headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload == {"status": "stopping"}
        thread.join(timeout=2)
        assert not thread.is_alive()
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# --- expanded settings surface, planning payload, history, microscope ---

from stele.ui.presets import (  # noqa: E402
    DEFAULT_SETTINGS,
    MIN_GUTTER_UM,
    PLATE_SIZES,
    normalized_settings,
    preset_payload,
    profiles_from_settings,
)
from stele.ui.runs import RunManager, simulation_defaults  # noqa: E402


def test_settings_defaults_round_trip_and_plate_presets():
    clean = normalized_settings(None)
    assert clean["plate_size"] == "6in" and clean["plate_width_mm"] == 152.4
    assert clean["gutter_x_um"] == 0.0 and clean["polarity"] == "clear_field"
    for key, size in PLATE_SIZES.items():
        if size["width_mm"] is None:
            continue
        settings = normalized_settings({"plate_size": key})
        assert settings["plate_width_mm"] == size["width_mm"]
    custom = normalized_settings({"plate_size": "custom", "plate_width_mm": 125, "plate_height_mm": 100})
    assert (custom["plate_width_mm"], custom["plate_height_mm"]) == (125.0, 100.0)
    # legacy keys from the first release still work
    legacy = normalized_settings({"dpi": 600, "content_kind": "scan", "min_feature_um": 2, "nav_band": False})
    assert legacy["dpi"] == 600 and legacy["nav_band"] is False
    for key in DEFAULT_SETTINGS:
        assert key in legacy


def test_settings_reject_out_of_range_values_with_plain_messages():
    with pytest.raises(ValueError, match="Unusable border"):
        normalized_settings({"edge_exclusion_mm": 30})
    with pytest.raises(ValueError, match="leaves no room"):
        normalized_settings({"plate_size": "custom", "plate_width_mm": 20, "plate_height_mm": 20, "edge_exclusion_mm": 10})
    with pytest.raises(ValueError, match="Plate tone"):
        normalized_settings({"polarity": "negative"})
    with pytest.raises(ValueError, match="Numerical aperture"):
        normalized_settings({"numerical_aperture": 2.0})
    with pytest.raises(ValueError, match="at most 6 lines"):
        normalized_settings({"nav_text": "\n".join("abcdefgh")})
    with pytest.raises(ValueError, match="must be a number"):
        normalized_settings({"magnification": "lots"})


def test_profiles_from_settings_map_every_group():
    profiles = profiles_from_settings(
        {
            "plate_size": "125mm", "edge_exclusion_mm": 4, "min_feature_um": 0.5,
            "pseudopage_width_um": 3000, "pseudopage_height_um": 4000, "gutter_x_um": 0, "gutter_y_um": 50,
            "title_text": "MY PLATE", "title_height_mm": 2, "title_band_mm": 3, "title_align": "left",
            "nav_band": True, "nav_band_mm": 6, "nav_text": "line one\nline two", "nav_align": "right",
            "polarity": "dark_field", "mirrored": True, "tiers": "4,1",
            "magnification": 200, "numerical_aperture": 0.4, "wavelength_nm": 550, "contrast_criterion": 0.3,
            "content_kind": "photos", "dpi": 600, "expected_min_text_pt": 6,
        }
    )
    assert profiles.fab.plate_width_mm == 125.0 and profiles.fab.edge_exclusion_mm == 4.0
    assert profiles.fab.min_space_um == 0.5
    layout = profiles.layout
    assert layout.pitch_x_um == 3000 + MIN_GUTTER_UM  # 0 gutter keeps the safety gap
    assert layout.pitch_y_um == 4050.0
    assert layout.title_text == "MY PLATE" and layout.title_height_um == 2000.0
    assert layout.title_band_um == 3000.0 and layout.title_align == "left"
    assert layout.nav_band_um == 6000.0 and layout.nav_text == "line one\nline two"
    assert layout.nav_align == "right" and layout.polarity == "dark_field" and layout.mirrored
    assert layout.tier_scales == [4.0, 1.0]
    assert (profiles.reader.magnification, profiles.reader.numerical_aperture) == (200.0, 0.4)
    assert profiles.reader.wavelength_nm == 550.0 and profiles.reader.contrast_criterion == 0.3
    assert profiles.content.image_mode == "dither" and profiles.content.expected_min_text_pt == 6.0
    payload = preset_payload()
    assert {p["id"] for p in payload["choices"]["polarities"]} == {"clear_field", "dark_field"}
    assert payload["choices"]["min_gutter_um"] == MIN_GUTTER_UM


def test_plan_payload_reports_layout_sketch_title_fit_and_fonts(tmp_path):
    store = UploadStore(tmp_path)
    document = _upload(store, pages=3)  # PyMuPDF's default Helvetica is not embedded
    assert document.fonts_not_embedded == ["Helvetica"]
    long_title = "A TITLE FAR TOO LONG FOR THE SPAN BETWEEN THE CORNER FIDUCIALS OF THE PLATE " * 2
    plan = _page_plan(
        store,
        {
            "documents": [{"id": document.id, "pages": "all"}],
            "settings": {"plate_size": "5in", "polarity": "dark_field", "title_text": long_title,
                         "nav_text": "Description"},
            "plate_name": "SKETCH",
        },
    )
    assert plan["plate"]["width_mm"] == 127.0 and plan["plate"]["polarity"] == "dark_field"
    assert plan["plate"]["gutter_um"] == [MIN_GUTTER_UM, MIN_GUTTER_UM]
    sketch = plan["sketch"]
    assert sketch["plate_um"] == [127000.0, 127000.0]
    assert set(sketch["reserved"]) >= {"title_band", "nav_band", "fiducial_sw", "orientation_glyph"}
    assert len(sketch["placements"]) == 3 and sketch["placements_total"] == 3
    assert sketch["title_rect_um"] and sketch["nav_block_um"]
    assert plan["title"]["shrunk_to_fit"] and plan["title"]["height_um"] < 2500.0
    assert any("too wide" in w for w in plan["warnings"])
    assert plan["fonts_not_embedded"] == {"records.pdf": ["Helvetica"]}
    assert any("not embedded" in w for w in plan["warnings"])
    optics = plan["optics"]
    assert optics["rayleigh_um"] == pytest.approx(0.61 * 0.58 / 0.25, abs=1e-3)
    assert optics["reader_floor_pt"] > optics["writer_floor_pt"] > 0
    assert "Description" in plan["nav"]["lines"]
    # a band that cannot hold its text is a plain-language error, not a crash
    with pytest.raises(ValueError, match="no room"):
        _page_plan(
            store,
            {
                "documents": [{"id": document.id, "pages": "1"}],
                "settings": {"plate_size": "custom", "plate_width_mm": 10, "plate_height_mm": 60,
                             "edge_exclusion_mm": 3, "title_band_mm": 3, "nav_band": False},
            },
        )


def test_run_manager_history_heatmaps_and_microscope(tmp_path):
    """A real 300-DPI trial through the run manager: heatmap artifacts are
    published, the completed run is discoverable from disk by a new manager,
    and the microscope endpoint re-renders the built GDS with other optics."""
    store = UploadStore(tmp_path / "home")
    document = _upload(store, pages=1)
    manager = RunManager(store)
    run = manager.start(
        [{"id": document.id, "pages": "all"}], "HISTORY",
        {"dpi": 300, "nav_text": "Guide text", "polarity": "dark_field"}, trial=True,
    )
    for _ in range(600):
        current = manager.get(run["id"])
        if current["status"] in {"complete", "error", "cancelled"}:
            break
        threading.Event().wait(0.2)
    assert current["status"] == "complete", current.get("diagnostic")
    assert current["settings"]["polarity"] == "dark_field"
    assert (tmp_path / "home" / "runs" / run["id"] / "config" / "settings.json").is_file()
    kinds = {a["kind"] for a in current["artifacts"]}
    assert {"config", "report", "gds", "preview"} <= kinds
    result = current["result"]
    assert result["polarity"] == "dark_field" and result["tone_truth_table"]
    if result["status"] == "fail":
        # PyMuPDF's un-embedded Helvetica differs between engines: the verdict
        # must say so and the heatmap must be downloadable
        assert "heatmap" in kinds
        assert any("not embedded" in f["text"] for f in result["findings"])
        heatmap = next(a for a in current["artifacts"] if a["kind"] == "heatmap")
        assert manager.artifact(run["id"], heatmap["id"]).is_file()
    sim = current["simulation"]
    assert sim["cells"][0]["cell"] == "PAGE_0000" and sim["default"]["cell"] == "PAGE_0000"
    assert sim["polarity"] == "dark_field"

    view = manager.simulate(run["id"], {"numerical_aperture": 0.65, "magnification": 400})
    assert view["reader"]["numerical_aperture"] == 0.65
    assert view["rayleigh_um"] == pytest.approx(0.61 * 0.58 / 0.65, abs=1e-3)
    assert view["png_base64"] and view["polarity"] == "dark_field"
    assert view["build_reader"]["numerical_aperture"] == 0.25
    with pytest.raises(ValueError, match="Numerical aperture"):
        manager.simulate(run["id"], {"numerical_aperture": 9})
    with pytest.raises(ValueError, match="not a page"):
        manager.simulate(run["id"], {"cell": "PAGE_0042"})

    # a fresh manager (new session) finds the run on disk with the same result
    later = RunManager(UploadStore(tmp_path / "home"))
    history = later.history()
    assert [h["id"] for h in history] == [run["id"]]
    reopened = later.get(run["id"])
    assert reopened["status"] == "complete"
    assert reopened["result"]["status"] == result["status"]
    assert {a["id"] for a in reopened["artifacts"]} == {a["id"] for a in current["artifacts"]}
    assert simulation_defaults(later._state(run["id"]).report)["default"]["cell"] == "PAGE_0000"


# --- reopening a previous run: documents, page choices, settings, plan ---

from stele.ui.planning import plan_from_report  # noqa: E402
from stele.ui.presets import settings_from_profiles  # noqa: E402


def test_settings_recover_from_profiles_for_pre_settings_runs():
    profiles = profiles_from_settings(
        {"plate_size": "5in", "edge_exclusion_mm": 4, "gutter_x_um": 0, "gutter_y_um": 250,
         "title_text": "OLD", "title_height_mm": 2, "title_band_mm": 3, "title_align": "left",
         "nav_band": True, "nav_band_mm": 6, "nav_text": "desc", "nav_align": "right",
         "polarity": "dark_field", "mirrored": True, "tiers": "4,1", "content_kind": "scan",
         "dpi": 600, "magnification": 200, "numerical_aperture": 0.4}
    )
    dumped = {kind: getattr(profiles, kind).model_dump() for kind in ("fab", "reader", "content", "layout")}
    recovered = settings_from_profiles(dumped)
    assert recovered["plate_size"] == "5in" and recovered["edge_exclusion_mm"] == 4.0
    assert recovered["gutter_x_um"] == 0.0 and recovered["gutter_y_um"] == 250.0
    assert recovered["title_text"] == "OLD" and recovered["title_align"] == "left"
    assert recovered["nav_band"] and recovered["nav_band_mm"] == 6.0 and recovered["nav_text"] == "desc"
    assert recovered["polarity"] == "dark_field" and recovered["mirrored"] and recovered["tiers"] == "4,1"
    assert recovered["content_kind"] == "scan" and recovered["dpi"] == 600
    assert (recovered["magnification"], recovered["numerical_aperture"]) == (200.0, 0.4)
    # a v0.2 layout with the old 120/40 um gutters reads back as real gutters
    legacy = settings_from_profiles(
        {"fab": {"plate_width_mm": 152.4, "plate_height_mm": 152.4},
         "layout": {"pseudopage_width_um": 1980.0, "pseudopage_height_um": 2560.0,
                    "pitch_x_um": 2100.0, "pitch_y_um": 2600.0, "nav_band_um": 5000.0},
         "content": {"threshold": "otsu", "image_mode": "dither"}}
    )
    assert legacy["plate_size"] == "6in" and legacy["gutter_x_um"] == 120.0
    assert legacy["gutter_y_um"] == 40.0 and legacy["content_kind"] == "photos"


def test_reopening_a_run_restores_documents_pages_settings_and_plan(tmp_path):
    store = UploadStore(tmp_path / "home")
    first = _upload(store, pages=4)
    data = _pdf_bytes(2)
    second = store.save(io.BytesIO(data), len(data), "appendix.pdf")
    manager = RunManager(store)
    run = manager.start(
        [{"id": first.id, "pages": "2-4"}, {"id": second.id, "pages": "all"}],
        "records", {"dpi": 300, "title_align": "right", "nav_text": "kept"}, trial=True,
    )
    for _ in range(600):
        current = manager.get(run["id"])
        if current["status"] in {"complete", "error", "cancelled"}:
            break
        threading.Event().wait(0.2)
    assert current["status"] == "complete", current.get("diagnostic")
    # a trial builds one page but keeps the whole selection with the run
    assert [d["name"] for d in current["selection"]["documents"]] == ["records.pdf", "appendix.pdf"]
    assert [d["pages"] for d in current["selection"]["documents"]] == ["2-4", "all"]
    run_dir = tmp_path / "home" / "runs" / run["id"]
    assert sorted(p.name for p in (run_dir / "input").iterdir()) == ["01-records.pdf", "02-appendix.pdf"]
    plan = current["plan"]
    assert plan["from_report"] and plan["source_pages"] == 1 and plan["plates"] == 1
    assert plan["plate"]["polarity"] == "clear_field" and plan["sketch"]["placements"]
    assert plan["title"]["align"] == "right" and plan["nav"]["lines"][-1] == "kept"
    assert plan["optics"]["rayleigh_um"] == pytest.approx(0.61 * 0.58 / 0.25, abs=1e-3)

    # a new session: restore adopts the run's copies and returns page choices
    later = RunManager(UploadStore(tmp_path / "home"))
    restored = later.restore(run["id"])
    assert restored["plate_name"] == "records" and restored["trial"] is True
    assert restored["settings"]["title_align"] == "right" and restored["settings"]["nav_text"] == "kept"
    assert [d["name"] for d in restored["documents"]] == ["records.pdf", "appendix.pdf"]
    assert [d["pages"] for d in restored["documents"]] == ["2-4", "all"]
    assert restored["documents"][0]["page_count"] == 4 and restored["missing"] == []
    # the adopted documents plan like fresh uploads, and adopting twice reuses them
    again = later.restore(run["id"])
    assert [d["id"] for d in again["documents"]] == [d["id"] for d in restored["documents"]]
    live = _page_plan(
        later.store,
        {"documents": [{"id": d["id"], "pages": d["pages"]} for d in restored["documents"]],
         "settings": restored["settings"], "plate_name": restored["plate_name"]},
    )
    assert live["source_pages"] == 5 and not live.get("from_report")

    # a run whose input files are gone reports them as missing, not as an error
    for path in (run_dir / "input").iterdir():
        path.unlink()
    gone = RunManager(UploadStore(tmp_path / "home")).restore(run["id"])
    assert gone["documents"] == [] and len(gone["missing"]) == 2


def test_plan_from_report_handles_reports_without_furniture():
    report = {
        "plates": [{"plan": {"usable_slots": 10, "theoretical_slots_pre_reservation": 12,
                             "plate_um": [1000.0, 1000.0], "active_um": [10.0, 10.0, 990.0, 990.0],
                             "reserved": {}},
                    "placements": [], "ingest": []}],
        "config": {"profiles": {"fab": {"plate_width_mm": 1.0, "plate_height_mm": 1.0,
                                        "edge_exclusion_mm": 0.01}, "layout": {}}},
        "validation": {"warnings": ["w"], "info": []},
    }
    plan = plan_from_report(report)
    assert plan["from_report"] and plan["reduction"] is None and plan["title"] is None
    assert plan["plate"]["size_id"] == "custom" and plan["warnings"] == ["w"]
    assert plan["optics"] is None  # the truncated config cannot build profiles
    assert plan_from_report({"plates": []}) is None
