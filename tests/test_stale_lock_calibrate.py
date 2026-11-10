"""PP-8c review: ``calibrate`` takes ``--remove-stale-lock`` like the others.

The lock message (``output_locked``) tells users to give ``--remove-stale-lock`` to the
command. ``check``, ``run`` and ``masks`` took it; ``calibrate``, which also holds the output
folder's lock, did not. The option removes a lock only on that request, and never one of a
process still running on the same computer. (The ``design`` command was removed in v0.5.1.)
"""

import os

import numpy as np
import pytest

from described_tree import main_described
from morphobricks_preprocessing.run.lock import read_lock
from test_modes_lock_report import finished_pid, stale_lock
from tiff_tree import write_data


@pytest.fixture
def checked(tmp_path):
    root, output = tmp_path / "exp", tmp_path / "out"
    for seed, rel in enumerate(["ctrl/day1/a.tif", "drug/day1/a.tif"]):
        data = np.random.default_rng(seed).integers(0, 200, (6, 8), dtype=np.uint8)
        write_data(root / rel, data, "YX")
    assert main_described(["check", str(root), "--output", str(output)]) == 0
    return output


CALIBRATE = ["calibrate", "--size", ".", "XY", "0.5", "um"]


def stated(output):
    return (output / "calibration.csv").exists()


@pytest.mark.parametrize(("args", "done"), [(CALIBRATE, stated)])
def test_a_stale_lock_is_removed_only_on_request(checked, capsys, args, done):
    stale_lock(checked, finished_pid(), host="another-computer")
    command = [*args, "--output", str(checked)]
    assert main_described(command) == 1
    out = capsys.readouterr().out
    assert "Another run is using this output folder" in out and "--remove-stale-lock" in out
    assert not done(checked) and read_lock(checked) is not None
    assert main_described([*command, "--remove-stale-lock"]) == 0
    out = capsys.readouterr().out
    assert "Removed the lock of process" in out and "another-computer" in out
    assert done(checked) and read_lock(checked) is None


@pytest.mark.skipif(os.name != "posix", reason="only POSIX can tell a process runs")
@pytest.mark.parametrize(("args", "done"), [(CALIBRATE, stated)])
def test_the_lock_of_a_running_process_on_this_computer_is_kept(checked, capsys, args, done):
    stale_lock(checked, os.getpid())
    assert main_described([*args, "--output", str(checked), "--remove-stale-lock"]) == 1
    assert "The lock was kept" in capsys.readouterr().out
    assert not done(checked) and read_lock(checked).pid == os.getpid()


def test_calibrate_finds_the_lock_from_the_experiment_folder(tmp_path, capsys):
    root = tmp_path / "exp"
    write_data(root / "ctrl" / "a.tif", np.zeros((6, 8), dtype=np.uint8), "YX")
    output = tmp_path / "exp_MorphoBricks_Preprocessing"
    output.mkdir()
    stale_lock(output, finished_pid(), host="another-computer")
    assert main_described([*CALIBRATE, str(root), "--remove-stale-lock"]) == 0
    assert stated(output) and read_lock(output) is None
