"""Dither pass invariants (C1 design)."""

import numpy as np
import pytest

from stele.passes.dither import (
    TILE_SITES,
    bitmap_to_rects_um,
    clustered_mask,
    dither_sites,
    find_diagonal_contacts,
    patch_sites_for_contacts,
    repair_corner_contacts,
    thresholds_from_ranks,
    tile_pattern,
    void_and_cluster,
)


@pytest.fixture(scope="module")
def blue():
    import os

    path = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "bluenoise", "vnc64.npy")
    return thresholds_from_ranks(np.load(path))


def test_mask_is_a_permutation():
    rank = void_and_cluster(n=16, seed=7)
    assert sorted(rank.ravel().tolist()) == list(range(16 * 16))


def test_clustered_mask_permutation():
    m = clustered_mask(8)
    assert sorted(m.ravel().tolist()) == list(range(64))


def test_dither_coverage_tracks_tone(blue):
    for tone in (0.1, 0.5, 0.9):
        bm = dither_sites(np.full((128, 128), tone), blue)
        assert abs(bm.mean() - tone) < 0.01


def test_repair_removes_all_diagonal_contacts(blue):
    bm = dither_sites(np.full((128, 128), 0.5), blue)
    repaired = repair_corner_contacts(bm, blue)
    assert not find_diagonal_contacts(repaired)
    # tone preserved to first order
    assert abs(repaired.mean() - bm.mean()) < 0.01


def test_tile_pattern_deterministic(blue):
    a = tile_pattern(32, (16, 48), blue)
    b = tile_pattern(32, (16, 48), blue)
    assert np.array_equal(a, b)
    assert a.shape == (TILE_SITES, TILE_SITES)


def test_page_patches_clear_contacts(blue):
    # two abutting tiles of different tones -> boundary contacts possible
    page = np.zeros((32, 64), dtype=bool)
    page[:, :32] = tile_pattern(20, (0, 0), blue)[:32, :32] if TILE_SITES >= 32 else np.tile(
        tile_pattern(20, (0, 0), blue), (2, 2)
    )[:32, :32]
    page[:, 32:] = np.tile(tile_pattern(45, (0, 32 % blue.shape[0]), blue), (2, 2))[:32, :32]
    patch_sites_for_contacts(page, blue)
    assert not find_diagonal_contacts(page)


def test_rects_cover_bitmap_exactly(blue):
    bm = dither_sites(np.full((32, 32), 0.4), blue)
    rects = bitmap_to_rects_um(bm, pitch_um=2.0)
    area = sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in rects)
    assert area == pytest.approx(bm.sum() * 4.0)
    # reconstruct and compare
    back = np.zeros_like(bm)
    for x0, y0, x1, y1 in rects:
        c0, c1 = int(x0 / 2), int(x1 / 2)
        r1, r0 = 32 - int(y0 / 2), 32 - int(y1 / 2)
        back[r0:r1, c0:c1] = True
    assert np.array_equal(back, bm)
