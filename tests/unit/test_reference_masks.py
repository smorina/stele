from __future__ import annotations

import cv2
import numpy as np

from stele.verify.reference import _threshold_mask, _to_comparison_grid


def test_threshold_mask_applies_wider_image_band_only_inside_rectangles():
    gray = np.array(
        [
            [80, 100, 120, 140],
            [80, 100, 120, 140],
            [80, 100, 120, 140],
        ],
        dtype=np.uint8,
    )

    mask = _threshold_mask(gray, 96, [(1, 3, 1, 3)], 128)

    expected = gray < 96
    expected[1:3, 1:3] = gray[1:3, 1:3] < 128
    np.testing.assert_array_equal(mask, expected)


def test_comparison_grid_matches_previous_float_coverage_path():
    rng = np.random.default_rng(42)
    mask = rng.random((91, 73)) > 0.63

    actual = _to_comparison_grid(mask, 31, 27, coverage=0.55, upsampling=False)
    previous = (
        cv2.resize(mask.astype(np.float32), (31, 27), interpolation=cv2.INTER_AREA)
        >= 0.55
    )

    np.testing.assert_array_equal(actual, previous)


def test_comparison_grid_upsampling_remains_nearest_neighbor():
    mask = np.array([[False, True], [True, False]])

    actual = _to_comparison_grid(mask, 6, 4, coverage=0.5, upsampling=True)
    previous = (
        cv2.resize(mask.astype(np.uint8), (6, 4), interpolation=cv2.INTER_NEAREST) > 0
    )

    np.testing.assert_array_equal(actual, previous)
