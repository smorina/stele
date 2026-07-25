"""Generate the stress-corpus PDF referenced by jobs/stress_plate.yaml.

Run: uv run python scripts/gen_stress.py [n_pages]   (default 600)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "testdata", "gen"))

import synthetic

n = int(sys.argv[1]) if len(sys.argv) > 1 else 600
out = os.path.join(os.path.dirname(__file__), "..", "out", "stress.pdf")
os.makedirs(os.path.dirname(out), exist_ok=True)
synthetic.stress_corpus(out, n)
print(f"wrote {out} ({n} unique pages)")
