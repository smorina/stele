"""Uploaded-document storage and reproducible UI run orchestration."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Callable

import fitz
import yaml

from stele.build import Cancelled, build_job
from stele.config.manifest import InputSpec
from stele.config.profiles import ReaderProfile
from stele.doctor import default_home
from stele.ingest.normalize import unembedded_fonts
from stele.ui.planning import plan_from_report
from stele.ui.presets import (
    normalized_settings,
    profiles_from_settings,
    settings_from_profiles,
)
from stele.ui.verdict import summarize_report

MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024


def _safe_name(name: str) -> str:
    plain = Path(name).name
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "-", plain).strip(". ")
    if not stem.lower().endswith(".pdf"):
        stem += ".pdf"
    return stem[:180] or "document.pdf"


def _artifact_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return slug[:140] or "stele-run"


@dataclass
class UploadedDocument:
    id: str
    path: Path
    name: str
    size_bytes: int
    sha256: str
    page_count: int
    pages: list[dict[str, Any]]
    contains_images: bool
    fonts_not_embedded: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "page_count": self.page_count,
            "pages": self.pages,
            "contains_images": self.contains_images,
            "fonts_not_embedded": self.fonts_not_embedded,
        }


class UploadStore:
    def __init__(self, home: str | Path | None = None):
        self.home = Path(home) if home is not None else default_home()
        self.root = self.home / "uploads"
        self.root.mkdir(parents=True, exist_ok=True)
        self._documents: dict[str, UploadedDocument] = {}
        self._lock = threading.Lock()

    def save(self, stream: BinaryIO, length: int, filename: str) -> UploadedDocument:
        if length <= 0:
            raise ValueError("The selected file is empty")
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("PDFs larger than 2 GB are not accepted by the local UI")
        doc_id = uuid.uuid4().hex
        folder = self.root / doc_id
        folder.mkdir(mode=0o700)
        path = folder / _safe_name(filename)
        digest = hashlib.sha256()
        remaining = length
        try:
            with path.open("xb") as out:
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("The upload ended before the declared file size")
                    out.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
            with path.open("rb") as uploaded:
                signature_region = uploaded.read(1024)
            if b"%PDF-" not in signature_region:
                raise ValueError("The selected file is not a PDF")
            info = self._inspect(path)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        document = UploadedDocument(
            id=doc_id,
            path=path,
            name=path.name,
            size_bytes=length,
            sha256=digest.hexdigest(),
            **info,
        )
        with self._lock:
            self._documents[doc_id] = document
        return document

    def get(self, doc_id: str) -> UploadedDocument:
        with self._lock:
            document = self._documents.get(doc_id)
        if document is None:
            raise ValueError("That uploaded document is no longer available; choose it again")
        return document

    def adopt(self, source: Path, filename: str) -> UploadedDocument:
        """Register a PDF that already exists on this computer (a previous
        run's input) as if it had just been uploaded. The same file adopted
        twice returns the existing document instead of another copy."""
        source = Path(source)
        if not source.is_file():
            raise ValueError(f"{filename}: the file is no longer in the run folder")
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        sha256 = digest.hexdigest()
        safe = _safe_name(filename)
        with self._lock:
            for document in self._documents.values():
                if document.sha256 == sha256 and document.name == safe:
                    return document
        doc_id = uuid.uuid4().hex
        folder = self.root / doc_id
        folder.mkdir(mode=0o700)
        path = folder / safe
        try:
            _link_or_copy(source, path)
            info = self._inspect(path)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        document = UploadedDocument(
            id=doc_id,
            path=path,
            name=path.name,
            size_bytes=path.stat().st_size,
            sha256=sha256,
            **info,
        )
        with self._lock:
            self._documents[doc_id] = document
        return document

    @staticmethod
    def _inspect(path: Path) -> dict[str, Any]:
        pages: list[dict[str, Any]] = []
        contains_images = False
        fonts: set[str] = set()
        try:
            with fitz.open(path) as pdf:
                if pdf.page_count == 0:
                    raise ValueError("The PDF has no pages")
                for index, page in enumerate(pdf):
                    rect = page.rect
                    images = bool(page.get_images(full=True))
                    contains_images = contains_images or images
                    # non-embedded fonts are substituted differently by the
                    # build and reference engines: surfaced before the build
                    # so a verification difference is not a surprise
                    page_fonts = unembedded_fonts(page)
                    fonts.update(page_fonts)
                    pages.append(
                        {
                            "number": index + 1,
                            "width_pt": round(rect.width, 2),
                            "height_pt": round(rect.height, 2),
                            "mediabox_pt": list(page.mediabox),
                            "cropbox_pt": list(page.cropbox),
                            "rotation_deg": page.rotation,
                            "contains_images": images,
                            "fonts_not_embedded": page_fonts,
                        }
                    )
        except fitz.FileDataError as exc:
            raise ValueError(f"The PDF could not be opened: {exc}") from exc
        return {
            "page_count": len(pages),
            "pages": pages,
            "contains_images": contains_images,
            "fonts_not_embedded": sorted(fonts),
        }


@dataclass
class RunWorkspace:
    id: str
    root: Path
    manifest: Path
    plate_name: str
    artifact_base: str
    settings: dict[str, Any]
    trial: bool
    # the page's original selection (every document, its page choice), kept
    # so a run can be reopened with the same documents even after a trial
    # reduced the manifest to one page
    selection: dict[str, Any] | None = None


def _link_or_copy(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def create_workspace(
    store: UploadStore,
    selections: list[dict[str, Any]],
    plate_name: str,
    settings: dict[str, Any] | None,
    trial: bool = False,
) -> RunWorkspace:
    if not selections:
        raise ValueError("Choose at least one PDF")
    clean_settings = normalized_settings(settings)
    clean_name = re.sub(r"[^A-Za-z0-9._ -]+", "-", plate_name).strip()[:64]
    if not clean_name:
        raise ValueError("Give the plate a short name")

    chosen: list[tuple[UploadedDocument, str]] = []
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
        chosen.append((document, pages))
    original = list(chosen)
    if trial:
        document, pages = chosen[0]
        first_index = InputSpec(path=str(document.path), pages=pages).page_indices(
            document.page_count
        )[0]
        chosen = [(document, str(first_index + 1))]

    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    first_source = Path(chosen[0][0].name).stem
    source_label = (
        f"{first_source}-plus-{len(chosen) - 1}-more" if len(chosen) > 1 else first_source
    )
    artifact_base = _artifact_slug(
        f"{clean_name}-{source_label}-{'trial' if trial else 'full'}-{run_id[-6:]}"
    )
    home = store.home
    root = home / "runs" / run_id
    input_dir = root / "input"
    config_dir = root / "config"
    artifact_dir = root / "artifacts"
    for folder in (input_dir, config_dir, artifact_dir):
        folder.mkdir(parents=True, exist_ok=False)

    # every selected document is kept with the run (hard links cost nothing),
    # so reopening a trial can restore the whole selection; the manifest only
    # references what this run actually builds
    targets: dict[str, Path] = {}
    selection_docs: list[dict[str, Any]] = []
    for number, (document, pages) in enumerate(original, start=1):
        target = input_dir / f"{number:02d}-{document.name}"
        _link_or_copy(document.path, target)
        targets[document.id] = target
        selection_docs.append(
            {
                "name": document.name,
                "pages": pages,
                "input": str(target.relative_to(root)),
                "sha256": document.sha256,
                "page_count": document.page_count,
            }
        )
    manifest_inputs: list[dict[str, str]] = [
        {"path": str(targets[document.id].relative_to(root)), "pages": pages}
        for document, pages in chosen
    ]
    selection = {"plate_name": clean_name, "trial": trial, "documents": selection_docs}
    (config_dir / "selection.json").write_text(json.dumps(selection, indent=2) + "\n")

    profiles = profiles_from_settings(clean_settings)
    profile_paths: dict[str, str] = {}
    for kind, model in (
        ("fab", profiles.fab),
        ("reader", profiles.reader),
        ("content", profiles.content),
        ("layout", profiles.layout),
    ):
        path = config_dir / f"{kind}.yaml"
        path.write_text(yaml.safe_dump(model.model_dump(), sort_keys=False))
        profile_paths[kind] = str(path.relative_to(root))

    manifest_data = {
        "inputs": manifest_inputs,
        "profiles": profile_paths,
        "output": {
            "gds": f"artifacts/{artifact_base}.gds",
            "report": f"artifacts/{artifact_base}.report.json",
            "preview": f"artifacts/{artifact_base}.preview.png",
            "plate_name": clean_name,
        },
    }
    manifest = root / f"{artifact_base}.manifest.yaml"
    manifest.write_text(yaml.safe_dump(manifest_data, sort_keys=False))
    # the UI settings behind the profiles, so a later session can reopen the
    # run with the same choices (run history)
    (config_dir / "settings.json").write_text(json.dumps(clean_settings, indent=2) + "\n")
    return RunWorkspace(
        id=run_id,
        root=root,
        manifest=manifest,
        plate_name=clean_name,
        artifact_base=artifact_base,
        settings=clean_settings,
        trial=trial,
        selection=selection,
    )


@dataclass
class RunState:
    workspace: RunWorkspace
    status: str = "queued"
    stage: str = "preparing"
    done: int = 0
    total: int = 1
    detail: str = "Waiting to start"
    cancel_requested: bool = False
    error: str = ""
    diagnostic: str = ""
    result: dict[str, Any] | None = None
    artifacts: list[dict[str, str]] = field(default_factory=list)
    artifact_paths: dict[str, Path] = field(default_factory=dict)
    report: dict[str, Any] | None = None
    simulation: dict[str, Any] | None = None
    plan: dict[str, Any] | None = None

    def created_label(self) -> str:
        stamp = self.workspace.id[:15]
        try:
            return datetime.strptime(stamp, "%Y%m%d-%H%M%S").strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return stamp

    def public(self) -> dict[str, Any]:
        return {
            "id": self.workspace.id,
            "status": self.status,
            "stage": self.stage,
            "done": self.done,
            "total": self.total,
            "detail": self.detail,
            "error": self.error,
            "diagnostic": self.diagnostic,
            "result": self.result,
            "artifacts": self.artifacts,
            "trial": self.workspace.trial,
            "plate_name": self.workspace.plate_name,
            "created": self.created_label(),
            "settings": self.workspace.settings,
            "selection": self.workspace.selection,
            "plan": self.plan,
            "simulation": self.simulation,
            "cli_command": f'stele build "{self.workspace.manifest}"',
        }


def simulation_defaults(report: dict[str, Any]) -> dict[str, Any] | None:
    """What the microscope panel needs from a finished report: the page cells
    per plate, the reader the build used, the plate tone, and a default
    viewport — the densest text window the readability gate measured,
    converted from the gate's y-down raster rows to page-up micrometres."""
    plates = report.get("plates") or []
    if not plates:
        return None
    cells: list[dict[str, Any]] = []
    default = None
    for plate_index, plate in enumerate(plates, start=1):
        seen: set[str] = set()
        ingest = plate.get("ingest") or []
        for pl, record in zip(plate.get("placements") or [], ingest):
            if pl["cell"] in seen:
                continue
            seen.add(pl["cell"])
            frame = record.get("frame_pt") or [612.0, 792.0]
            scale = float(pl["scale"])
            cells.append(
                {
                    "cell": pl["cell"],
                    "plate": plate_index,
                    "page": int(record.get("page", 0)),
                    "source": os.path.basename(str(record.get("pdf", ""))),
                    "tier": pl.get("tier_scale", 1.0),
                    "page_um": [
                        round(frame[0] * 25400.0 / 72.0 * scale, 1),
                        round(frame[1] * 25400.0 / 72.0 * scale, 1),
                    ],
                }
            )
        if default is None:
            readability = plate.get("verify", {}).get("readability") or {}
            for tier in sorted(readability):
                r = readability[tier]
                if r.get("assessed") and r.get("window_um") and r.get("cell"):
                    match = next((c for c in cells if c["cell"] == r["cell"]
                                  and c["plate"] == plate_index), None)
                    if match is None:
                        continue
                    x, y_top, w, h = r["window_um"]
                    default = {
                        "plate": plate_index,
                        "cell": r["cell"],
                        "region_um": [round(x, 1), round(match["page_um"][1] - y_top - h, 1),
                                      w, h],
                        "why": "the densest text window the readability gate measured",
                    }
                    break
    if default is None and cells:
        c = cells[0]
        default = {
            "plate": c["plate"],
            "cell": c["cell"],
            "region_um": [round(c["page_um"][0] / 2 - 200.0, 1),
                          round(c["page_um"][1] / 2 - 150.0, 1), 400.0, 300.0],
            "why": "the page center",
        }
    reader = report.get("config", {}).get("profiles", {}).get("reader", {})
    return {
        "cells": cells,
        "default": default,
        "reader": {
            k: reader.get(k)
            for k in ("magnification", "numerical_aperture", "wavelength_nm",
                      "contrast_criterion")
        },
        "polarity": report.get("orientation", {}).get("polarity", "clear_field"),
        "gds": list(report.get("plate_set", {}).get("gds") or []),
    }


class RunManager:
    def __init__(
        self,
        store: UploadStore,
        builder: Callable[..., dict[str, Any]] = build_job,
    ):
        self.store = store
        self.builder = builder
        self._runs: dict[str, RunState] = {}
        self._lock = threading.Lock()
        self._history_loaded = False
        # one opened plate at a time for the microscope panel: a full plate's
        # GDS can be hundreds of MB, so this is never a per-run cache
        self._layout_cache: tuple[Path, Any] | None = None
        self._layout_lock = threading.Lock()

    def start(
        self,
        selections: list[dict[str, Any]],
        plate_name: str,
        settings: dict[str, Any] | None,
        trial: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            if any(state.status in {"queued", "running"} for state in self._runs.values()):
                raise ValueError(
                    "Another build is already running. Wait for it to finish or cancel it first."
                )
            workspace = create_workspace(
                self.store, selections, plate_name, settings, trial=trial
            )
            state = RunState(workspace=workspace)
            self._runs[workspace.id] = state
        threading.Thread(
            target=self._run,
            args=(workspace.id,),
            name=f"stele-{workspace.id}",
            daemon=True,
        ).start()
        return state.public()

    def get(self, run_id: str) -> dict[str, Any]:
        return self._state(run_id).public()

    def history(self) -> list[dict[str, Any]]:
        """Every run this session knows about plus completed runs found on
        disk under <home>/runs, newest first."""
        self._load_history()
        with self._lock:
            states = list(self._runs.values())
        states.sort(key=lambda st: st.workspace.id, reverse=True)
        return [st.public() for st in states]

    def _load_history(self) -> None:
        """Register completed runs from earlier sessions (report present) as
        read-only complete states; runs without a report are listed as
        incomplete so their folders are still discoverable."""
        runs_root = self.store.home / "runs"
        if not runs_root.is_dir():
            return
        for folder in sorted(runs_root.iterdir()):
            if not folder.is_dir():
                continue
            with self._lock:
                if folder.name in self._runs:
                    continue
            state = self._state_from_disk(folder)
            if state is None:
                continue
            with self._lock:
                self._runs.setdefault(folder.name, state)
        self._history_loaded = True

    @staticmethod
    def _state_from_disk(folder: Path) -> RunState | None:
        manifests = sorted(folder.glob("*.manifest.yaml"))
        if not manifests:
            return None
        manifest = manifests[0]
        artifact_base = manifest.name.removesuffix(".manifest.yaml")
        try:
            manifest_data = yaml.safe_load(manifest.read_text()) or {}
        except (OSError, yaml.YAMLError):
            return None
        plate_name = str((manifest_data.get("output") or {}).get("plate_name") or artifact_base)
        trial = "-trial-" in artifact_base
        settings: dict[str, Any] = {}
        settings_path = folder / "config" / "settings.json"
        if settings_path.is_file():
            try:
                settings = normalized_settings(json.loads(settings_path.read_text()))
            except (OSError, ValueError):
                settings = {}
        if not settings:
            # a run from before settings.json: recover the page's settings
            # from the profile YAMLs the run was built with
            profiles: dict[str, dict[str, Any]] = {}
            for kind, rel in (manifest_data.get("profiles") or {}).items():
                path = folder / rel
                if path.is_file():
                    try:
                        profiles[kind] = yaml.safe_load(path.read_text()) or {}
                    except (OSError, yaml.YAMLError):
                        pass
            try:
                settings = settings_from_profiles(profiles) if profiles else {}
            except ValueError:
                settings = {}
        selection: dict[str, Any] | None = None
        selection_path = folder / "config" / "selection.json"
        if selection_path.is_file():
            try:
                selection = json.loads(selection_path.read_text())
            except (OSError, ValueError):
                selection = None
        if selection is None:
            # older runs: the manifest's inputs are the selection
            docs = []
            for item in manifest_data.get("inputs") or []:
                rel = str(item.get("path", ""))
                name = re.sub(r"^\d{2}-", "", Path(rel).name)
                docs.append({"name": name, "pages": str(item.get("pages", "all")), "input": rel})
            selection = {"plate_name": plate_name, "trial": trial, "documents": docs}
        workspace = RunWorkspace(
            id=folder.name,
            root=folder,
            manifest=manifest,
            plate_name=plate_name,
            artifact_base=artifact_base,
            settings=settings,
            trial=trial,
            selection=selection,
        )
        state = RunState(workspace=workspace)
        report_path = folder / "artifacts" / f"{artifact_base}.report.json"
        if not report_path.is_file():
            state.status = "incomplete"
            state.stage = "incomplete"
            state.detail = "No report was written for this run (cancelled, failed, or still running elsewhere)"
            return state
        try:
            report = json.loads(report_path.read_text())
        except (OSError, ValueError):
            state.status = "incomplete"
            state.stage = "incomplete"
            state.detail = "The report for this run could not be read"
            return state
        artifacts, paths = RunManager._collect_artifacts(workspace, report)
        state.status = "complete"
        state.stage = "complete"
        state.done = state.total = 1
        state.detail = "Completed in an earlier session"
        state.result = summarize_report(report)
        state.artifacts = artifacts
        state.artifact_paths = paths
        state.report = report
        state.simulation = simulation_defaults(report)
        state.plan = plan_from_report(report)
        return state

    def restore(self, run_id: str) -> dict[str, Any]:
        """Bring a previous run's documents back into this session (adopting
        the copies kept in its run folder) and hand the page everything it
        needs to show the same plate again: documents with their page
        selections, the settings, and the plate name."""
        state = self._state(run_id)
        selection = state.workspace.selection or {"documents": []}
        root = state.workspace.root.resolve()
        documents: list[dict[str, Any]] = []
        missing: list[str] = []
        for item in selection.get("documents") or []:
            rel = str(item.get("input", ""))
            source = (state.workspace.root / rel).resolve()
            if not rel or root not in source.parents or not source.is_file():
                missing.append(str(item.get("name") or rel))
                continue
            try:
                document = self.store.adopt(source, str(item.get("name") or source.name))
            except ValueError as exc:
                missing.append(f"{item.get('name') or rel}: {exc}")
                continue
            public = document.public()
            public["pages"] = str(item.get("pages", "all")) or "all"
            documents.append(public)
        return {
            "id": state.workspace.id,
            "plate_name": state.workspace.plate_name,
            "trial": state.workspace.trial,
            "settings": state.workspace.settings,
            "documents": documents,
            "missing": missing,
        }

    def simulate(self, run_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Through-the-microscope view of a region of a finished run, with the
        reader optics overridden by the request. Reads only the built GDS."""
        from stele.verify.renderback import open_layout
        from stele.verify.simulate import default_region, simulate_region

        state = self._state(run_id)
        if state.status != "complete" or state.report is None or state.simulation is None:
            raise ValueError("The microscope view needs a completed build with a report")
        sim = state.simulation
        gds_paths = sim.get("gds") or []
        plate_index = int(params.get("plate") or (sim["default"] or {}).get("plate") or 1)
        if not 1 <= plate_index <= len(gds_paths):
            raise ValueError("That plate does not exist in this run")
        gds_path = Path(gds_paths[plate_index - 1])
        root = state.workspace.root.resolve()
        if root not in gds_path.resolve().parents or not gds_path.is_file():
            raise ValueError("The manufacturing file for this run is missing")
        cell = str(params.get("cell") or (sim["default"] or {}).get("cell") or "PAGE_0000")
        known = {c["cell"] for c in sim["cells"] if c["plate"] == plate_index}
        if cell not in known:
            raise ValueError(f"Cell {cell} is not a page of plate {plate_index}")
        reader_defaults = {k: v for k, v in (sim.get("reader") or {}).items() if v is not None}
        reader_settings = normalized_settings(
            {**reader_defaults, **{k: params[k] for k in reader_defaults if k in params}}
        )
        reader = ReaderProfile(
            name="ui-microscope",
            magnification=reader_settings["magnification"],
            numerical_aperture=reader_settings["numerical_aperture"],
            wavelength_nm=reader_settings["wavelength_nm"],
            contrast_criterion=reader_settings["contrast_criterion"],
        )
        try:
            defocus = float(params.get("defocus_um", 0.0) or 0.0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Defocus must be a number") from exc
        if not -200.0 <= defocus <= 200.0:
            raise ValueError("Defocus must be between -200 and 200 µm")
        fab = state.report.get("config", {}).get("profiles", {}).get("fab", {})
        with self._layout_lock:
            if self._layout_cache is None or self._layout_cache[0] != gds_path:
                self._layout_cache = (gds_path, open_layout(str(gds_path)))
            layout = self._layout_cache[1]
            region = params.get("region")
            if region:
                try:
                    region_um = tuple(float(v) for v in region)
                except (TypeError, ValueError) as exc:
                    raise ValueError("The region must be four numbers: x, y, width, height") from exc
                if len(region_um) != 4:
                    raise ValueError("The region must be four numbers: x, y, width, height")
                if region_um[2] <= 0 or region_um[3] <= 0:
                    raise ValueError("The region width and height must be positive")
                if region_um[2] > 3000 or region_um[3] > 3000:
                    raise ValueError("Keep the region under 3 mm on a side")
            else:
                region_um = default_region(layout, cell)
            result = simulate_region(
                layout, cell, region_um, reader,
                int(fab.get("layer", 1)), int(fab.get("datatype", 0)),
                polarity=sim.get("polarity", "clear_field"), defocus_um=defocus,
            )
        png = result.pop("png")
        result["png_base64"] = base64.b64encode(png).decode("ascii")
        result["plate"] = plate_index
        result["build_reader"] = sim.get("reader")
        return result

    def cancel(self, run_id: str) -> dict[str, Any]:
        state = self._state(run_id)
        with self._lock:
            if state.status in {"queued", "running"}:
                state.cancel_requested = True
                state.detail = "Cancellation requested; stopping at the next safe boundary"
        return state.public()

    def has_active_run(self) -> bool:
        with self._lock:
            return any(state.status in {"queued", "running"} for state in self._runs.values())

    def artifact(self, run_id: str, artifact_id: str) -> Path:
        state = self._state(run_id)
        path = state.artifact_paths.get(artifact_id)
        if path is None:
            raise ValueError("That artifact does not exist")
        root = state.workspace.root.resolve()
        resolved = path.resolve()
        if path.is_symlink() or root not in resolved.parents or not resolved.is_file():
            raise ValueError("The artifact path is not safe")
        return resolved

    def _state(self, run_id: str) -> RunState:
        with self._lock:
            state = self._runs.get(run_id)
        if state is None and not self._history_loaded:
            self._load_history()
            with self._lock:
                state = self._runs.get(run_id)
        if state is None:
            raise ValueError("That run is not available in this Stele session")
        return state

    def _progress(self, run_id: str, stage: str, done: int, total: int, detail: str):
        state = self._state(run_id)
        with self._lock:
            state.status = "running"
            state.stage = stage
            state.done = done
            state.total = max(1, total)
            state.detail = detail
            return "cancel" if state.cancel_requested else None

    def _run(self, run_id: str) -> None:
        state = self._state(run_id)
        try:
            with self._lock:
                state.status = "running"
                state.detail = "Starting the compiler"
            report = self.builder(
                str(state.workspace.manifest),
                verify=True,
                preview=True,
                progress=lambda stage, done, total, detail: self._progress(
                    run_id, stage, done, total, detail
                ),
            )
            artifacts, paths = self._collect_artifacts(state.workspace, report)
            with self._layout_lock:
                self._layout_cache = None  # a new plate supersedes any opened one
            with self._lock:
                state.status = "complete"
                state.stage = "complete"
                state.done = state.total = 1
                state.detail = "Your files are ready"
                state.result = summarize_report(report)
                state.artifacts = artifacts
                state.artifact_paths = paths
                state.report = report
                state.simulation = simulation_defaults(report)
                state.plan = plan_from_report(report)
        except Cancelled:
            shutil.rmtree(state.workspace.root / "artifacts", ignore_errors=True)
            with self._lock:
                state.status = "cancelled"
                state.detail = "Build cancelled; partial manufacturing files were removed"
        except BaseException as exc:
            with self._lock:
                state.status = "error"
                state.error = str(exc) or type(exc).__name__
                state.detail = "The build stopped"
                state.diagnostic = traceback.format_exc()

    @staticmethod
    def _collect_artifacts(
        workspace: RunWorkspace, report: dict[str, Any]
    ) -> tuple[list[dict[str, str]], dict[str, Path]]:
        entries: list[dict[str, str]] = []
        paths: dict[str, Path] = {}

        def add(artifact_id: str, label: str, kind: str, path: str | Path) -> None:
            candidate = Path(path)
            if candidate.is_file():
                entries.append(
                    {
                        "id": artifact_id,
                        "label": f"{label} · {candidate.name}",
                        "kind": kind,
                    }
                )
                paths[artifact_id] = candidate

        add("manifest", "Reproducible job manifest", "config", workspace.manifest)
        report_path = (
            workspace.root / "artifacts" / f"{workspace.artifact_base}.report.json"
        )
        add("report", "Verification report (JSON)", "report", report_path)
        for index, plate in enumerate(report.get("plates", []), start=1):
            suffix = f" · plate {index}" if len(report.get("plates", [])) > 1 else ""
            add(f"gds-{index}", f"Manufacturing file{suffix}", "gds", plate["gds"]["path"])
            if plate.get("preview"):
                add(
                    f"preview-{index}",
                    f"Plate preview{suffix}",
                    "preview",
                    plate["preview"],
                )
            # defect heatmaps: the findings tell the user to inspect them, so
            # they must be reachable from the page, not only from the run folder
            for heatmap in plate.get("verify", {}).get("heatmaps") or []:
                cell = Path(heatmap).stem
                add(f"heatmap-{index}-{cell}", f"Defect heatmap{suffix} · {cell}", "heatmap",
                    heatmap)
        return entries, paths
