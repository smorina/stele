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


def _plate_gates(report: dict[str, Any]) -> list[dict[str, Any]]:
    plates = report.get("plates") or []
    gates = [p.get("verify", {}).get("gates") for p in plates]
    return [g for g in gates if isinstance(g, dict)]


def summarize_report(report: dict[str, Any]) -> dict[str, Any]:
    status = report.get("plate_set", {}).get("status", "unverified")
    wording = STATUS.get(status, STATUS["unverified"])
    findings: list[dict[str, str]] = []

    for index, gates in enumerate(_plate_gates(report), start=1):
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
                    "text": f"{where}{width} width and {space} spacing rule violation(s) were found.",
                    "action": "Confirm the manufacturing rules or choose a coarser content treatment.",
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

    return {
        "status": status,
        "headline": wording["headline"],
        "meaning": wording["meaning"],
        "candidate_handoff": CANDIDATE_HANDOFF,
        "findings": findings,
        "unenforced": report.get("validation", {}).get("unenforced_fields", []),
        "plates": report.get("plate_set", {}).get("plates", 0),
        "timings_s": report.get("timings_s", {}),
    }
