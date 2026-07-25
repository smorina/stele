"""Final-code-review fixes: plate-set status authority, halftone boundary
coverage, exact-tile cap, channel-transform validation, tone assessment
honesty, otsu-aware references, slot-containment audit."""

import numpy as np
import pytest

from stele.build import aggregate_status
from stele.config.profiles import ContentPolicy
from stele.ir.model import ImageRegion
from stele.passes.colorsep import ChannelTransform, check_triad_containment, triad_boxes
from stele.passes.halftone import dither_region, load_mask
from stele.verify.tone import image_tone_metrics


@pytest.fixture(scope="module")
def blue():
    return load_mask("bluenoise")


def _region(w_um: float, h_um: float, tone: float = 0.5) -> ImageRegion:
    v = np.full((64, 64, 1), 1.0 - tone, dtype=np.float32)
    rgba = np.concatenate([v, v, v, np.ones_like(v)], axis=-1)
    return ImageRegion(bbox_um=(0.0, 0.0, w_um, h_um), rgba=rgba, colorspace="gray")


# --------------- finding 1: aggregate status authority ---------------


def test_aggregate_status_second_plate_failure_fails_the_set():
    assert aggregate_status(["pass", "fail"]) == "fail"


def test_aggregate_status_unverified_never_a_pass():
    assert aggregate_status([None]) == "unverified"
    assert aggregate_status(["pass", None]) == "unverified"
    assert aggregate_status(["fail", None]) == "fail"


# --------------- finding 3: halftone boundary coverage ---------------


def test_partial_tiles_emitted_no_loss_no_overrun(blue):
    """A region whose site grid is NOT a tile multiple: geometry must cover
    the floor(extent/pitch) grid exactly — nothing dropped, nothing beyond
    the bbox (the old code lost up to 15 site rows/columns)."""
    pitch = 2.0
    reg = _region(2.0 * 90, 2.0 * 70)  # 90 x 70 sites: 16 | 90 and 16 | 70 fail
    geo = dither_region(reg, 1.0, pitch, blue)
    assert geo.n_sites == 90 * 70
    xs = [x for _, x, _ in geo.tile_placements]
    ys = [y for _, y, _ in geo.tile_placements]
    ws = {k: geo.tile_bitmaps[k].shape for k, _, _ in geo.tile_placements}
    x_max = max(x + ws[k][1] * pitch for k, x, _ in geo.tile_placements)
    y_max = max(y + ws[k][0] * pitch for k, _, y in geo.tile_placements)
    assert min(xs) == 0.0 and min(ys) == 0.0
    assert x_max == pytest.approx(90 * pitch)  # full grid covered ...
    assert y_max == pytest.approx(70 * pitch)
    assert x_max <= reg.bbox_um[2] + 1e-9  # ... and never past the bbox
    assert y_max <= reg.bbox_um[3] + 1e-9
    # mid-tone: emitted ink must track the tone over the WHOLE grid
    on = sum(geo.tile_bitmaps[k].sum() for k, _, _ in geo.tile_placements)
    assert abs(on / geo.n_sites - 0.5) < 0.03


def test_sub_tile_region_stays_inside_its_box(blue):
    """A region smaller than one 16-site tile must not emit geometry beyond
    its box (the old code padded it up to a full tile)."""
    pitch = 2.0
    reg = _region(2.0 * 10, 2.0 * 6)  # 10 x 6 sites
    geo = dither_region(reg, 1.0, pitch, blue)
    assert geo.n_sites == 10 * 6
    for k, x, y in geo.tile_placements:
        h, w = geo.tile_bitmaps[k].shape
        assert x + w * pitch <= reg.bbox_um[2] + 1e-9
        assert y + h * pitch <= reg.bbox_um[3] + 1e-9


def test_sub_site_region_skipped_and_reported(blue):
    geo = dither_region(_region(1.0, 40.0), 1.0, 2.0, blue)
    assert geo.skipped_sub_site
    assert not geo.tile_placements


def test_partial_tile_keys_encode_shape(blue):
    """Different-shaped bitmaps must never share a cell key."""
    reg = _region(2.0 * 33, 2.0 * 33, tone=0.5)  # 16+16+1 sites each way
    geo = dither_region(reg, 1.0, 2.0, blue)
    for k, _, _ in geo.tile_placements:
        h, w = geo.tile_bitmaps[k].shape
        assert k[:2] == bytes((h, w))


# --------------- finding 6: exact-tile cap ---------------


def test_exact_budget_caps_unique_exact_tiles(blue):
    rng = np.random.default_rng(3)
    noise = rng.random((256, 256, 1)).astype(np.float32)
    rgba = np.concatenate([noise, noise, noise, np.ones_like(noise)], axis=-1)
    reg = ImageRegion(bbox_um=(0, 0, 2.0 * 128, 2.0 * 128), rgba=rgba, colorspace="gray")
    budget = [5]
    geo = dither_region(reg, 1.0, 2.0, blue, exact_budget=budget)
    assert geo.n_exact_tiles == 5
    assert budget[0] == 0
    assert geo.n_budget_fallbacks > 0  # the rest degraded to quantized tiles
    # unbudgeted: same content yields far more exact patterns
    geo_free = dither_region(reg, 1.0, 2.0, blue)
    assert geo_free.n_exact_tiles > 5


# --------------- finding 4: channel transform validation ---------------


def test_channel_transforms_validated():
    base = {"name": "t", "image_mode": "dither", "color_mode": "rgb_triad"}
    with pytest.raises(ValueError, match="channels are R, G, B"):
        ContentPolicy(**base, channel_transforms={"X": {"dx_um": 1.0}})
    with pytest.raises(ValueError, match="unknown fields"):
        ContentPolicy(**base, channel_transforms={"R": {"dz_um": 1.0}})
    with pytest.raises(ValueError, match="scale"):
        ContentPolicy(**base, channel_transforms={"R": {"scale": 0.0}})
    with pytest.raises(ValueError, match="scale"):
        ContentPolicy(**base, channel_transforms={"R": {"scale": -1.0}})
    ok = ContentPolicy(**base, channel_transforms={"G": {"dx_um": 5000.0}})
    assert ok.channel_transforms["G"]["dx_um"] == 5000.0


def test_rgb_triad_requires_dither():
    with pytest.raises(ValueError, match="requires image_mode"):
        ContentPolicy(name="t", image_mode="threshold", color_mode="rgb_triad")


def test_triad_containment_rejects_frame_escape():
    xf = {"B": ChannelTransform(dx_um=100000.0)}
    boxes = triad_boxes((10000.0, 10000.0, 150000.0, 150000.0), xf)
    with pytest.raises(ValueError, match="outside the"):
        check_triad_containment((215900.0, 279400.0), boxes)
    # identity transforms on an in-frame region are fine
    check_triad_containment(
        (215900.0, 279400.0), triad_boxes((10000.0, 10000.0, 150000.0, 150000.0))
    )


# --------------- finding 3b: tone assessment honesty ---------------


def test_tone_zero_coverage_reports_not_assessed():
    cand = np.zeros((100, 100), dtype=np.uint8)
    ref = np.full((100, 100), 128, dtype=np.uint8)
    m = image_tone_metrics(cand, ref, [(0, 0, 20, 20)], superpixel_px=64)
    assert m["superpixels"] == 0
    assert m["assessed"] is False
    assert m["regions_skipped"] == 1


def test_tone_assessed_region_counted():
    cand = np.zeros((512, 512), dtype=np.uint8)
    cand[::2, :] = 255  # 50% coverage screen
    ref = np.full((512, 512), 128, dtype=np.uint8)  # 50% darkness
    m = image_tone_metrics(cand, ref, [(0, 0, 512, 512)], superpixel_px=32)
    assert m["assessed"] is True
    assert m["regions_assessed"] == 1
    assert m["pass"]
