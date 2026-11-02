"""Every command exits with status 1 when it reports an error, and 0 otherwise.

Found in the review of #130: ``check`` printed a refused experiment design (an error) and
exited 0, while ``run`` stops on it with 1. The same mismatch was in ``design``, which
printed ``repeat_group_conflict`` and exited 0. Warnings alone never change the status.
"""

import numpy as np
import pytest

from morphobricks_preprocessing.cli import main
from tiff_tree import write_data

DESIGN = "experiment_design.csv"


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    rels = ["ctrl/day1/a.tif", "ctrl/day2/a.tif", "drug/day1/a.tif", "drug/day2/a.tif"]
    for seed, rel in enumerate(rels):
        data = np.random.default_rng(seed).integers(0, 200, (6, 8), dtype=np.uint8)
        write_data(root / rel, data, "YX")
    return root


@pytest.fixture
def checked(experiment, tmp_path):
    """The output folder of a first 'check', which writes the design template (status 0)."""
    output = tmp_path / "checked"
    assert main(["check", str(experiment), "--output", str(output)]) == 0
    assert (output / DESIGN).is_file()
    return output


def test_check_exits_with_1_on_a_refused_design(experiment, checked, capsys):
    path = checked / DESIGN
    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines[0].split(",")
    path.write_text(",".join([*header, header[0]]) + "\n" + "\n".join(lines[1:]) + "\n")
    before = path.read_bytes()
    capsys.readouterr()
    assert main(["check", str(experiment), "--output", str(checked)]) == 1
    out = capsys.readouterr().out
    assert "Experiment design: The experiment design cannot be read" in out
    assert "names the column 'condition' twice" in out
    assert path.read_bytes() == before  # 'check' never replaces a design
    # 'run' stops on the same design with 1
    assert main(["run", str(experiment), "--output", str(checked), "--axes", "YX"]) == 1


def test_check_exits_with_0_on_warnings_and_an_unconfirmed_design(experiment, checked, capsys):
    capsys.readouterr()
    assert main(["check", str(experiment), "--output", str(checked)]) == 0
    assert "not confirmed" in capsys.readouterr().out


def test_design_exits_with_1_when_it_reports_a_conflict(checked, capsys):
    capsys.readouterr()
    assert main(["design", "--output", str(checked), "--repeat-group", "ctrl/day2=day1"]) == 1
    out = capsys.readouterr().out
    assert "Error: A repeat group is one biological repeat" in out
    assert "day1 has more than one folder of ctrl" in out
    # the move itself is written; undoing it gives a usable design again (status 0)
    assert main(["design", "--output", str(checked), "--repeat-group", "ctrl/day2=day2"]) == 0


def test_check_exits_with_1_on_a_repeat_group_conflict(experiment, checked, capsys):
    # Review of #133: 'check' described the conflict in its summary but exited 0, while
    # 'run' stops on it with 1. It is now reported as the error it is.
    assert main(["design", "--output", str(checked), "--repeat-group", "ctrl/day2=day1"]) == 1
    capsys.readouterr()
    assert main(["check", str(experiment), "--output", str(checked)]) == 1
    out = capsys.readouterr().out
    assert "day1: ctrl/day1, ctrl/day2, drug/day1 (more than one folder of ctrl)" in out
    assert "Error: A repeat group is one biological repeat" in out
    assert main(["run", str(experiment), "--output", str(checked), "--axes", "YX"]) == 1
