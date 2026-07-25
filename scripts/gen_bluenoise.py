"""Generate and commit the blue-noise threshold mask asset.

Run: uv run python scripts/gen_bluenoise.py
Regenerating changes every tone-tile cell key — explicit, reviewed action.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np

from stele.passes.dither import void_and_cluster

OUT = os.path.join(os.path.dirname(__file__), "..", "assets", "bluenoise", "vnc64.npy")

rank = void_and_cluster(n=64, sigma=1.9, seed=12345)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
np.save(OUT, rank)

# quality report: radially averaged power spectrum of the 50% pattern
pattern = rank < (rank.size // 2)
f = np.fft.fftshift(np.abs(np.fft.fft2(pattern - pattern.mean())) ** 2)
n = rank.shape[0]
yy, xx = np.mgrid[0:n, 0:n]
r = np.hypot(yy - n // 2, xx - n // 2).astype(int)
radial = np.bincount(r.ravel(), f.ravel()) / np.maximum(np.bincount(r.ravel()), 1)
low = radial[1 : n // 8].mean()
ring = radial[n // 4 : n // 2].mean()
print(f"wrote {OUT}")
print(f"spectrum: low-freq {low:.1f} vs principal ring {ring:.1f} (ratio {low/ring:.3f} — want << 1)")
