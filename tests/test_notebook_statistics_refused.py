"""Refused percentiles hide the statistics calculated before (#527).

The old table, tiles and plot were calculated with other percentiles: they must not stay on
screen under the refused choice. The widgets are driven headless.
"""

import numpy as np
import pytest

from test_notebook_ui import confirmed
from tiff_tree import write_data

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402


def test_refused_percentiles_hide_the_earlier_statistics(tmp_path):
    root = tmp_path / "exp"
    rng = np.random.default_rng(0)
    for rel in ["ctrl/rep1/a.tif", "ctrl/rep1/b.tif", "drug/rep1/a.tif"]:
        write_data(root / rel, rng.integers(0, 200, (3, 2, 16, 16), dtype=np.uint8), axes="TCYX")
    nb = confirmed(root, output_folder=str(tmp_path / "out"))
    assert nb.run().written
    nb.percentiles.value = "1, 99.8"
    nb.statistics_button.click()
    assert ui.is_visible(nb.statistics_card) and nb.stats_tiles.value
    for refused in ("1,120", "nan", "1, inf", "invalid"):
        nb.percentiles.value = refused
        nb.statistics_button.click()
        assert "The percentiles cannot be read." in nb.stats_status.value
        assert not ui.is_visible(nb.statistics_card) and not ui.is_visible(nb.distribution_card)
        assert nb.statistics.value == nb.stats_tiles.value == nb.stats_next.value == ""
    nb.percentiles.value = "1, 99.8"
    nb.statistics_button.click()
    assert ui.is_visible(nb.statistics_card) and nb.statistics.value.count("<td>ctrl</td>") == 2
