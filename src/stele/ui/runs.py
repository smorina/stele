"""Uploaded-document storage and reproducible UI run orchestration."""

from __future__ import annotations

import hashlib
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
from stele.doctor import default_home
from stele.ui.presets import normalized_settings, profiles_from_settings
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

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "page_count": self.page_count,
            "pages": self.pages,
            "contains_images": self.contains_images,
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

    @staticmethod
    def _inspect(path: Path) -> dict[str, Any]:
        pages: list[dict[str, Any]] = []
        contains_images = False
        try:
            with fitz.open(path) as pdf:
                if pdf.page_count == 0:
                    raise ValueError("The PDF has no pages")
                for index, page in enumerate(pdf):
                    rect = page.rect
                    images = bool(page.get_images(full=True))
                    contains_images = contains_images or images
                    pages.append(
                        {
                            "number": index + 1,
                            "width_pt": round(rect.width, 2),
                            "height_pt": round(rect.height, 2),
                            "mediabox_pt": list(page.mediabox),
                            "cropbox_pt": list(page.cropbox),
                            "rotation_deg": page.rotation,
                            "contains_images": images,
                        }
                    )
        except fitz.FileDataError as exc:
            raise ValueError(f"The PDF could not be opened: {exc}") from exc
        return {
            "page_count": len(pages),
            "pages": pages,
            "contains_images": contains_images,
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

    manifest_inputs: list[dict[str, str]] = []
    for number, (document, pages) in enumerate(chosen, start=1):
        target = input_dir / f"{number:02d}-{document.name}"
        _link_or_copy(document.path, target)
        manifest_inputs.append({"path": str(target.relative_to(root)), "pages": pages})

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
    return RunWorkspace(
        id=run_id,
        root=root,
        manifest=manifest,
        plate_name=clean_name,
        artifact_base=artifact_base,
        settings=clean_settings,
        trial=trial,
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

    def public(self) -> dict[str, Any]:
        return {
            "id": self.workspace.id,
            "status": self.status,
            "stage": self.stage,
            "done": self.done,
            "total": self.total,
            "detail": self.detail,
            "error": self.error,
            "result": self.result,
            "artifacts": self.artifacts,
            "trial": self.workspace.trial,
            "cli_command": f'stele build "{self.workspace.manifest}"',
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
            with self._lock:
                state.status = "complete"
                state.stage = "complete"
                state.done = state.total = 1
                state.detail = "Your files are ready"
                state.result = summarize_report(report)
                state.artifacts = artifacts
                state.artifact_paths = paths
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
        return entries, paths
