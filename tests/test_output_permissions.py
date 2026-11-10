"""PP-8c item 1: output files get the permissions a normal ``open()`` would give them.

Every output is written atomically (a temporary file, then a replace). A temporary file
is created readable by its owner only (0600), so before this fix every output was 0600
whatever the umask, and colleagues could not read the results in a shared lab folder.
Now a new file gets ``0666 & ~umask``, and a file that existed keeps its own mode.
"""

import os
import stat
import sys

import pytest

from described_tree import main_described, run_described
from morphobricks_preprocessing import _io, calibration_entry, write_calibration
from morphobricks_preprocessing.calibration import Calibration
from morphobricks_preprocessing.testing import make_clean_run

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX file modes: Windows files have no umask modes"
)


@pytest.fixture
def umask_022():
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_new_file_mode_follows_the_umask(umask_022):
    new_file_mode = _io.new_file_mode  # added by the fix
    assert new_file_mode() == 0o644
    os.umask(0o002)
    assert new_file_mode() == 0o664
    assert os.umask(0o002) == 0o002  # reading it leaves the umask as it was


def test_an_atomic_write_gives_the_mode_of_a_normal_open(tmp_path, umask_022):
    written = _io.write_text_atomic(tmp_path / "a.txt", "x")
    with open(tmp_path / "b.txt", "w") as stream:
        stream.write("x")
    assert mode(written) == mode(tmp_path / "b.txt") == 0o644


def test_a_file_that_existed_keeps_its_mode(tmp_path, umask_022):
    path = tmp_path / "a.txt"
    path.write_text("before")
    path.chmod(0o640)
    _io.write_text_atomic(path, "after")
    assert path.read_text() == "after"
    assert mode(path) == 0o640


def test_every_output_of_a_run_is_readable_by_others(tmp_path, umask_022):
    clean = make_clean_run(tmp_path, axes="YX", shape=(8, 8), mask_types=("Cells",), design=True)
    write_calibration(Calibration((calibration_entry(".", "X", 0.5, "um"),)), clean.output)
    args = ["masks", str(clean.experiment), "--output", str(clean.output)]
    assert main_described([*args, "--masks", str(clean.masks)]) == 0
    files = sorted(path for path in clean.output.iterdir() if path.is_file())
    names = {path.name for path in files}
    assert {
        "plane_histograms.csv",
        "run_manifest.json",
        "discovery_report.json",
        "discovery_report.txt",
        "experiment_design.csv",
        "calibration.csv",
        "mask_relationships.json",
    } <= names
    assert {path.name: mode(path) for path in files} == {name: 0o644 for name in names}


def test_a_rerun_keeps_the_modes_the_user_set(tmp_path, umask_022):
    clean = make_clean_run(tmp_path, axes="YX", shape=(8, 8))
    table = clean.output / "plane_histograms.csv"
    table.chmod(0o600)
    assert run_described(output_folder=clean.output, mode="full").written
    assert mode(table) == 0o600
    assert mode(clean.output / "run_manifest.json") == 0o644
