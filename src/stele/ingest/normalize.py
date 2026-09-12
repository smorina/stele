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
    # fonts the page uses but does not embed: the build and reference engines
    # substitute DIFFERENT fonts for these, so glyph-shape disagreements on
    # such pages are a known cause of verification defects (recorded so the
    # verdict can say so)
    fonts_not_embedded: tuple[str, ...] = ()

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
            "fonts_not_embedded": list(self.fonts_not_embedded),
        }


def unembedded_fonts(page) -> list[str]:
    """Base names of fonts a PyMuPDF page references without embedding them
    (PyMuPDF reports the file extension as 'n/a'). Type3 fonts are glyph
    procedures inside the PDF and never need embedding."""
    names: set[str] = set()
    try:
        entries = page.get_fonts(full=True)
    except Exception:
        return []
    for entry in entries:
        ext = entry[1] if len(entry) > 1 else ""
        ftype = entry[2] if len(entry) > 2 else ""
        base = entry[3] if len(entry) > 3 else ""
        if ext == "n/a" and ftype != "Type3":
            names.add(str(base) or "unnamed")
    return sorted(names)


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
                        fonts_not_embedded=tuple(unembedded_fonts(page)),
                    )
                )
                ordinal += 1
    return jobs
