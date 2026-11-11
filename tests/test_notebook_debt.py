"""PP-10b: the notebook's UI modules show results; owner and pure modules compute them.

Left by the move-only split of PP-9 (plan §10): the cumulative share, the preview's value
range, the plane counts and the plane layouts were worked out in UI modules. Each now comes
from one owner function, so the notebook and its tests use the same maths.
"""

import numpy as np
import pytest

from morphobricks_preprocessing import prefill_mapping
from morphobricks_preprocessing.axes import AxisMapping
from morphobricks_preprocessing.notebook_values import plane_counts, value_range
from morphobricks_preprocessing.planes import plane_layout
from morphobricks_preprocessing.run.planned import MemoryPlan, memory_plan, plane_layouts
from tiff_tree import write_data

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")


def test_value_range_keeps_finite_values_only():
    assert value_range(np.array([[3, 7], [1, 9]], np.uint16)) == (1.0, 9.0)
    assert value_range(np.array([np.nan, -np.inf, 2.5, 4.0])) == (2.5, 4.0)
    assert value_range(np.array([np.nan, np.inf])) is None


def test_plane_counts_multiply_the_z_c_and_t_lengths():
    mapping = AxisMapping("TZCYX", ("T", "Z", "C", "Y", "X"))
    assert plane_counts(mapping, [(2, 3), (5, 5), (1, 2), (8, 8), (8, 8)]) == (10, 30)
    assert plane_counts(AxisMapping("YX", ("Y", "X")), [(8, 8), (8, 8)]) == (1, 1)
    assert plane_counts(AxisMapping("ZY", ("Z", "Y")), [(2, 2), (8, 8)]) is None


def test_plane_layouts_and_memory_plan(tmp_path):
    root = tmp_path / "exp"
    rng = np.random.default_rng(0)
    write_data(root / "big.tif", rng.integers(0, 4000, (2, 64, 64), np.uint16), axes="ZYX")
    write_data(root / "small.tif", rng.integers(0, 4000, (2, 8, 8), np.uint16), axes="ZYX")
    (root / "broken.tif").write_bytes(b"not an image")
    mapping = prefill_mapping("ZYX")
    small = plane_layout(root / "small.tif", mapping, bin_count=65_536).memory_estimate
    files = {"big.tif": mapping, "small.tif": mapping, "broken.tif": mapping}
    layouts = plane_layouts(root, files, memory_limit=small, bin_count=65_536)
    assert list(layouts) == ["big.tif", "small.tif"]  # an unreadable layout is left out
    plan = memory_plan(layouts)
    assert plan.blocked == ("big.tif",) and plan.over == () and plan.need == 0
    assert plan.largest == layouts["big.tif"].memory_estimate
    assert memory_plan({}) == MemoryPlan()


def test_distribution_png_uses_the_owner_cumulative_share(monkeypatch):
    from morphobricks_normalization.histogram import compute_plane_histogram
    from morphobricks_normalization.records import PlaneRecord

    from morphobricks_preprocessing import notebook_plots

    pixels = np.arange(64, dtype=np.uint8).reshape(8, 8)
    record = PlaneRecord(
        "c/r/t/a.tif",
        "c",
        "r",
        "t",
        histograms=(compute_plane_histogram(pixels, 256),),
        position_id="a",
        channels=("DNA",),
    )
    used = []
    real = notebook_plots.cumulative_share

    def spy(pooled):
        used.append(pooled)
        return real(pooled)

    monkeypatch.setattr(notebook_plots, "cumulative_share", spy)
    assert notebook_plots.distribution_png({("c",): [record]}) is not None
    assert len(used) == 1


def test_the_unused_pyramid_options_are_removed():
    from morphobricks_preprocessing import notebook_options, notebook_ui

    assert not hasattr(notebook_options, "PYRAMID_OPTIONS")
    assert "PYRAMID_OPTIONS" not in notebook_ui.__all__


def test_distribution_png_draws_one_panel_per_channel(monkeypatch):
    # Issue #104: the curves are drawn per channel, never from every channel pooled.
    from morphobricks_normalization.histogram import compute_plane_histogram
    from morphobricks_normalization.records import PlaneRecord

    from morphobricks_preprocessing import notebook_plots

    channels = (np.full((8, 8), 10, np.uint16), np.full((8, 8), 1000, np.uint16))
    record = PlaneRecord(
        "c/r/t/a.tif",
        "c",
        "r",
        "t",
        histograms=tuple(compute_plane_histogram(p, 16) for p in channels),
        position_id="a",
        channels=("C0", "C1"),
    )
    used = []
    real = notebook_plots.cumulative_share

    def spy(pooled):
        used.append(pooled)
        return real(pooled)

    monkeypatch.setattr(notebook_plots, "cumulative_share", spy)
    assert notebook_plots.distribution_png({("c",): [record]}) is not None
    assert [(p.minimum, p.maximum, p.pixel_count) for p in used] == [(10, 10, 64), (1000, 1000, 64)]
