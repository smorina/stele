"""Job manifest: inputs + profile references + output targets."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from stele.config.profiles import ContentPolicy, FabProfile, LayoutSpec, Profiles, ReaderProfile


class InputSpec(BaseModel):
    path: str
    pages: str = "all"  # "all" | "1-5,8" (1-based, inclusive ranges)

    def page_indices(self, n_pages: int) -> list[int]:
        if self.pages.strip().lower() == "all":
            return list(range(n_pages))
        out: list[int] = []
        for part in self.pages.split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-")
                out.extend(range(int(a) - 1, int(b)))
            else:
                out.append(int(part) - 1)
        bad = [i for i in out if i < 0 or i >= n_pages]
        if bad:
            raise ValueError(f"page selection out of range (document has {n_pages} pages): {bad}")
        return out


class OutputSpec(BaseModel):
    gds: str = "out/plate.gds"
    report: str = ""  # default: <gds>.report.json
    preview: str = ""  # default: <gds>.preview.png
    plate_name: str = "STELE-0001"

    def report_path(self) -> str:
        return self.report or self.gds + ".report.json"

    def preview_path(self) -> str:
        return self.preview or self.gds + ".preview.png"


class JobManifest(BaseModel):
    inputs: list[InputSpec]
    profiles: dict[str, str] = Field(
        description="paths to profile YAMLs keyed by fab/reader/content/layout"
    )
    output: OutputSpec = OutputSpec()
    base_dir: str = ""  # directory of the manifest file; set by load_manifest


def _load_yaml(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_manifest(path: str) -> tuple[JobManifest, Profiles]:
    base = os.path.dirname(os.path.abspath(path))
    data = _load_yaml(path)
    manifest = JobManifest.model_validate(data)
    manifest.base_dir = base

    def resolve(p: str) -> str:
        return p if os.path.isabs(p) else os.path.join(base, p)

    for inp in manifest.inputs:
        inp.path = resolve(inp.path)
    manifest.output.gds = resolve(manifest.output.gds)
    if manifest.output.report:
        manifest.output.report = resolve(manifest.output.report)
    if manifest.output.preview:
        manifest.output.preview = resolve(manifest.output.preview)

    missing = {"fab", "reader", "content", "layout"} - set(manifest.profiles)
    if missing:
        raise ValueError(f"manifest missing profile references: {sorted(missing)}")
    profiles = Profiles(
        fab=FabProfile.model_validate(_load_yaml(resolve(manifest.profiles["fab"]))),
        reader=ReaderProfile.model_validate(_load_yaml(resolve(manifest.profiles["reader"]))),
        content=ContentPolicy.model_validate(_load_yaml(resolve(manifest.profiles["content"]))),
        layout=LayoutSpec.model_validate(_load_yaml(resolve(manifest.profiles["layout"]))),
    )
    return manifest, profiles


def resolved_config_dump(manifest: JobManifest, profiles: Profiles) -> dict:
    """Fully-resolved config for the build report (reproducibility)."""
    return {
        "manifest": manifest.model_dump(),
        "profiles": {
            "fab": profiles.fab.model_dump(),
            "reader": profiles.reader.model_dump(),
            "content": profiles.content.model_dump(),
            "layout": profiles.layout.model_dump(),
        },
    }


def sha256_file(path: str | Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
