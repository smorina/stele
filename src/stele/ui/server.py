"""Secure localhost HTTP server for the no-install-front-end UI."""

from __future__ import annotations

import json
import mimetypes
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any

from stele.config.manifest import InputSpec
from stele.config.validate import validate_profiles
from stele.doctor import run_doctor, write_doctor_json
from stele.ingest.normalize import PageJob
from stele.layout.engine import plan_plates
from stele.ui.presets import preset_payload, profiles_from_settings
from stele.ui.runs import RunManager, UploadStore

MAX_JSON_BYTES = 1024 * 1024


def _page_plan(store: UploadStore, body: dict[str, Any]) -> dict[str, Any]:
    selections = body.get("documents") or []
    if not selections:
        raise ValueError("Choose at least one PDF")
    jobs: list[PageJob] = []
    source_pages = 0
    ordinal = 0
    for item in selections:
        document = store.get(str(item.get("id", "")))
        pages = str(item.get("pages", "all")).strip()
        if not pages:
            raise ValueError(
                f"{document.name}: check All pages or enter page numbers such as 1-5, 8"
            )
        indices = InputSpec(path=str(document.path), pages=pages).page_indices(
            document.page_count
        )
        if not indices:
            raise ValueError(f"{document.name}: the page selection is empty")
        source_pages += len(indices)
        for index in indices:
            page = document.pages[index]
            jobs.append(
                PageJob(
                    pdf_path=str(document.path),
                    pdf_sha256=document.sha256,
                    page_index=index,
                    ordinal=ordinal,
                    mediabox_pt=tuple(page["mediabox_pt"]),
                    cropbox_pt=tuple(page["cropbox_pt"]),
                    rotation_deg=int(page["rotation_deg"]),
                    frame_pt=(float(page["width_pt"]), float(page["height_pt"])),
                )
            )
            ordinal += 1
    profiles = profiles_from_settings(body.get("settings"))
    validation = validate_profiles(profiles)
    if not validation.ok:
        raise ValueError("; ".join(validation.errors))
    plans = plan_plates(
        profiles.fab,
        profiles.layout,
        jobs,
        fit_mode=profiles.content.fit_mode,
    )
    first = plans[0]
    first_placement = first.placements[0] if first.placements else None
    page_size = None
    reduction = None
    if first_placement is not None:
        rect = first_placement.content_rect()
        page_size = [
            round((rect.x1 - rect.x0) / 1000, 2),
            round((rect.y1 - rect.y0) / 1000, 2),
        ]
        reduction = round(1.0 / first_placement.scale, 1)
    return {
        "source_pages": source_pages,
        "placements": sum(len(plan.placements) for plan in plans),
        "plates": len(plans),
        "usable_slots_first_plate": first.capacity(),
        "theoretical_slots_first_plate": first.theoretical_slots,
        "utilization": round(len(first.placements) / max(1, first.capacity()), 4),
        "reduction": reduction,
        "pseudopage_mm": page_size,
        "warnings": validation.warnings,
        "info": validation.info,
        "estimate": (
            "Verification can take several minutes and several GB of memory even for "
            "one page. Complete the required trial before starting the full corpus."
        ),
    }


class SteleServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], home: str | Path | None = None):
        self.token = secrets.token_urlsafe(24)
        self.store = UploadStore(home)
        self.runs = RunManager(self.store)
        super().__init__(address, SteleHandler)

    @property
    def origin(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def launch_url(self) -> str:
        return f"{self.origin}/?token={urllib.parse.quote(self.token)}"


class SteleHandler(BaseHTTPRequestHandler):
    server: SteleServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        if sys.stderr is not None:
            print(f"stele ui: {self.address_string()} {fmt % args}", file=sys.stderr)

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urllib.parse.urlsplit(self.path)
            if not self._authorized(parsed):
                return
            if parsed.path == "/":
                html = files("stele.ui").joinpath("static", "app.html").read_bytes()
                self._send_bytes(200, html, "text/html; charset=utf-8")
                return
            if parsed.path == "/api/preset":
                self._send_json(200, preset_payload())
                return
            if parsed.path == "/api/doctor":
                result = run_doctor(self.server.store.home)
                write_doctor_json(result, self.server.store.home)
                self._send_json(200, result)
                return
            if parsed.path.startswith("/api/run/"):
                run_id = parsed.path.removeprefix("/api/run/").strip("/")
                self._send_json(200, self.server.runs.get(run_id))
                return
            if parsed.path.startswith("/api/artifact/"):
                rest = parsed.path.removeprefix("/api/artifact/").split("/")
                if len(rest) != 2:
                    raise ValueError("Invalid artifact address")
                path = self.server.runs.artifact(rest[0], rest[1])
                self._send_file(
                    path,
                    download=urllib.parse.parse_qs(parsed.query).get("download") == ["1"],
                )
                return
            self._send_json(404, {"error": "Not found"})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            self._internal_error(exc)

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urllib.parse.urlsplit(self.path)
            if not self._authorized(parsed, effect=True):
                return
            if parsed.path == "/api/upload":
                content_type = self.headers.get("Content-Type", "")
                if content_type.split(";", 1)[0].strip() != "application/pdf":
                    raise ValueError("Choose a PDF file")
                length = self._content_length()
                filename = urllib.parse.unquote(self.headers.get("X-Filename", "document.pdf"))
                document = self.server.store.save(self.rfile, length, filename)
                self._send_json(201, document.public())
                return
            body = self._json_body()
            if parsed.path == "/api/quit":
                if self.server.runs.has_active_run():
                    self._send_json(
                        409,
                        {
                            "error": (
                                "A build is still running. Wait for it to finish or "
                                "cancel it before quitting Stele."
                            )
                        },
                    )
                    return
                self._send_json(200, {"status": "stopping"})
                threading.Thread(
                    target=self.server.shutdown,
                    name="stele-shutdown",
                    daemon=True,
                ).start()
                return
            if parsed.path == "/api/calc":
                self._send_json(200, _page_plan(self.server.store, body))
                return
            if parsed.path == "/api/run":
                run = self.server.runs.start(
                    body.get("documents") or [],
                    str(body.get("plate_name", "")),
                    body.get("settings"),
                    trial=bool(body.get("trial", False)),
                )
                self._send_json(202, run)
                return
            if parsed.path.startswith("/api/run/") and parsed.path.endswith("/cancel"):
                run_id = parsed.path.removeprefix("/api/run/").removesuffix("/cancel").strip("/")
                self._send_json(200, self.server.runs.cancel(run_id))
                return
            self._send_json(404, {"error": "Not found"})
        except (ValueError, KeyError, TypeError) as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            self._internal_error(exc)

    def _authorized(self, parsed: urllib.parse.SplitResult, effect: bool = False) -> bool:
        expected_host = urllib.parse.urlsplit(self.server.origin).netloc
        if self.headers.get("Host") != expected_host:
            self.close_connection = True
            self._send_json(403, {"error": "Host check failed"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin:
            self.close_connection = True
            self._send_json(403, {"error": "Origin check failed"})
            return False
        if effect and origin != self.server.origin:
            self.close_connection = True
            self._send_json(403, {"error": "State-changing requests require the UI origin"})
            return False
        query_token = urllib.parse.parse_qs(parsed.query).get("token", [""])[0]
        header_token = self.headers.get("X-Stele-Token", "")
        if not secrets.compare_digest(query_token or header_token, self.server.token):
            self.close_connection = True
            self._send_json(403, {"error": "This Stele session token is missing or invalid"})
            return False
        return True

    def _content_length(self) -> int:
        raw = self.headers.get("Content-Length")
        if raw is None:
            raise ValueError("Content-Length is required")
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc

    def _json_body(self) -> dict[str, Any]:
        length = self._content_length()
        if length < 0 or length > MAX_JSON_BYTES:
            raise ValueError("Request is too large")
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Invalid JSON request") from exc
        if not isinstance(body, dict):
            raise ValueError("The request must be a JSON object")
        return body

    def _security_headers(self) -> None:
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
            "img-src 'self' blob: data:; connect-src 'self'; font-src 'none'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        self._send_bytes(
            status,
            json.dumps(payload, separators=(",", ":"), default=str).encode(),
            "application/json; charset=utf-8",
        )

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, download: bool) -> None:
        size = path.stat().st_size
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        disposition = "attachment" if download else "inline"
        safe_name = path.name.replace('"', "")
        self.send_header("Content-Disposition", f'{disposition}; filename="{safe_name}"')
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                self.wfile.write(chunk)

    def _internal_error(self, exc: Exception) -> None:
        if sys.stderr is not None:
            print(f"stele ui internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        self._send_json(
            500,
            {"error": "Stele hit an unexpected local error. Open Diagnostics for support details."},
        )


def create_server(
    home: str | Path | None = None,
    port: int = 0,
) -> SteleServer:
    return SteleServer(("127.0.0.1", port), home=home)


def serve(
    home: str | Path | None = None,
    port: int = 0,
    open_browser: bool = True,
    on_ready: Callable[[SteleServer], None] | None = None,
) -> int:
    server = create_server(home=home, port=port)
    if on_ready is not None:
        on_ready(server)
    print(f"Stele is ready at {server.launch_url}")
    print("Documents stay on this computer. Press Ctrl-C to stop.")
    if open_browser:
        threading.Timer(0.2, webbrowser.open, args=(server.launch_url,)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nStopping Stele.")
    finally:
        server.server_close()
    return 0
