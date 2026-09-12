"""Translate the report's authoritative gate status without weakening it."""

from __future__ import annotations

from typing import Any

CANDIDATE_HANDOFF = (
    "Stele is a prototype. This is a software-verified candidate handoff, "
    "not fab-ready output. "
    "A mask shop must confirm its rule deck and exposure tone before fabrication."
)

STATUS = {
    "pass": {
        "headline": "Built and verified",
        "meaning": "Every software check passed.",
    },
    "pass_with_warnings": {
        "headline": "Built with warnings",
        "meaning": "Content checks passed, but at least one manufacturing or readability check needs attention.",
    },
    "fail": {
        "headline": "Failed verification",
        "meaning": "The output did not survive every required software check. Review the findings below.",
    },
    "unverified": {
        "headline": "Built, not checked",
        "meaning": "Verification was not run. Nothing was proved; this is not a pass.",
    },
}


def _stroke_context(report: dict[str, Any], plate: dict[str, Any]) -> str:
    """Why width/space violations usually appear on text plates: strokes below
    the writer minimum, i.e. small type at the plate's reduction."""
    pages = plate.get("verify", {}).get("pages") or {}
    mins = [
        p.get("stroke_widths_um", {}).get("min_um")
        for p in pages.values()
        if isinstance(p, dict) and isinstance(p.get("stroke_widths_um"), dict)
    ]
    mins = [m for m in mins if isinstance(m, (int, float))]
    fab = report.get("config", {}).get("profiles", {}).get("fab", {})
    floor = fab.get("min_feature_um")
    placements = plate.get("placements") or []
    scale = placements[0].get("scale") if placements else None
    if not mins or floor is None or not scale:
        return ""
    reduction = 1.0 / float(scale)
    pt = float(floor) * reduction / 25.0  # STROKE_UM_PER_PT heuristic (config.validate)
    return (
        f" The thinnest measured stroke is {min(mins):.2f} µm against the {float(floor):g} µm "
        f"writer minimum; at {reduction:.0f}:1 that is typical of text below about {pt:.0f} pt."
    )


def _font_substitution_note(plate: dict[str, Any]) -> str:
    """Comma-separated non-embedded font names on pages whose content check
    failed, or '' when none of the failing pages has such fonts."""
    pages = plate.get("verify", {}).get("pages") or {}
    failing = {
        cell
        for cell, result in pages.items()
        if isinstance(result, dict)
        and (result.get("structural_defects") or result.get("component_failures")
             or result.get("shape_mismatch"))
    }
    if not failing:
        return ""
    names: set[str] = set()
    ingest = plate.get("ingest") or []
    for placement, record in zip(plate.get("placements") or [], ingest):
        if placement.get("cell") in failing:
            names.update(record.get("fonts_not_embedded") or [])
    return ", ".join(sorted(names))


def summarize_report(report: dict[str, Any]) -> dict[str, Any]:
    status = report.get("plate_set", {}).get("status", "unverified")
    wording = STATUS.get(status, STATUS["unverified"])
    findings: list[dict[str, str]] = []

    for index, plate in enumerate(report.get("plates") or [], start=1):
        gates = plate.get("verify", {}).get("gates")
        if not isinstance(gates, dict):
            continue
        plate_finding_start = len(findings)
        where = f"Plate {index}: " if len(report.get("plates", [])) > 1 else ""
        mismatch = float(gates.get("worst_defect_mismatch", 0))
        if mismatch > 0.02:
            findings.append(
                {
                    "level": "error",
                    "text": f"{where}a page differs from its source by {mismatch:.1%}.",
                    "action": "Inspect the defect heatmap; unusual fonts or transparency may be involved.",
                }
            )
        structural = int(gates.get("structural_defects", 0))
        if structural:
            findings.append(
                {
                    "level": "error",
                    "text": f"{where}{structural} piece(s) of content are missing or extra.",
                    "action": "Do not fabricate this plate. Inspect the heatmap and report the build.",
                }
            )
        component = int(gates.get("component_failures", 0))
        if component:
            findings.append(
                {
                    "level": "error",
                    "text": f"{where}{component} shape(s) do not match the source closely enough.",
                    "action": "Inspect the defect heatmap before rebuilding.",
                }
            )
        elif gates.get("shape_mismatch"):
            findings.append(
                {
                    "level": "error",
                    "text": f"{where}at least one rendered shape does not match its source.",
                    "action": "Inspect the defect heatmap before rebuilding.",
                }
            )
        expected = int(gates.get("placements_expected", 0))
        found = int(gates.get("placements_found", 0))
        if expected != found:
            findings.append(
                {
                    "level": "error",
                    "text": f"{where}expected {expected} page placements but found {found}.",
                    "action": "This indicates a build defect; do not use the output.",
                }
            )
        if gates.get("chirality") is False:
            findings.append(
                {
                    "level": "error",
                    "text": f"{where}the plate handedness is wrong; it may be mirrored.",
                    "action": "Check the mirror setting and rebuild.",
                }
            )
        thin = gates.get("stroke_floor_failing_pages") or []
        if thin:
            findings.append(
                {
                    "level": "warning",
                    "text": f"{where}{len(thin)} page(s) have strokes thinner than the writer profile allows.",
                    "action": "Use a finer writer, enlarge the pseudopages, or confirm the limit with the shop.",
                }
            )
        width = int(gates.get("width_violations", 0))
        space = int(gates.get("space_violations", 0))
        if width or space:
            findings.append(
                {
                    "level": "warning",
                    "text": (
                        f"{where}{width} width and {space} spacing rule violation(s) were found."
                        + _stroke_context(report, plate)
                    ),
                    "action": (
                        "These are warnings under the current writer profile. Use a finer "
                        "writer, larger pages on glass, or confirm the rules with the shop."
                    ),
                }
            )
        tiers = gates.get("readability_failing_tiers") or []
        if tiers:
            findings.append(
                {
                    "level": "warning",
                    "text": f"{where}simulated text contrast is too low at {', '.join(tiers)}.",
                    "action": "Use a higher-NA reader or enlarge the content.",
                }
            )
        unassessed = int(gates.get("image_tone_unassessed_regions", 0))
        if unassessed:
            findings.append(
                {
                    "level": "warning",
                    "text": f"{where}{unassessed} small image region(s) could not be assessed for tone.",
                    "action": "Treat those regions as unverified, even though they did not fail.",
                }
            )
        tone_failures = gates.get("image_tone_failing_pages") or []
        if tone_failures:
            findings.append(
                {
                    "level": "error",
                    "text": f"{where}{len(tone_failures)} page(s) did not preserve image shading.",
                    "action": "Inspect those pages and adjust the halftone treatment before fabrication.",
                }
            )
        if gates.get("content_pass") is False and not any(
            item["level"] == "error" for item in findings[plate_finding_start:]
        ):
            findings.append(
                {
                    "level": "error",
                    "text": f"{where}a content, occupancy, or orientation check failed.",
                    "action": "Use the JSON report to identify the failed gate; do not use this output.",
                }
            )
        if gates.get("geometry_pass") is False and not (thin or width or space):
            findings.append(
                {
                    "level": "warning",
                    "text": f"{where}a file budget, density, or measured-geometry check failed.",
                    "action": "Review the plate checks in the report and confirm the vendor limits.",
                }
            )
        substituted = _font_substitution_note(plate)
        if substituted and (structural or component or gates.get("shape_mismatch")):
            findings.append(
                {
                    "level": "warning",
                    "text": (
                        f"{where}the differing page(s) use fonts that are not embedded in the "
                        f"PDF ({substituted}). The build engine and the independent reference "
                        f"engine substitute different fonts for these, so the differences above "
                        f"may be glyph-shape disagreements rather than lost content."
                    ),
                    "action": (
                        "Compare the heatmap against the page. For a clean check, re-export "
                        "the PDF with embedded fonts (PDF/A or 'print to PDF') and rebuild."
                    ),
                }
            )
        title = (plate.get("furniture") or {}).get("title") or {}
        if title.get("shrunk_to_fit"):
            findings.append(
                {
                    "level": "warning",
                    "text": (
                        f"{where}the title was etched at {title['height_um'] / 1000:.2f} mm "
                        f"instead of {title['requested_height_um'] / 1000:.2f} mm so it fits "
                        f"between the corner marks."
                    ),
                    "action": "Shorten the title or lower the title height if that matters.",
                }
            )

    for warning in report.get("validation", {}).get("warnings", []):
        findings.append(
            {
                "level": "warning",
                "text": str(warning),
                "action": "Review the assumption before sending the result to a mask shop.",
            }
        )

    if not findings and status == "pass":
        findings.append(
            {
                "level": "ok",
                "text": "All configured content, geometry, placement, chirality, budget, and readability checks passed.",
                "action": "Next, confirm the recorded manufacturing assumptions with a mask shop.",
            }
        )
    elif not findings and status == "unverified":
        findings.append(
            {
                "level": "warning",
                "text": "No verification results exist for this build.",
                "action": "Run verification before treating the manufacturing file as checked.",
            }
        )

    orientation = report.get("orientation", {})
    fonts: set[str] = set()
    for plate in report.get("plates") or []:
        for record in plate.get("ingest") or []:
            fonts.update(record.get("fonts_not_embedded") or [])
    return {
        "status": status,
        "headline": wording["headline"],
        "meaning": wording["meaning"],
        "candidate_handoff": CANDIDATE_HANDOFF,
        "findings": findings,
        "unenforced": report.get("validation", {}).get("unenforced_fields", []),
        "plates": report.get("plate_set", {}).get("plates", 0),
        "timings_s": report.get("timings_s", {}),
        "polarity": orientation.get("polarity", "clear_field"),
        "mirrored": bool(orientation.get("mirrored", False)),
        "tone_truth_table": orientation.get("tone_truth_table", []),
        "fonts_not_embedded": sorted(fonts),
        "furniture": (report.get("plates") or [{}])[0].get("furniture") or {},
    }
