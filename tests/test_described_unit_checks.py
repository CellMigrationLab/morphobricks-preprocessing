"""A described run checks pixel types and dimensions per unit (issue #239, narrowed).

The Spec still refuses RGB and fluorescence units in one description in Stages 1-2
(``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED``, experiment-v1 sections 4.4b and 8, fixture v29). What
changes is which refusal the user meets: not ``mixed_dtypes`` and ``inconsistent_dimensions``
from comparing an RGB image with a fluorescence one, but the Spec's own. A unit mixed inside
itself and an undescribed (v0.4) run are refused as before.
"""

from pathlib import Path

import numpy as np
import pytest
import tifffile

from channel_images import write_channels, write_rgb
from description_tree import MAPPING, build_experiment
from morphobricks_preprocessing import preflight, prefill_mapping, run_experiment
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_candidate import check_candidate
from morphobricks_preprocessing.notebook_check_view import checks_html
from morphobricks_preprocessing.unit_consistency import unit_checked
from spec_pin import fixtures

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}
LAYOUT = "R_HETEROGENEOUS_LAYOUT_NOT_ENABLED"
FOLDER_CODES = {"mixed_dtypes", "inconsistent_dimensions"}


def codes(issues):
    return {i.code for i in issues}


def mixed_experiment(tmp_path):
    """The Spec's v29 shape: an RGB (uint8) unit and a two-channel (uint16) unit, published."""
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir()
    for index in range(3):
        write_rgb(root / "CTRL/rep1/brightfield" / f"b{index}.tif", seed=index + 1)
        write_channels(
            root / "CTRL/rep1/fluorescence" / f"f{index}.tif",
            ["DNA", "ACTIN"],
            ["blobs", "filaments"],
            seed=index + 1,
        )
    return root, out


def publish(out, name="v29"):
    (out / "morphobricks.toml").write_bytes(VALID[name].data)


@pytest.mark.filterwarnings("ignore:No experiment description")
def test_the_folder_check_of_a_mixed_experiment_is_refused_without_a_description(tmp_path):
    root, out = mixed_experiment(tmp_path)
    pre = preflight(root, out)
    assert FOLDER_CODES <= codes(pre.issues)
    assert not pre.ok
    result = run_experiment(root, MAPPING, output_folder=out)  # no published file: v0.4 run
    assert not result.written
    assert "mixed_dtypes" in codes(result.issues)
    assert LAYOUT not in codes(result.issues)


def test_a_described_run_of_a_mixed_experiment_stops_with_the_spec_refusal(tmp_path):
    root, out = mixed_experiment(tmp_path)
    publish(out)
    result = run_experiment(root, MAPPING, output_folder=out)
    assert not result.written
    assert LAYOUT in codes(result.issues)
    assert not FOLDER_CODES & codes(result.issues)
    assert not (out / "plane_histograms.csv").exists()


def test_the_described_folder_check_of_a_mixed_experiment_passes(tmp_path):
    root, out = mixed_experiment(tmp_path)
    pre = unit_checked(preflight(root, out))
    assert pre.ok, [i.message for i in pre.issues if i.is_error]
    assert not FOLDER_CODES & codes(pre.issues)


def test_the_check_and_save_screen_explains_the_refusal_in_words(tmp_path):
    root, out = mixed_experiment(tmp_path)
    pre = unit_checked(preflight(root, out))
    check = check_candidate(description_from_bytes(VALID["v29"].data), pre, None, out)
    assert check.can_save
    assert check.heterogeneous is not None and not check.can_run
    text = checks_html(check)
    assert "2 channel layouts" in text
    assert "R, G, B, rgb" in text and "DNA, ACTIN" in text
    assert "this version cannot run it" in text
    assert "mixed_dtypes" not in text


def _mix_inside_unit(root):
    """Replace one image of the fluorescence unit by a uint8 one: the unit has two pixel types."""
    path = Path(root) / "CTRL/rep1/fluorescence/f0.tif"
    path.unlink()
    tifffile.imwrite(path, np.full((2, 8, 8), 7, np.uint8), photometric="minisblack")


def test_a_unit_mixed_inside_itself_is_refused_in_a_described_run(tmp_path):
    root, out = mixed_experiment(tmp_path)
    publish(out)
    _mix_inside_unit(root)
    result = run_experiment(root, MAPPING, output_folder=out)
    assert not result.written
    assert {"mixed_dtypes", LAYOUT} <= codes(result.issues)
    pre = unit_checked(preflight(root, out))
    assert not pre.ok
    (issue,) = [i for i in pre.issues if i.code == "mixed_dtypes"]
    assert all(p.startswith("CTRL/rep1/fluorescence/") for p in issue.paths)


def test_a_unit_mixed_inside_itself_is_refused_by_the_run_of_one_image_type(tmp_path):
    description = description_from_bytes(VALID["v01"].data)
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir()
    (out / "morphobricks.toml").write_bytes(VALID["v01"].data)
    images = build_experiment(root, description, images=2)
    tifffile.imwrite(root / images[0], np.zeros((2, 8, 8), np.uint16), photometric="minisblack")
    result = run_experiment(root, MAPPING, output_folder=out)
    assert not result.written
    assert "mixed_dtypes" in codes(result.issues)
    assert "mixed_dtypes" in codes(unit_checked(preflight(root, out)).issues)


def test_units_of_one_image_type_are_still_compared_with_each_other(tmp_path):
    """Two RGB units of different pixel types: one layout, so the pixel types still refuse."""
    text = VALID["v29"].data.decode("utf-8").replace(
        'channels = ["DNA", "ACTIN"]', 'channels = ["R", "G", "B"]\nimage_type = "rgb"'
    )
    root, out = mixed_experiment(tmp_path)
    for path in (root / "CTRL/rep1/fluorescence").glob("*.tif"):
        path.unlink()
        tifffile.imwrite(path, np.full((8, 8, 3), 700, np.uint16), photometric="rgb")
    (out / "morphobricks.toml").write_text(text, encoding="utf-8")
    result = run_experiment(root, prefill_mapping("YXS"), output_folder=out)
    assert "mixed_dtypes" in codes(result.issues)
    assert LAYOUT not in codes(result.issues)


def _checked_notebook(root, out, *, describe):
    pytest.importorskip("ipywidgets")
    pytest.importorskip("matplotlib")
    from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook

    nb = PreprocessingNotebook(str(root), output_folder=str(out), describe=describe)
    nb.welcome(), nb.folder_step()
    nb.check_button.click()
    return nb


def test_the_described_notebook_folder_check_reaches_the_screens(tmp_path):
    root, out = mixed_experiment(tmp_path)
    nb = _checked_notebook(root, out, describe=True)
    assert nb.preflight.ok, [i.code for i in nb.preflight.issues if i.is_error]
    assert not FOLDER_CODES & codes(nb._step1_issues())


def test_the_undescribed_notebook_keeps_the_folder_refusal(tmp_path):
    root, out = mixed_experiment(tmp_path)
    nb = _checked_notebook(root, out, describe=False)
    assert not nb.preflight.ok
    assert FOLDER_CODES <= codes(nb._step1_issues())
