"""Installation diagnostics shared by the CLI and local UI."""

from __future__ import annotations

import importlib
import json
import os
import platform
import shutil
import sys
import tempfile
from importlib import metadata
from pathlib import Path
from typing import Any

from stele import __version__

DEPENDENCIES = {
    "gdstk": "gdstk",
    "PyMuPDF": "fitz",
    "pypdfium2": "pypdfium2",
    "opencv-python-headless": "cv2",
    "shapely": "shapely",
    "numpy": "numpy",
    "Pillow": "PIL",
    "pydantic": "pydantic",
    "PyYAML": "yaml",
    "klayout": "klayout.db",
}


def default_home() -> Path:
    configured = os.environ.get("STELE_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".stele"


def _memory_total_bytes() -> int | None:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages * page_size)
    except (AttributeError, OSError, ValueError):
        return None


def run_doctor(home: str | Path | None = None) -> dict[str, Any]:
    target = Path(home) if home is not None else default_home()
    checks: list[dict[str, Any]] = []

    python_ok = sys.version_info[:2] == (3, 12)
    checks.append(
        {
            "name": "Python",
            "ok": python_ok,
            "detail": f"{platform.python_version()} at {sys.executable}",
            "fix": "Stele requires its managed Python 3.12 environment." if not python_ok else "",
        }
    )

    for distribution, module in DEPENDENCIES.items():
        try:
            imported = importlib.import_module(module)
            version = metadata.version(distribution)
            checks.append(
                {
                    "name": distribution,
                    "ok": True,
                    "detail": f"{version} · {getattr(imported, '__file__', 'built-in')}",
                    "fix": "",
                }
            )
        except Exception as exc:
            checks.append(
                {
                    "name": distribution,
                    "ok": False,
                    "detail": f"{type(exc).__name__}: {exc}",
                    "fix": "Replace Stele with a fresh copy of the same release.",
                }
            )

    try:
        target.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target, prefix=".doctor-", delete=True):
            pass
        writable = True
        write_detail = str(target.resolve())
    except OSError as exc:
        writable = False
        write_detail = str(exc)
    checks.append(
        {
            "name": "Run storage",
            "ok": writable,
            "detail": write_detail,
            "fix": "Choose a writable STELE_HOME folder." if not writable else "",
        }
    )

    try:
        from stele.passes.halftone import load_mask, mask_asset_sha256

        mask = load_mask("bluenoise")
        asset_ok = mask.shape == (64, 64)
        asset_detail = f"64×64 · SHA-256 {mask_asset_sha256('bluenoise')[:16]}…"
    except Exception as exc:
        asset_ok = False
        asset_detail = f"{type(exc).__name__}: {exc}"
    checks.append(
        {
            "name": "Blue-noise asset",
            "ok": asset_ok,
            "detail": asset_detail,
            "fix": "Replace Stele; a packaged data file is missing." if not asset_ok else "",
        }
    )

    try:
        import gdstk
        import klayout.db as kdb

        with tempfile.TemporaryDirectory(prefix="stele-doctor-") as tmp:
            path = str(Path(tmp) / "roundtrip.gds")
            lib = gdstk.Library()
            cell = lib.new_cell("DOCTOR")
            cell.add(gdstk.rectangle((0, 0), (10, 10), layer=1))
            lib.write_gds(path)
            layout = kdb.Layout()
            layout.read(path)
            roundtrip_ok = layout.cell("DOCTOR") is not None
        roundtrip_detail = "gdstk write → KLayout read succeeded"
    except Exception as exc:
        roundtrip_ok = False
        roundtrip_detail = f"{type(exc).__name__}: {exc}"
    checks.append(
        {
            "name": "Independent GDS round trip",
            "ok": roundtrip_ok,
            "detail": roundtrip_detail,
            "fix": "Replace Stele; the bundled GDS engines cannot interoperate."
            if not roundtrip_ok
            else "",
        }
    )

    disk = shutil.disk_usage(target if target.exists() else target.parent)
    total_memory = _memory_total_bytes()
    return {
        "ok": all(check["ok"] for check in checks),
        "stele_version": __version__,
        "maturity": "prototype",
        "platform": f"{platform.system()} {platform.release()} · {platform.machine()}",
        "checks": checks,
        "resources": {
            "disk_free_bytes": disk.free,
            "disk_total_bytes": disk.total,
            "memory_total_bytes": total_memory,
        },
    }


def format_doctor(result: dict[str, Any]) -> str:
    lines = [
        f"Stele {result['stele_version']} prototype diagnostics",
        result["platform"],
        "",
    ]
    for check in result["checks"]:
        lines.append(f"{'PASS' if check['ok'] else 'FAIL'}  {check['name']}: {check['detail']}")
        if check.get("fix"):
            lines.append(f"      Fix: {check['fix']}")
    free = result["resources"]["disk_free_bytes"] / 1e9
    lines.extend(["", f"Free disk for runs: {free:.1f} GB", f"Overall: {'READY' if result['ok'] else 'NEEDS ATTENTION'}"])
    return "\n".join(lines)


def write_doctor_json(result: dict[str, Any], home: str | Path | None = None) -> Path:
    target = Path(home) if home is not None else default_home()
    target.mkdir(parents=True, exist_ok=True)
    path = target / "doctor.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    return path
