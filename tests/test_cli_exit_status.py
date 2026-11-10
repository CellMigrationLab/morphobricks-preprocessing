"""Every command exits with status 1 when it reports an error, and 0 otherwise.

Found in the review of #130: ``check`` printed an error and exited 0, while ``run`` stopped on
it with 1. Warnings alone never change the status. Since v0.5.1 a missing published description
is such an error (``A_DESCRIPTION_REQUIRED``) for ``check``, ``run`` and ``masks``.
"""

import numpy as np
import pytest

from described_tree import main_described
from morphobricks_preprocessing.cli import main
from tiff_tree import write_data, write_file


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    rels = ["ctrl/day1/a.tif", "ctrl/day2/a.tif", "drug/day1/a.tif", "drug/day2/a.tif"]
    for seed, rel in enumerate(rels):
        data = np.random.default_rng(seed).integers(0, 200, (6, 8), dtype=np.uint8)
        write_data(root / rel, data, "YX")
    return root


@pytest.mark.parametrize("command", ["check", "run", "masks"])
def test_without_a_published_description_every_run_command_exits_with_1(
    experiment, tmp_path, capsys, command
):
    output = tmp_path / "out"
    extra = ["--axes", "YX"] if command == "run" else []
    assert main([command, str(experiment), "--output", str(output), *extra]) == 1
    out = capsys.readouterr().out
    assert "A_DESCRIPTION_REQUIRED" in out and "describe" in out
    assert not output.exists()  # nothing read or written


def test_check_exits_with_0_on_warnings(experiment, tmp_path, capsys):
    write_file(experiment / "ctrl" / "notes.txt")  # a warning, not an error
    output = tmp_path / "out"
    assert main_described(["check", str(experiment), "--output", str(output)]) == 0
    assert "Warning" in capsys.readouterr().out


def test_check_and_run_exit_with_1_on_an_error(experiment, tmp_path, capsys):
    write_file(experiment / "ctrl" / "day1" / "bad.tif", b"garbage")
    output = tmp_path / "out"
    assert main_described(["check", str(experiment), "--output", str(output)]) == 1
    assert main_described(["run", str(experiment), "--output", str(output), "--axes", "YX"]) == 1
