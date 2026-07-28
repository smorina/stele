from stele.backends.preview import rasterize
from stele.layout.engine import PlatePlan, Rect
from stele.layout.fiducials import centered_orientation_glyph
from stele.verify import chirality


def test_chirality_renders_only_the_furniture_viewport(monkeypatch):
    rect = Rect(100.0, 200.0, 900.0, 1400.0)
    plan = PlatePlan(
        plate_w_um=152400.0,
        plate_h_um=152400.0,
        active=Rect(3000.0, 3000.0, 149400.0, 149400.0),
        reserved={"orientation_glyph": rect},
    )
    calls = []

    def render(layout, cell_name, bbox_um, px_per_um, layer, datatype):
        calls.append((cell_name, bbox_um, px_per_um, layer, datatype))
        return rasterize(
            centered_orientation_glyph(rect.as_tuple(), 1000.0),
            rect.as_tuple(),
            px_per_um,
            supersample=2,
        )

    monkeypatch.setattr(chirality, "rasterize_cell_hierarchical", render)
    monkeypatch.setattr(chirality, "top_transform_mirrored", lambda layout: False)

    result = chirality.check_chirality(
        object(), plan, glyph_height_um=1000.0, mirrored_declared=False
    )

    assert calls == [("FURNITURE", rect.as_tuple(), 1.0, 1, 0)]
    assert result["pass"]
    assert result["iou_declared"] == 1.0
