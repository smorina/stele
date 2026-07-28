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
