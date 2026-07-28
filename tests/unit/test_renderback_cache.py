from __future__ import annotations

import gdstk

from stele.verify.renderback import open_layout, rasterize_cell_hierarchical


def test_render_cache_retains_shared_tiles_but_not_page_specific_cells(tmp_path):
    path = tmp_path / "cache-policy.gds"
    lib = gdstk.Library(unit=1e-6, precision=1e-9)
    tile = lib.new_cell("HT_SHARED")
    tile.add(gdstk.rectangle((0, 0), (4, 4), layer=1))
    patch = lib.new_cell("PAGE_0000_PATCH")
    patch.add(gdstk.rectangle((10, 10), (14, 14), layer=1))
    page = lib.new_cell("PAGE_0000")
    page.add(gdstk.Reference(tile), gdstk.Reference(tile, (5, 0)), gdstk.Reference(patch))
    lib.write_gds(path)

    layout = open_layout(str(path))
    cache = {}
    rendered = rasterize_cell_hierarchical(
        layout, "PAGE_0000", (0, 0, 20, 20), 2.0, _cache=cache
    )

    tile_index = layout.cell("HT_SHARED").cell_index()
    patch_index = layout.cell("PAGE_0000_PATCH").cell_index()
    assert rendered.sum() > 0
    assert (tile_index, 2.0) in cache
    assert (patch_index, 2.0) not in cache
