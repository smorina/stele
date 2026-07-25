"""Deterministic synthetic PDF generators (the regression corpus).

All documents are generated with PyMuPDF drawing/text APIs — no external
assets, byte-stable across runs (fixed metadata, no timestamps).
"""

from __future__ import annotations

import fitz

LETTER = fitz.paper_rect("letter")
_FONT = "EF0"  # embedded font alias
_FONT_BUFFER = fitz.Font("helv").buffer  # PyMuPDF's bundled Nimbus Sans


def _new_doc() -> fitz.Document:
    return fitz.open()


def _new_page(doc: fitz.Document):
    """New page with the font EMBEDDED. Unembedded Base-14 fonts get
    substituted differently by every renderer (mupdf: bundled Nimbus,
    pdfium: a system font) — measured as ~185 phantom 'defects' per page of
    small text. Embedding makes both verification engines shape identical
    glyphs, like real archival PDFs (USPTO embeds everything)."""
    page = doc.new_page(width=LETTER.width, height=LETTER.height)
    page.insert_font(fontname=_FONT, fontbuffer=_FONT_BUFFER)
    return page


def _save(doc: fitz.Document, path: str) -> str:
    doc.set_metadata({})  # no creation date -> byte-stable
    doc.save(path, deflate=True)
    doc.close()
    return path


def font_ladder(path: str) -> str:
    """The same pangram at descending point sizes (72 -> 4 pt)."""
    doc = _new_doc()
    page = _new_page(doc)
    y = 60.0
    for pt in (72, 48, 36, 24, 18, 14, 12, 10, 8, 6, 5, 4):
        page.insert_text((36, y), f"{pt}pt Sphinx of black quartz, judge my vow", fontsize=pt, fontname=_FONT)
        y += pt * 1.6 + 8
    return _save(doc, path)


def stroke_wedge(path: str) -> str:
    """Horizontal rules of descending width: 8 pt down to 0.05 pt."""
    doc = _new_doc()
    page = _new_page(doc)
    y = 60.0
    for w in (8, 6, 4, 3, 2, 1.5, 1, 0.75, 0.5, 0.35, 0.25, 0.15, 0.1, 0.05):
        page.draw_line((72, y), (540, y), width=w)
        page.insert_text((548, y + 2), f"{w}pt", fontsize=8, fontname=_FONT)
        y += 40
    return _save(doc, path)


def checkerboard(path: str, n: int = 24) -> str:
    doc = _new_doc()
    page = _new_page(doc)
    cell = 500.0 / n
    for i in range(n):
        for j in range(n):
            if (i + j) % 2 == 0:
                r = fitz.Rect(56 + i * cell, 136 + j * cell, 56 + (i + 1) * cell, 136 + (j + 1) * cell)
                page.draw_rect(r, color=None, fill=(0, 0, 0))
    return _save(doc, path)


def _fill_textbox(page, rect, words: list[str], fontsize: float) -> None:
    """insert_textbox writes NOTHING when the text overflows — fit by halving."""
    n = len(words)
    while n > 0:
        text = " ".join(words[:n])
        if page.insert_textbox(rect, text, fontsize=fontsize, fontname=_FONT) >= 0:
            return
        n //= 2
    raise RuntimeError("could not fit any text into the box")


def text_page(path: str, seed_text: str = "", body_pt: float = 10.0) -> str:
    """A dense body-text page (the typical archive page)."""
    doc = _new_doc()
    page = _new_page(doc)
    words = (seed_text or (
        "We hold these truths to be self evident that all knowledge deserves "
        "a container more durable than the civilization that produced it "
    )).split()
    stream = [words[i % len(words)] for i in range(2200)]
    rect = fitz.Rect(54, 54, LETTER.width - 54, LETTER.height - 54)
    _fill_textbox(page, rect, stream, body_pt)
    return _save(doc, path)


def mixed_page(path: str) -> str:
    """Text + line art + a filled figure with holes (nesting exercise)."""
    doc = _new_doc()
    page = _new_page(doc)
    page.insert_text((54, 70), "Mixed content page", fontsize=24, fontname=_FONT)
    rect = fitz.Rect(54, 100, 558, 320)
    _fill_textbox(page, rect, ("hole nesting oOoOo " * 60).split(), 11)
    # donut with a dot inside the hole (CCOMP nesting case); color=None -> no
    # stroke (a zero-width hairline stroke renders differently across engines)
    page.draw_circle((160, 480), 90, color=None, fill=(0, 0, 0))
    page.draw_circle((160, 480), 55, color=None, fill=(1, 1, 1))
    page.draw_circle((160, 480), 20, color=None, fill=(0, 0, 0))
    # hatching
    for k in range(40):
        page.draw_line((300 + 5 * k, 400), (300 + 5 * k, 560), width=0.6)
    return _save(doc, path)


def pathological(path: str) -> str:
    """Rotation, even-odd star, clip path, zero-width strokes."""
    doc = _new_doc()
    page = _new_page(doc)
    page.insert_text((60, 80), "rotated page content", fontsize=18, rotate=90, fontname=_FONT)
    star = [
        (306, 400), (346, 520), (240, 445), (372, 445), (266, 520), (306, 400),
    ]
    page.draw_polyline(star, width=0.7, closePath=True)
    page.draw_line((60, 600), (540, 600), width=0)  # zero-width stroke
    doc[0].set_rotation(90)
    return _save(doc, path)


def odd_pagebox(path: str) -> str:
    """CropBox smaller than and offset within MediaBox."""
    doc = _new_doc()
    page = _new_page(doc)
    page.insert_text((100, 200), "odd page box content", fontsize=18, fontname=_FONT)
    page.set_cropbox(fitz.Rect(72, 72, 500, 700))
    return _save(doc, path)


def mixed_sizes(path: str) -> str:
    """Letter + A5 + A4-landscape in one document (per-page fit scales)."""
    doc = _new_doc()
    for w, h, label in [
        (LETTER.width, LETTER.height, "letter page"),
        (420, 595, "A5 page"),
        (842, 595, "A4 landscape page"),
    ]:
        page = doc.new_page(width=w, height=h)
        page.insert_font(fontname=_FONT, fontbuffer=_FONT_BUFFER)
        page.insert_text((40, 60), label, fontsize=20, fontname=_FONT)
        _fill_textbox(page, fitz.Rect(40, 80, w - 40, h - 40), (label + " body ").split() * 400, 10)
    return _save(doc, path)


def gray_ramp(path: str, steps: int = 11) -> str:
    """Grayscale step ramp — exercises the tone-hysteresis path (steps near
    the binarization threshold are legitimately ambiguous across engines)."""
    doc = _new_doc()
    page = _new_page(doc)
    w = 480.0 / steps
    for i in range(steps):
        g = i / (steps - 1)
        r = fitz.Rect(66 + i * w, 200, 66 + (i + 1) * w, 400)
        page.draw_rect(r, color=None, fill=(g, g, g))
    page.insert_text((66, 180), "gray ramp 0..1", fontsize=14, fontname=_FONT)
    return _save(doc, path)


def clipped_content(path: str, donor_pdf: str) -> str:
    """A real clip: another page shown through a small window (Form XObject
    with a clipping BBox)."""
    doc = _new_doc()
    page = _new_page(doc)
    page.insert_text((54, 70), "clipped window below", fontsize=18, fontname=_FONT)
    with fitz.open(donor_pdf) as donor:
        page.show_pdf_page(fitz.Rect(100, 120, 400, 420), donor, 0, clip=fitz.Rect(54, 54, 300, 300))
    return _save(doc, path)


def scan_1bit(path: str) -> str:
    """An embedded 1-bit bitonal image (the scanned-book case).

    Representative of real archival scans: 300 dpi with features >= 2 px at
    scan resolution (like the patent's own drawing sheets). Single-pixel
    features scaled non-integrally sit below the capture floor — the two
    verification engines phase their image resampling differently there, and
    no binarization policy can adjudicate them (measured)."""
    import io

    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("1", (2550, 3300), 1)  # 300 dpi letter, white
    d = ImageDraw.Draw(img)
    f = ImageFont.load_default(size=96)
    for k in range(18):
        d.text((120, 120 + k * 168), f"bitonal scan line {k:02d} lorem ipsum dolor", font=f, fill=0)
    for k in range(0, 2550, 12):
        d.line([(k, 3000), (k, 3200)], fill=0, width=2)  # 2-px hatch at 12-px pitch
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    doc = _new_doc()
    page = _new_page(doc)
    page.insert_image(fitz.Rect(36, 36, LETTER.width - 36, LETTER.height - 36), stream=buf.getvalue())
    return _save(doc, path)


def cmyk_image(path: str) -> str:
    """An embedded CMYK JPEG (color-management edge case)."""
    import io

    import numpy as np
    from PIL import Image

    yy, xx = np.mgrid[0:400, 0:600]
    c = ((xx / 600) * 255).astype(np.uint8)
    m = ((yy / 400) * 255).astype(np.uint8)
    y = ((1 - xx / 600) * 255).astype(np.uint8)
    k = np.minimum(c, m) // 3
    img = Image.merge("CMYK", [Image.fromarray(a) for a in (c, m, y, k)])
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    doc = _new_doc()
    page = _new_page(doc)
    page.insert_text((54, 70), "cmyk image below", fontsize=18, fontname=_FONT)
    page.insert_image(fitz.Rect(54, 100, 554, 433), stream=buf.getvalue())
    return _save(doc, path)


ALL = {
    "font_ladder": font_ladder,
    "stroke_wedge": stroke_wedge,
    "checkerboard": checkerboard,
    "text_page": text_page,
    "mixed_page": mixed_page,
    "pathological": pathological,
    "odd_pagebox": odd_pagebox,
    "mixed_sizes": mixed_sizes,
    "gray_ramp": gray_ramp,
    "scan_1bit": scan_1bit,
    "cmyk_image": cmyk_image,
}


def generate_all(directory: str) -> dict[str, str]:
    import os

    os.makedirs(directory, exist_ok=True)
    out = {name: fn(os.path.join(directory, f"{name}.pdf")) for name, fn in ALL.items()}
    out["clipped_content"] = clipped_content(
        os.path.join(directory, "clipped_content.pdf"), out["text_page"]
    )
    return out


def stress_corpus(path: str, n_pages: int) -> str:
    """One PDF with n unique dense mixed-content pages (capacity/perf tests)."""
    doc = _new_doc()
    for i in range(n_pages):
        page = _new_page(doc)
        page.insert_text((54, 60), f"STRESS PAGE {i + 1:05d}", fontsize=20, fontname=_FONT)
        body = f"page {i} entropy {(i * 2654435761) & 0xFFFFFFFF:x} " * 180
        _fill_textbox(page, fitz.Rect(54, 90, 558, 600), body.split(), 10)
        for k in range(i % 17):
            page.draw_line((60 + 28 * k, 620), (90 + 28 * k, 730), width=0.5 + (k % 5) * 0.4)
    return _save(doc, path)
