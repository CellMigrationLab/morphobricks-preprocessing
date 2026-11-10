"""Mount points below the experiment and masks folders are refused (owner, 3 November 2026).

A Linux bind mount below the experiment is an ordinary folder to the walk, not a link, so
before the fix discovery walked into data from outside the selected folder. Hard-linked
images and masks (a link count above 1) are a warning only.

The tests with a real bind mount need ``mount --bind`` (root on Linux) and are skipped by
name otherwise; the others replace the mount table, so they always run.
"""

import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from described_tree import main_described, run_described
from morphobricks_preprocessing import (
    RunInputs,
    RunInputsError,
    prefill_mapping,
)
from morphobricks_preprocessing import mounts as mounts_module
from morphobricks_preprocessing.discovery import discover_experiment
from morphobricks_preprocessing.mounts import MountCheck, mount_points
from morphobricks_preprocessing.run import preflight
from morphobricks_preprocessing.testing import make_clean_run
from tiff_tree import write_data


def _errors(issues):
    return sorted(issue.code for issue in issues if issue.is_error)


def _issue(issues, code):
    return next(issue for issue in issues if issue.code == code)


@pytest.fixture
def tree(tmp_path):
    """An experiment of two leaf folders, and a folder outside it holding one image."""
    root, outside = tmp_path / "exp", tmp_path / "outside"
    for rel in ("ctrl/rep1/a.tif", "drug/rep1/a.tif"):
        write_data(root / rel, np.zeros((4, 4), np.uint8), "YX")
    write_data(outside / "far.tif", np.ones((4, 4), np.uint8), "YX")
    return root, outside


# The mount table replaced: always runs ----------------------------------------------------


@pytest.fixture
def mounted(monkeypatch):
    """Make the given real paths read as mount points (as /proc/self/mountinfo lists them)."""
    points: set[Path] = set()

    def table(*_args):
        return frozenset(points)

    monkeypatch.setattr(mounts_module, "mount_points", table)

    def mount(path):
        points.add(Path(os.path.realpath(path)))

    return mount


def test_a_mounted_folder_in_the_experiment_is_refused_naming_it(tree, mounted):
    root, _ = tree
    write_data(root / "drug/rep2/b.tif", np.zeros((4, 4), np.uint8), "YX")
    mounted(root / "drug/rep2")
    found = discover_experiment(root)
    assert _errors(found.issues) == ["mounted_folder"]
    issue = _issue(found.issues, "mounted_folder")
    assert issue.paths == ("drug/rep2",)
    assert "experiment folder" in issue.message and "mount point" in issue.message
    assert "drug/rep2/b.tif" not in found.images


def test_the_experiment_folder_itself_may_be_a_mount_point(tree, mounted):
    root, _ = tree
    mounted(root)
    found = discover_experiment(root)
    assert found.ok and not found.issues


def test_a_folder_reached_through_a_link_onto_a_mount_point_is_refused(tree, mounted):
    root, _ = tree
    try:
        (root / "drug/rep2").symlink_to(root / ".hidden", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links cannot be made here")
    write_data(root / ".hidden/b.tif", np.zeros((4, 4), np.uint8), "YX")
    mounted(root / ".hidden")
    found = discover_experiment(root)
    assert _issue(found.issues, "mounted_folder").paths == ("drug/rep2",)


def test_check_and_run_refuse_a_mounted_folder_and_write_no_table(tree, mounted, tmp_path):
    root, _ = tree
    mounted(root / "drug/rep1")
    out = tmp_path / "out"
    assert main_described(["check", str(root), "--output", str(out)]) == 1
    result = run_described(root, prefill_mapping("YX"), output_folder=out)
    assert not result.written
    assert "mounted_folder" in _errors(result.issues)
    assert not (out / "plane_histograms.csv").exists()


def test_a_mounted_folder_in_the_masks_folder_is_refused(tmp_path, mounted):
    clean = make_clean_run(tmp_path / "run", axes="YX", shape=(16, 20), mask_types=("Cells",))
    mounted(clean.masks / "Cells/drug")
    pre = preflight(clean.experiment, clean.output, masks_folder=clean.masks)
    issue = _issue(pre.masks.issues, "mounted_folder")
    assert issue.paths == ("Cells/drug",) and "masks folder" in issue.message
    with pytest.raises(RunInputsError, match="mount point"):
        RunInputs.open(clean.output)


def test_run_inputs_refuse_a_folder_mounted_after_the_run(tmp_path, mounted):
    clean = make_clean_run(tmp_path / "run", axes="YX", shape=(16, 20))
    mounted(clean.experiment / "ctrl/rep1")
    with pytest.raises(RunInputsError, match="ctrl/rep1"):
        RunInputs.open(clean.output)


def test_a_mounted_folder_inside_an_ome_zarr_store_is_refused(tree, mounted):
    pytest.importorskip("zarr")
    from zarr_tree import write_ngff

    root, _ = tree
    store = write_ngff(root / "ctrl/rep1/z.ome.zarr", np.zeros((1, 1, 4, 4), np.uint8))
    mounted(store / "0")
    found = discover_experiment(root)
    assert _issue(found.issues, "mounted_folder").paths == ("ctrl/rep1/z.ome.zarr/0",)


def test_a_mounted_file_inside_an_ome_zarr_store_is_refused(tree, mounted):
    # Review of #140: a store's plain files are looked up among the mount points too (a
    # bind mount of one chunk file brings in another file's bytes).
    pytest.importorskip("zarr")
    from zarr_tree import write_ngff

    root, _ = tree
    store = write_ngff(root / "ctrl/rep1/z.ome.zarr", np.zeros((1, 1, 4, 4), np.uint8))
    chunk = next(path for path in sorted(store.rglob("*")) if path.is_file() and "0" in path.parts)
    mounted(chunk)
    found = discover_experiment(root)
    assert _issue(found.issues, "mounted_folder").paths == (chunk.relative_to(root).as_posix(),)


def test_a_folder_on_another_device_is_a_mount_point(tmp_path, monkeypatch):
    # The portable rule, where no mount table can be read (macOS, Windows).
    monkeypatch.setattr(mounts_module, "mount_points", lambda *_: frozenset())
    (tmp_path / "sub").mkdir()
    check = MountCheck(tmp_path)
    (entry,) = os.scandir(tmp_path)
    assert check.device is not None
    assert not check.is_mount(tmp_path / "sub", entry)
    check.device += 1  # the selected folder on another device than its subfolder
    assert check.is_mount(tmp_path / "sub", entry)


class _OnAnotherDevice:
    """A file's stat result as a file mounted on its own from another device gives it."""

    def __init__(self, found):
        self._found = found
        self.st_dev = found.st_dev + 1

    def __getattr__(self, name):
        return getattr(self._found, name)


def test_a_file_on_another_device_is_a_mount_point(tree, monkeypatch):
    # Review of rc5: the device rule held for folders only; a file's device comes from the
    # one stat its walk takes (LinkGuard.claim), so a file mounted on its own is seen too.
    monkeypatch.setattr(mounts_module, "mount_points", lambda *_: frozenset())
    root, _ = tree
    # Compared without resolving: os.path.realpath may itself call os.stat (Windows).
    target, real_stat = os.path.normcase(os.path.abspath(root / "drug/rep1/a.tif")), os.stat

    def stat(path, *args, **kwargs):
        found = real_stat(path, *args, **kwargs)
        same = isinstance(path, (str, os.PathLike)) and (
            os.path.normcase(os.path.abspath(path)) == target
        )
        return _OnAnotherDevice(found) if same else found

    assert _errors(discover_experiment(root).issues) == []
    monkeypatch.setattr(os, "stat", stat)
    found = discover_experiment(root)
    assert _errors(found.issues) == ["mounted_folder"]
    assert _issue(found.issues, "mounted_folder").paths == ("drug/rep1/a.tif",)


def test_the_device_is_read_from_the_path_not_the_folder_entry(tmp_path, monkeypatch):
    # Review of #140: on Windows os.DirEntry.stat() reports st_dev 0, so reading the
    # device from the entry refused every folder of every experiment there.
    monkeypatch.setattr(mounts_module, "mount_points", lambda *_: frozenset())
    (tmp_path / "sub").mkdir()
    check = MountCheck(tmp_path)

    class WindowsEntry:
        """A folder entry as Windows gives it: its stat() has no device."""

        path = str(tmp_path / "sub")

        def is_dir(self):
            return True

        def stat(self):
            return os.stat_result((0o40755, 0, 0, 0, 0, 0, 0, 0, 0, 0))

    assert not check.is_mount(tmp_path / "sub", WindowsEntry())


def test_the_mount_table_is_read_with_its_escapes(tmp_path):
    table = tmp_path / "mountinfo"
    table.write_bytes(
        b"22 1 0:21 / / rw - ext4 /dev/vda rw\n"
        b"49 28 254:0 /data /mnt/my\\040data rw,relatime - ext4 /dev/vda rw\n"
        b"short line\n"
    )
    assert mount_points(table) == {Path("/"), Path("/mnt/my data")}
    assert mount_points(tmp_path / "missing") == frozenset()


# A real bind mount (Linux, as root) --------------------------------------------------------


def _bind(source, target):
    """Bind-mount ``source`` on ``target``; skip by name when it cannot be done here."""
    if os.name != "posix" or shutil.which("mount") is None or os.geteuid() != 0:
        pytest.skip("a bind mount needs mount --bind as root on Linux")
    done = subprocess.run(
        ["mount", "--bind", str(source), str(target)], capture_output=True, check=False
    )
    if done.returncode != 0:
        pytest.skip(f"mount --bind is not allowed here: {done.stderr.decode().strip()}")


@pytest.fixture
def bind():
    """Bind-mount folders for one test, and unmount them after it."""
    targets = []

    def mount(source, target):
        target.mkdir(parents=True, exist_ok=True)
        _bind(source, target)
        targets.append(target)

    yield mount
    for target in reversed(targets):
        subprocess.run(["umount", str(target)], check=False)


def test_a_real_bind_mount_of_the_same_file_system_is_refused(tree, bind, tmp_path):
    root, outside = tree
    bind(outside, root / "drug/rep2")
    # The kernel's own checks see no mount point: same device, an inode of its own.
    assert not os.path.ismount(root / "drug/rep2")
    found = discover_experiment(root)
    assert _issue(found.issues, "mounted_folder").paths == ("drug/rep2",)
    assert "drug/rep2/far.tif" not in found.images
    out = tmp_path / "out"
    assert main_described(["check", str(root), "--output", str(out)]) == 1
    result = run_described(root, prefill_mapping("YX"), output_folder=out)
    assert not result.written and "mounted_folder" in _errors(result.issues)


def test_a_real_bind_mount_inside_an_ome_zarr_store_is_refused(tree, bind):
    pytest.importorskip("zarr")
    from zarr_tree import write_ngff

    root, outside = tree
    store = write_ngff(root / "ctrl/rep1/z.ome.zarr", np.zeros((1, 1, 4, 4), np.uint8))
    bind(outside, store / "extra")
    found = discover_experiment(root)
    assert _issue(found.issues, "mounted_folder").paths == ("ctrl/rep1/z.ome.zarr/extra",)


# Hard-linked images and masks: a warning only ----------------------------------------------


def _hard_link(source, target):
    try:
        os.link(source, target)
    except (OSError, NotImplementedError):
        pytest.skip("hard links cannot be made here")


def test_an_image_with_another_name_outside_is_a_warning(tree, tmp_path):
    root, outside = tree
    _hard_link(root / "ctrl/rep1/a.tif", outside / "other-name.tif")
    found = discover_experiment(root)
    assert found.ok
    issue = _issue(found.issues, "hard_linked_file")
    assert not issue.is_error and issue.paths == ("ctrl/rep1/a.tif (2 names)",)
    assert found.images == ("ctrl/rep1/a.tif", "drug/rep1/a.tif")
    out = tmp_path / "out"
    assert main_described(["check", str(root), "--output", str(out)]) == 0
    result = run_described(root, prefill_mapping("YX"), output_folder=out)
    assert result.written
    assert "hard_linked_file" in {issue.code for issue in result.issues}


def test_a_hard_linked_mask_is_a_warning(tmp_path):
    clean = make_clean_run(tmp_path / "run", axes="YX", shape=(16, 20), mask_types=("Cells",))
    _hard_link(clean.masks / "Cells/ctrl/rep1/a.tif", tmp_path / "elsewhere.tif")
    pre = preflight(clean.experiment, clean.output, masks_folder=clean.masks)
    issue = _issue(pre.masks.issues, "hard_linked_file")
    assert issue.paths == ("Cells/ctrl/rep1/a.tif (2 names)",)
    assert not [issue for issue in pre.masks.issues if issue.is_error]
    RunInputs.open(clean.output)  # unchanged: a warning does not refuse the run
