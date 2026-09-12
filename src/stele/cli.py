"""stele CLI: build | verify | validate | calc."""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys


def _plate_gds_candidates(base: str) -> list[str]:
    """Existing GDS files for a manifest output: the base path, or the
    .pNN-suffixed members of a multi-plate set."""
    if os.path.exists(base):
        return [base]
    root, ext = os.path.splitext(base)
    return sorted(glob.glob(f"{root}.p[0-9][0-9]{ext}"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stele", description="PDF -> photomask GDSII compiler")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="compile a job manifest into a plate GDS")
    p_build.add_argument("manifest")
    p_build.add_argument("--no-verify", action="store_true")
    p_build.add_argument("--no-preview", action="store_true")

    p_verify = sub.add_parser("verify", help="verify an existing plate GDS against its manifest")
    p_verify.add_argument("manifest")
    p_verify.add_argument("--gds", default=None, help="override GDS path (default: manifest output)")

    p_sim = sub.add_parser("simulate", help="through-the-reader view of a built plate region")
    p_sim.add_argument("manifest")
    p_sim.add_argument("--cell", default="PAGE_0000", help="page cell to view")
    p_sim.add_argument("--region", default=None,
                       help="x0,y0,w,h in page um (default: 400x300 um at page center)")
    p_sim.add_argument("--out", default=None, help="output PNG (default: <gds>.<cell>.sim.png)")

    p_val = sub.add_parser("validate", help="cross-profile validation of a job manifest")
    p_val.add_argument("manifest")

    p_calc = sub.add_parser("calc", help="capacity/resolution math for a job manifest")
    p_calc.add_argument("manifest")

    p_ui = sub.add_parser("ui", help="open the local browser interface")
    p_ui.add_argument("--port", type=int, default=0, help="localhost port (default: automatic)")
    p_ui.add_argument("--no-browser", action="store_true", help="print the URL without opening it")
    p_ui.add_argument("--home", default=None, help="run storage folder (default: ~/.stele)")

    p_doctor = sub.add_parser("doctor", help="check this installation and its GDS engines")
    p_doctor.add_argument("--json", action="store_true", help="print machine-readable JSON")
    p_doctor.add_argument("--home", default=None, help="run storage folder (default: ~/.stele)")

    args = parser.parse_args(argv)

    if args.cmd == "ui":
        from stele.ui import serve

        return serve(home=args.home, port=args.port, open_browser=not args.no_browser)

    if args.cmd == "doctor":
        from stele.doctor import format_doctor, run_doctor, write_doctor_json

        result = run_doctor(args.home)
        write_doctor_json(result, args.home)
        print(json.dumps(result, indent=2) if args.json else format_doctor(result))
        return 0 if result["ok"] else 1

    if args.cmd == "validate":
        from stele.config.manifest import load_manifest
        from stele.config.validate import validate_profiles

        _, profiles = load_manifest(args.manifest)
        result = validate_profiles(profiles)
        for line in result.info:
            print(f"info:    {line}")
        for line in result.warnings:
            print(f"WARNING: {line}")
        for line in result.errors:
            print(f"ERROR:   {line}")
        print("validation:", "OK" if result.ok else "FAILED")
        return 0 if result.ok else 1

    if args.cmd == "calc":
        from stele.config.manifest import load_manifest
        from stele.config.validate import validate_profiles
        from stele.layout.engine import plan_plate

        _, profiles = load_manifest(args.manifest)
        result = validate_profiles(profiles)
        plan = plan_plate(profiles.fab, profiles.layout, jobs=[])
        print(json.dumps(plan.summary(), indent=2))
        for line in result.info:
            print(f"info: {line}")
        return 0

    if args.cmd == "simulate":
        from stele.config.manifest import load_manifest
        from stele.verify.renderback import open_layout
        from stele.verify.simulate import default_region, simulate_region

        manifest, profiles = load_manifest(args.manifest)
        candidates = _plate_gds_candidates(manifest.output.gds)
        if not candidates:
            print(f"ERROR: no built GDS at {manifest.output.gds} (or .pNN plate set)",
                  file=sys.stderr)
            return 1
        layout = cell = None
        for path in candidates:  # multi-plate sets: find the plate holding the cell
            layout = open_layout(path)
            cell = layout.cell(args.cell)
            if cell is not None:
                break
        if cell is None:
            print(f"ERROR: cell {args.cell!r} not found in {', '.join(candidates)}",
                  file=sys.stderr)
            return 1
        if args.region:
            region = tuple(float(v) for v in args.region.split(","))
            if len(region) != 4:
                print("ERROR: --region takes x0,y0,w,h in page um", file=sys.stderr)
                return 1
        else:
            region = default_region(layout, args.cell)
        sim = simulate_region(
            layout, args.cell, region, profiles.reader,
            profiles.fab.layer, profiles.fab.datatype, polarity=profiles.layout.polarity,
        )
        out = args.out or f"{path}.{args.cell}.sim.png"
        with open(out, "wb") as f:
            f.write(sim["png"])
        print(f"simulated view ({profiles.reader.name}, x{profiles.reader.magnification:g}, "
              f"{profiles.layout.polarity}, eye-limited {sim['eye_um_per_px']:.2f} um/px on "
              f"plate): {out}")
        print(json.dumps(sim["contrast"], indent=1))
        crit = profiles.reader.contrast_criterion
        print(f"legible stroke bins (Michelson >= {crit}): {sim['legible_bins']}")
        # the sim is a gate, not just a demo: nothing legible -> nonzero exit
        print(f"readability: {'PASS' if sim['pass'] else 'FAIL'}")
        return 0 if sim["pass"] else 1

    if args.cmd == "verify":
        from stele.build import (
            _plate_gds_path,
            aggregate_status,
            page_cell_assignments,
            verify_plate,
        )
        from stele.config.manifest import load_manifest
        from stele.ingest.normalize import collect_pages
        from stele.ir.model import Orientation, Polarity
        from stele.layout.engine import plan_plates

        manifest, profiles = load_manifest(args.manifest)
        jobs = collect_pages(manifest.inputs)
        # the same planner as the build: multi-plate manifests verify every
        # .pNN member, not just a nonexistent base file (review finding 1)
        plans = plan_plates(profiles.fab, profiles.layout, jobs,
                            fit_mode=profiles.content.fit_mode)
        if args.gds and len(plans) > 1:
            print(f"ERROR: --gds is ambiguous for a {len(plans)}-plate set",
                  file=sys.stderr)
            return 2
        orientation = Orientation(
            polarity=Polarity(profiles.layout.polarity), mirrored=profiles.layout.mirrored
        )
        results = []
        for plan in plans:
            gds = args.gds or _plate_gds_path(manifest.output.gds, plan)
            result = verify_plate(
                manifest, profiles, plan, orientation, page_cell_assignments(plan),
                heatmap_dir=gds + ".defects", gds_path=gds,
            )
            if len(plans) > 1:
                print(f"--- plate {plan.plate_index + 1}/{plan.plate_count}: {gds} ---")
            _print_gates(result["gates"])
            for hm in result.get("heatmaps", []):
                print(f"heatmap: {hm}")
            results.append(result)
        overall = aggregate_status([r["gates"]["status"] for r in results])
        report = results[0] if len(results) == 1 else {"plates": results, "status": overall}
        report_path = manifest.output.gds + ".verify.json"
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"verify report: {report_path}")
        if len(results) > 1:
            print(f"plate set status: {overall.upper()}")
        return _exit_code(overall)

    if args.cmd == "build":
        from stele.build import build_job
        from stele.layout.engine import OverflowError_

        try:
            report = build_job(
                args.manifest, verify=not args.no_verify, preview=not args.no_preview
            )
        except OverflowError_ as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        plates = report["plates"]
        for p in plates:
            g = p["gds"]
            print(f"gds: {g['path']}  ({g['size_mb']} MB, {g['cells']} page cells, "
                  f"{g['polygons']} polygons, {g['references']} refs)")
        print(f"plan: {report['plan']['usable_slots']} usable slots "
              f"({report['plan']['theoretical_slots_pre_reservation']} theoretical), "
              f"{report['plan']['placed_pages']} pages placed"
              + (f" on plate 1 of {len(plates)}" if len(plates) > 1 else ""))
        for w in report["validation"]["warnings"]:
            print(f"WARNING: {w}")
        for i, p in enumerate(plates):
            if "verify" in p:
                if len(plates) > 1:
                    print(f"--- plate {i + 1}/{len(plates)} ---")
                _print_gates(p["verify"]["gates"])
                for hm in p["verify"].get("heatmaps", []):
                    print(f"heatmap: {hm}")
        print(f"report: {report['config']['manifest']['output']['report'] or report['gds']['path'] + '.report.json'}")
        print(f"timings: {report['timings_s']}")
        # the AGGREGATE status is authoritative — a failure on plate 2+ must
        # not exit zero, and --no-verify output is UNVERIFIED, never "pass"
        overall = report["plate_set"]["status"]
        if overall == "unverified":
            print("plate set status: UNVERIFIED (built with --no-verify)")
        elif len(plates) > 1 or "verify" not in plates[0]:
            print(f"plate set status: {overall.upper()}")
        return _exit_code(overall)

    return 0


def _print_gates(gates: dict) -> None:
    print(
        f"verify: defect mismatch {gates['worst_defect_mismatch']:.4%} | "
        f"structural defects {gates['structural_defects']} | "
        f"component failures {gates['component_failures']} | "
        f"chirality {'PASS' if gates['chirality'] else 'FAIL'} | "
        f"placements {gates['placements_found']}/{gates['placements_expected']}"
    )
    print(
        f"geometry: stroke-floor failing pages {len(gates['stroke_floor_failing_pages'])} | "
        f"width violations {gates['width_violations']} | "
        f"space violations {gates['space_violations']} "
        f"(severity: {gates['drc_severity']})"
    )
    if gates.get("readability_failing_tiers"):
        print(f"readability: FAILING tiers {gates['readability_failing_tiers']}")
    if gates.get("image_tone_unassessed_regions"):
        print(f"image tone: {gates['image_tone_unassessed_regions']} region(s) "
              f"too small to assess (unverified content)")
    print(f"verify status: {gates['status'].upper()}")


def _exit_code(status: str) -> int:
    # unverified exits 0: the user explicitly opted out with --no-verify,
    # and the printed status says exactly what they got
    return 0 if status in ("pass", "pass_with_warnings", "unverified") else 1


if __name__ == "__main__":
    raise SystemExit(main())
