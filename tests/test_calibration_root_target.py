"""A calibration target such as ``/`` is refused, never the whole experiment (issue #390).

Owner decision of 24 November 2026: refuse. ``"."`` (or ``"./"``) is the whole experiment's
only spelling; ``/``, ``//``, ``\\`` and every other target that a trailing-separator trim
would make ``"."`` are refused with ``calibration_target_root`` in the library, in
``calibration.csv``, by the ``calibrate`` command and by the notebook's calibration card.
"""

import numpy as np
import pytest

from described_tree import run_described
from morphobricks_preprocessing import (
    CALIBRATION_FILENAME,
    CalibrationFormatError,
    calibration_entry,
    read_calibration,
)
from morphobricks_preprocessing.calibration import (
    EXPERIMENT,
    Calibration,
    CalibrationTargetRootError,
)
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.testing import make_clean_run

ROOTS = ["/", "//", "////", "\\", "\\\\", "/\\", ".//", "./\\", "././", ".\\"]
"""Targets that the trim of trailing separators would read as the whole experiment."""


@pytest.mark.parametrize("target", ROOTS)
def test_a_root_target_is_refused_with_its_code(target):
    with pytest.raises(CalibrationTargetRootError) as found:
        calibration_entry(target, "X", 0.5, "um")
    assert found.value.code == "calibration_target_root"
    assert found.value.target == target
    message = str(found.value)
    assert repr(target) in message and "calibration_target_root" in message
    assert "only the target ." in message  # what to give for the whole experiment
    assert isinstance(found.value, ValueError)  # every entry already refuses a ValueError
    with pytest.raises(CalibrationTargetRootError):
        calibration_entry(target, "Z", None, "pixels")
    with pytest.raises(CalibrationTargetRootError):
        Calibration().without(target, ["X"])


@pytest.mark.parametrize("target", [".", "./"])
def test_the_whole_experiment_and_a_folder_are_still_stated(target):
    assert calibration_entry(target, "X", 0.5, "um").target == EXPERIMENT
    assert calibration_entry("ctrl\\rep1/", "X", 0.5, "um").target == "ctrl/rep1"
    assert calibration_entry("./ctrl", "Y", None, "pixels").target == "ctrl"


def write_lines(output, text):
    output.mkdir(parents=True, exist_ok=True)
    path = output / CALIBRATION_FILENAME
    path.write_text("target,axis,size,unit\n" + text, encoding="utf-8")
    return path


@pytest.mark.parametrize("target", ["/", "//", "\\"])
def test_a_root_line_of_the_file_is_refused_naming_the_line(tmp_path, target):
    path = write_lines(tmp_path, f".,Y,0.5,um\n{target},X,0.5,um\n")
    with pytest.raises(CalibrationFormatError, match="line 3: .*calibration_target_root"):
        read_calibration(path)


def test_a_run_with_a_root_line_stops_and_publishes_nothing(tmp_path):
    clean = make_clean_run(tmp_path, axes="YX", shape=(16, 20), pixel_size=0.5)
    table = clean.result.csv_path.read_bytes()
    manifest = (clean.output / "run_manifest.json").read_bytes()
    before = write_lines(clean.output, "/,X,0.25,um\n").read_bytes()
    result = run_described(output_folder=clean.output)
    assert not result.written
    (found,) = [i for i in result.issues if i.code == "calibration_unreadable"]
    assert found.is_error and "line 2" in found.message
    assert "calibration_target_root" in found.message
    assert (clean.output / CALIBRATION_FILENAME).read_bytes() == before
    assert clean.result.csv_path.read_bytes() == table
    assert (clean.output / "run_manifest.json").read_bytes() == manifest


@pytest.mark.parametrize("option", ["--size", "--pixels", "--remove"])
@pytest.mark.parametrize("target", ["/", "//", "\\"])
def test_the_calibrate_command_refuses_a_root_target(tmp_path, capsys, option, target):
    output = tmp_path / "out"
    output.mkdir()
    extra = ["0.5", "um"] if option == "--size" else []
    assert main(["calibrate", "--output", str(output), option, target, "XY", *extra]) == 1
    printed = capsys.readouterr().out
    assert "Nothing was changed" in printed and "calibration_target_root" in printed
    assert not (output / CALIBRATION_FILENAME).exists()


def test_the_calibrate_command_still_states_the_experiment_and_a_folder(tmp_path, capsys):
    output = tmp_path / "out"
    output.mkdir()
    command = ["calibrate", "--output", str(output), "--size", ".", "XY", "0.5", "um"]
    assert main([*command, "--size", "ctrl", "Z", "2", "um"]) == 0
    text = (output / CALIBRATION_FILENAME).read_text(encoding="utf-8")
    assert ".,X,0.5,micrometer" in text and "ctrl,Z,2.0,micrometer" in text


# The notebook's calibration card -------------------------------------------------------------


@pytest.fixture
def notebook(tmp_path):
    """A checked notebook of a described two-condition experiment, with its output folder."""
    pytest.importorskip("ipywidgets")
    pytest.importorskip("matplotlib")
    from described_tree import describe_tree
    from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook
    from tiff_tree import write_data

    root, output = tmp_path / "exp", tmp_path / "out"
    rng = np.random.default_rng(5)
    for rel in ("ctrl/rep1/a.tif", "drug/rep1/a.tif"):
        write_data(root / rel, rng.integers(0, 99, (2, 10, 10), dtype=np.uint8), axes="CYX")
    describe_tree(root, str(output))
    nb = PreprocessingNotebook(str(root), output_folder=str(output))
    for step in (nb.welcome, nb.folder_step, nb.axis_step, nb.run_step, nb.results_step):
        step()
    nb.check_button.click()
    assert nb.preflight is not None
    return nb, output / CALIBRATION_FILENAME


@pytest.mark.parametrize("target", ["/", "//", "\\"])
def test_the_notebook_card_refuses_a_root_target(notebook, target):
    nb, path = notebook
    nb.calibration_target.value = target
    nb.calibration_axes.value = "XY"
    nb.calibration_size.value = "0.5"
    nb.calibration_state.click()
    message = nb.calibration_message.value
    assert "Nothing was changed" in message and "calibration_target_root" in message
    assert not path.exists()
    nb.calibration_remove.click()
    assert "calibration_target_root" in nb.calibration_message.value


def test_the_notebook_card_still_states_the_experiment_and_a_folder(notebook):
    nb, path = notebook
    nb.calibration_axes.value = "XY"
    nb.calibration_size.value = "0.5"
    for target in (".", "ctrl"):
        nb.calibration_target.value = target
        nb.calibration_state.click()
        assert "Saved in" in nb.calibration_message.value
    text = path.read_text(encoding="utf-8")
    assert ".,X,0.5,micrometer" in text and "ctrl,Y,0.5,micrometer" in text
