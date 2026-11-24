"""``calibrate`` never writes inside the experiment folder (#486).

``check`` and ``run`` refuse an output folder inside the experiment (``output_inside_experiment``)
before they create it, take its lock or write a report. ``calibrate`` took ``--output`` as given,
so ``calibrate EXP --output EXP/ctrl/rep1/analysis`` made that folder in the experiment and
wrote ``calibration.csv`` there. It now refuses with the same code, before anything is created,
locked or read: against the experiment given, else the one the run in the output folder
recorded. The public writers take the experiment as ``experiment_root``.
"""

import json
import os

import numpy as np
import pytest

from morphobricks_preprocessing import (
    CALIBRATION_FILENAME,
    MANIFEST_FILENAME,
    Calibration,
    calibration_entry,
    read_calibration,
    write_calibration,
)
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.run.calibrate import update_calibration
from morphobricks_preprocessing.run.lock import LOCK_FILENAME
from morphobricks_preprocessing.testing import make_clean_run
from tiff_tree import write_data

SIZE = ["--size", ".", "X", "0.5", "um"]


def tree(root):
    """Every file and folder below ``root``, with each file's bytes."""
    found = []
    for current, folders, files in os.walk(root):
        for name in folders:
            found.append((os.path.relpath(os.path.join(current, name), root), None))
        for name in files:
            path = os.path.join(current, name)
            with open(path, "rb") as handle:
                found.append((os.path.relpath(path, root), handle.read()))
    return sorted(found, key=lambda item: item[0])


@pytest.fixture
def exp(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "ctrl/rep1/a.tif", np.arange(48, dtype=np.uint8).reshape(6, 8), "YX")
    return root


def inside_outputs(exp, tmp_path):
    link = tmp_path / "into_exp"
    link.symlink_to(exp / "ctrl", target_is_directory=True)
    return {
        "experiment": exp,
        "nested": exp / "ctrl/rep1/analysis",
        "condition": exp / "ctrl",
        "through a link": link / "analysis",
    }


@pytest.mark.parametrize("where", ["experiment", "nested", "condition", "through a link"])
@pytest.mark.parametrize("show", [False, True])
def test_calibrate_refuses_an_output_folder_inside_the_experiment(
    exp, tmp_path, capsys, where, show
):
    output = inside_outputs(exp, tmp_path)[where]
    before = tree(exp)
    assert main(["calibrate", str(exp), "--output", str(output), *([] if show else SIZE)]) == 1
    out = capsys.readouterr().out
    assert out.startswith("Error: The output folder is inside the experiment folder.")
    assert "Choose an output folder outside the experiment" in out
    assert tree(exp) == before  # no folder, lock or calibration.csv, every byte unchanged


def test_calibrate_writes_next_to_the_experiment(exp, tmp_path, capsys):
    output = tmp_path / "out"
    assert main(["calibrate", str(exp), "--output", str(output), *SIZE]) == 0
    assert read_calibration(output / CALIBRATION_FILENAME)[0].entries
    assert not (output / LOCK_FILENAME).exists()
    assert main(["calibrate", str(exp), *SIZE]) == 0  # the default output folder
    assert "Written to" in capsys.readouterr().out


def test_calibrate_allows_a_prefix_sibling_of_the_experiment(exp, tmp_path, capsys):
    # exp2/out shares only a name prefix with exp: it is outside the experiment.
    output = tmp_path / "exp2" / "out"
    assert main(["calibrate", str(exp), "--output", str(output), *SIZE]) == 0
    assert (output / CALIBRATION_FILENAME).is_file()
    assert "output folder is inside" not in capsys.readouterr().out


def unchecked_note(output):
    return (
        "Note: no experiment was given and none is recorded in "
        f"{output.resolve()}, so it was not checked that the output folder is outside the "
        "experiment (give the experiment folder to check it)."
    )


@pytest.mark.parametrize("show", [False, True])
def test_calibrate_says_when_it_could_not_check_the_output_folder(tmp_path, capsys, show):
    output = tmp_path / "out"  # no experiment given, and no run in it
    assert main(["calibrate", "--output", str(output), *([] if show else SIZE)]) == 0
    captured = capsys.readouterr()
    assert captured.err.strip() == unchecked_note(output)
    assert "Note:" not in captured.out
    assert (output / CALIBRATION_FILENAME).is_file() == (not show)


def test_calibrate_gives_no_note_when_the_experiment_is_given(exp, tmp_path, capsys):
    assert main(["calibrate", str(exp), "--output", str(tmp_path / "out"), *SIZE]) == 0
    assert main(["calibrate", str(exp), *SIZE]) == 0  # the default output folder
    assert capsys.readouterr().err == ""


def test_calibrate_gives_no_note_when_the_run_recorded_the_experiment(tmp_path, capsys):
    clean = make_clean_run(tmp_path)
    assert main(["calibrate", "--output", str(clean.output), *SIZE]) == 0
    assert capsys.readouterr().err == ""


@pytest.fixture
def recorded_inside(tmp_path):
    # A run whose output folder is inside the experiment its manifest records.
    clean = make_clean_run(tmp_path)
    path = clean.output / MANIFEST_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    data["experiment_root"] = str(clean.output.parent)
    path.write_text(json.dumps(data), encoding="utf-8")
    return clean


def test_calibrate_given_only_the_output_uses_the_recorded_experiment(recorded_inside, capsys):
    output = recorded_inside.output
    before = tree(output)
    assert main(["calibrate", "--output", str(output), *SIZE]) == 1
    assert "The output folder is inside the experiment folder." in capsys.readouterr().out
    assert tree(output) == before


def test_calibrate_given_only_the_output_of_a_run_elsewhere_writes(tmp_path, capsys):
    clean = make_clean_run(tmp_path)
    assert main(["calibrate", "--output", str(clean.output), *SIZE]) == 0
    assert (clean.output / CALIBRATION_FILENAME).is_file()


CALIBRATION = Calibration((calibration_entry(".", "X", 0.5, "um"),))


@pytest.mark.parametrize("write", ["write_calibration", "update_calibration"])
def test_the_writers_refuse_an_output_folder_inside_the_experiment(exp, write):
    output = exp / "ctrl/rep1/analysis"
    before = tree(exp)
    with pytest.raises(ValueError, match="output_inside_experiment") as raised:
        if write == "write_calibration":
            write_calibration(CALIBRATION, output, experiment_root=exp)
        else:
            update_calibration(output, lambda current: CALIBRATION, experiment_root=exp)
    assert raised.value.code == "output_inside_experiment"
    assert tree(exp) == before


@pytest.mark.parametrize("write", ["write_calibration", "update_calibration"])
def test_the_writers_refuse_inside_the_recorded_experiment(recorded_inside, write):
    output = recorded_inside.output
    with pytest.raises(ValueError, match="output_inside_experiment"):
        if write == "write_calibration":
            write_calibration(CALIBRATION, output)
        else:
            update_calibration(output, lambda current: CALIBRATION)
    assert not (output / CALIBRATION_FILENAME).exists()
