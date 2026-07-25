import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "testdata", "gen"))

import synthetic


@pytest.fixture(scope="session")
def corpus_dir(tmp_path_factory) -> str:
    d = tmp_path_factory.mktemp("corpus")
    synthetic.generate_all(str(d))
    return str(d)


@pytest.fixture(scope="session")
def profiles_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "profiles")


def write_job(
    tmp_path,
    profiles_dir: str,
    pdf_path: str,
    pages: str = "all",
    gds_name: str = "plate.gds",
    layout_overrides: dict | None = None,
    content_overrides: dict | None = None,
    fab_overrides: dict | None = None,
) -> str:
    """Materialize a job manifest (and any overridden profiles) in tmp_path."""

    def prof(kind: str, filename: str, overrides: dict | None) -> str:
        src = os.path.join(profiles_dir, kind, filename)
        if not overrides:
            return os.path.abspath(src)
        with open(src) as f:
            data = yaml.safe_load(f)
        data.update(overrides)
        out = tmp_path / f"{kind}-override.yaml"
        out.write_text(yaml.safe_dump(data))
        return str(out)

    job = {
        "inputs": [{"path": os.path.abspath(pdf_path), "pages": pages}],
        "profiles": {
            "fab": prof("fab", "generic-laserwriter.yaml", fab_overrides),
            "reader": os.path.abspath(os.path.join(profiles_dir, "reader", "na025-580nm.yaml")),
            "content": prof("content", "default.yaml", content_overrides),
            "layout": prof("layout", "pseudopage-6in.yaml", layout_overrides),
        },
        "output": {"gds": str(tmp_path / gds_name), "plate_name": "TEST-0001"},
    }
    path = tmp_path / "job.yaml"
    path.write_text(yaml.safe_dump(job))
    return str(path)
