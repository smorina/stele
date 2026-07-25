"""Deterministic ingest normalization (review finding 8).

Policy, recorded per page in the build report:
- Page frame = CropBox intersected with MediaBox (what PyMuPDF exposes as
  page.rect), with /Rotate applied — the renderer output IS the normalized page.
- Mixed page sizes: `fit` scales each page uniformly to fit the pseudopage box
  (aspect preserved, anchored bottom-left in y-up plate space); `strict` errors.
- Unsupported PDF features never fail silently: the raster path renders
  whatever the engine composites, and vector-path demotion (M6) is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass

import fitz

from stele.config.manifest import InputSpec, sha256_file


@dataclass(frozen=True)
class PageJob:
    """One page selected for the build, with its normalization record."""

    pdf_path: str
    pdf_sha256: str
    page_index: int  # 0-based
    ordinal: int  # global order across all inputs
    mediabox_pt: tuple[float, float, float, float]
    cropbox_pt: tuple[float, float, float, float]
    rotation_deg: int
    frame_pt: tuple[float, float]  # normalized (rotated) page frame w, h in points

    def record(self) -> dict:
        return {
            "pdf": self.pdf_path,
            "sha256": self.pdf_sha256,
            "page": self.page_index + 1,
            "ordinal": self.ordinal,
            "mediabox_pt": self.mediabox_pt,
            "cropbox_pt": self.cropbox_pt,
            "rotation_deg": self.rotation_deg,
            "frame_pt": self.frame_pt,
        }


def collect_pages(inputs: list[InputSpec]) -> list[PageJob]:
    jobs: list[PageJob] = []
    ordinal = 0
    for spec in inputs:
        sha = sha256_file(spec.path)
        with fitz.open(spec.path) as doc:
            for idx in spec.page_indices(doc.page_count):
                page = doc[idx]
                r = page.rect  # cropbox ∩ mediabox with rotation applied
                jobs.append(
                    PageJob(
                        pdf_path=spec.path,
                        pdf_sha256=sha,
                        page_index=idx,
                        ordinal=ordinal,
                        mediabox_pt=tuple(page.mediabox),
                        cropbox_pt=tuple(page.cropbox),
                        rotation_deg=page.rotation,
                        frame_pt=(r.width, r.height),
                    )
                )
                ordinal += 1
    return jobs
