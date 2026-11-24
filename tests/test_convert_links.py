"""A conversion never writes through a link (or mount point) in the copy tree (issue #447).

The folder for the copies is resolved once, when it is chosen; below it, a symbolic link (a
directory junction on Windows) or a mount point would send the ``.partial`` files and the
``os.replace`` that moves them into place somewhere else: into the experiment, over an
original, or into any writable folder. Every test here failed before the fix: the links
were followed, or a recorded copy replaced by a link to the same bytes was reused.
"""

import os
import shutil
from pathlib import Path

import pytest
import tifffile

import morphobricks_preprocessing.conversion_reuse as reuse_module
import morphobricks_preprocessing.mounts as mounts_module
from morphobricks_preprocessing import convert_experiment
from test_convert_run import SWAPPED, codes, swapped_experiment
from test_links import _can_link

LINKED = "copy_folder_has_links"


@pytest.fixture
def linkable(tmp_path):
    if not _can_link(tmp_path):
        pytest.skip("symbolic links cannot be made here (Windows without the privilege)")
    return tmp_path


def _folders(folder):
    """An experiment, the folder for its copies and a folder elsewhere."""
    return swapped_experiment(folder / "exp"), folder / "copies", folder / "elsewhere"


def _tree(folder):
    """Every file below ``folder`` (links not followed) with its bytes."""
    return {
        p.relative_to(folder).as_posix(): p.read_bytes()
        for p in sorted(folder.rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


def _refused(result, *names):
    assert not result.written
    assert [issue.code for issue in result.issues if issue.is_error] == [LINKED]
    issue = next(issue for issue in result.issues if issue.code == LINKED)
    for name in names:
        assert any(path.startswith(name) for path in issue.paths), issue.paths
        assert "Nothing was written" in issue.message
    return issue


def test_a_linked_subfolder_into_the_experiment_is_refused_and_no_original_is_touched(
    linkable,
):
    exp, copies = swapped_experiment(linkable / "exp"), linkable / "copies"
    before = _tree(exp)
    copies.mkdir()
    (copies / "ctrl").symlink_to(exp / "ctrl", target_is_directory=True)
    issue = _refused(convert_experiment(exp, SWAPPED, copies), "ctrl ->")
    assert str(exp / "ctrl") in issue.paths[0]
    assert _tree(exp) == before
    assert not list(exp.rglob("*.partial"))


def test_a_linked_subfolder_to_an_unrelated_folder_is_refused_and_nothing_is_written_there(
    linkable,
):
    exp, copies, elsewhere = _folders(linkable)
    elsewhere.mkdir()
    copies.mkdir()
    (copies / "drug").symlink_to(elsewhere, target_is_directory=True)
    _refused(convert_experiment(exp, SWAPPED, copies), "drug ->")
    assert list(elsewhere.iterdir()) == []
    assert not (copies / "ctrl").exists()


def test_a_dangling_link_is_refused(linkable):
    exp, copies = swapped_experiment(linkable / "exp"), linkable / "copies"
    copies.mkdir()
    (copies / "ctrl").symlink_to(linkable / "gone", target_is_directory=True)
    issue = _refused(convert_experiment(exp, SWAPPED, copies), "ctrl ->")
    assert not (linkable / "gone").exists()
    # Issue #467 (3): a mount point is not counted as a link.
    assert "holds 1 link or mount point (links shown as link -> target)" in issue.message
    (copies / "drug").symlink_to(linkable / "gone", target_is_directory=True)
    issue = _refused(convert_experiment(exp, SWAPPED, copies), "ctrl ->", "drug ->")
    assert "holds 2 links or mount points (links shown as link -> target)" in issue.message


def test_a_linked_partial_is_refused_not_removed(linkable):
    exp, copies = swapped_experiment(linkable / "exp"), linkable / "copies"
    (copies / "ctrl/r1").mkdir(parents=True)
    partial = copies / "ctrl/r1/a.tif.partial"
    partial.symlink_to(exp / "ctrl/r1/a.tif")
    before = _tree(exp)
    _refused(convert_experiment(exp, SWAPPED, copies), "ctrl/r1/a.tif.partial ->")
    assert partial.is_symlink() and _tree(exp) == before


def test_a_linked_parent_on_restart_with_a_new_image_is_refused(linkable):
    exp, copies, elsewhere = _folders(linkable)
    assert convert_experiment(exp, SWAPPED, copies).written
    elsewhere.mkdir()
    os.rename(copies / "drug", elsewhere / "drug")  # same bytes and times: reusable
    (copies / "drug").symlink_to(elsewhere / "drug", target_is_directory=True)
    swapped_experiment(exp, names=("drug/r1/b.tif",))
    before = _tree(elsewhere)
    _refused(convert_experiment(exp, SWAPPED, copies), "drug ->")
    assert _tree(elsewhere) == before
    assert not (elsewhere / "drug/r1/b.tif").exists()


def test_a_recorded_copy_replaced_by_a_link_to_the_same_bytes_is_refused(linkable):
    exp, copies, elsewhere = _folders(linkable)
    assert convert_experiment(exp, SWAPPED, copies).written
    elsewhere.mkdir()
    copy = copies / "ctrl/r1/a.tif"
    shutil.copy2(copy, elsewhere / "a.tif")  # same size, time and SHA-256
    copy.unlink()
    copy.symlink_to(elsewhere / "a.tif")
    _refused(convert_experiment(exp, SWAPPED, copies), "ctrl/r1/a.tif ->")


def test_a_mount_point_below_the_copy_tree_is_refused(tmp_path, monkeypatch):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    (copies / "ctrl").mkdir(parents=True)
    mounted = (copies / "ctrl").resolve()
    monkeypatch.setattr(mounts_module, "mount_points", lambda *_: frozenset({mounted}))
    _refused(convert_experiment(exp, SWAPPED, copies), "ctrl")
    assert list((copies / "ctrl").iterdir()) == []


def test_real_subfolders_already_there_convert_as_before(tmp_path):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    (copies / "ctrl/r1").mkdir(parents=True)
    (copies / "drug").mkdir()
    result = convert_experiment(exp, SWAPPED, copies)
    assert result.written and not [i for i in result.issues if i.is_error]
    assert tifffile.imread(copies / "ctrl/r1/a.tif").shape == (2, 9, 6)
    again = convert_experiment(exp, SWAPPED, copies)
    assert again.written and len(again.reused) == 3


# Follow-ups of the review of #465 (issue #467) -------------------------------------------


def test_a_linked_publication_folder_is_refused_before_anything_is_removed_through_it(
    linkable,
):
    # Issue #467 (1): the copy tree's lock finished a run's publication first, so a
    # ``.publish`` link into the experiment lost its ``publication.json`` there, then the
    # conversion stopped with a bare OSError.
    exp, copies = swapped_experiment(linkable / "exp"), linkable / "copies"
    (exp / "ctrl/publication.json").write_text('{"files": {}}')
    before = _tree(exp)
    copies.mkdir()
    (copies / ".publish").symlink_to(exp / "ctrl", target_is_directory=True)
    _refused(convert_experiment(exp, SWAPPED, copies), ".publish ->")
    assert _tree(exp) == before


def test_a_publication_folder_in_the_copy_tree_is_neither_finished_nor_removed(tmp_path):
    # Issue #467 (1): a conversion never finishes or discards a run's publication: the
    # copy tree is not a run's output folder, so a ``.publish`` folder there is the user's.
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    notes = copies / ".publish" / "notes.txt"
    notes.parent.mkdir(parents=True)
    notes.write_bytes(b"mine")
    result = convert_experiment(exp, SWAPPED, copies)
    assert not result.written and codes(result.issues) == ["copy_folder_not_empty"]
    assert result.issues[0].paths == (".publish/notes.txt",)
    assert notes.read_bytes() == b"mine"


@pytest.mark.parametrize(
    "error", [PermissionError(13, "Permission denied"), FileNotFoundError(2, "No such file")]
)
def test_an_unreadable_or_vanished_folder_is_refused_and_named(tmp_path, monkeypatch, error):
    # Issue #467 (2): ``os.scandir`` raised out of the walk, a traceback for the user.
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    (copies / "ctrl/r1").mkdir(parents=True)
    blocked, real = (copies / "ctrl").resolve(), os.scandir

    def scandir(path="."):
        if isinstance(path, (str, os.PathLike)) and Path(path) == blocked:
            raise error
        return real(path)

    monkeypatch.setattr(reuse_module.os, "scandir", scandir)
    result = convert_experiment(exp, SWAPPED, copies)
    monkeypatch.undo()
    assert not result.written and codes(result.issues) == ["copy_folder_unreadable"]
    assert result.issues[0].paths == ("ctrl",)
    assert "Nothing was written" in result.issues[0].message
    assert sorted(p.relative_to(copies).as_posix() for p in copies.rglob("*")) == [
        "ctrl",
        "ctrl/r1",
    ]


def test_a_mount_at_or_above_the_copy_tree_is_allowed(tmp_path, monkeypatch):
    # Issue #467 (4): the Colab case, a copy tree on a mounted Drive.
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    copies.mkdir()
    above = frozenset({copies.resolve(), tmp_path.resolve()})
    monkeypatch.setattr(mounts_module, "mount_points", lambda *_: above)
    result = convert_experiment(exp, SWAPPED, copies)
    assert result.written and not [i for i in result.issues if i.is_error]


def test_a_link_inside_an_ome_zarr_copy_is_refused(linkable):
    # Issue #467 (4): an OME-Zarr copy is a folder; the walk goes into it.
    pytest.importorskip("zarr")
    exp, copies, elsewhere = _folders(linkable)
    assert convert_experiment(exp, SWAPPED, copies, store="ome-zarr").written
    elsewhere.mkdir()
    (copies / "ctrl/r1/a.ome.zarr/notes").symlink_to(elsewhere, target_is_directory=True)
    before = _tree(copies)
    _refused(
        convert_experiment(exp, SWAPPED, copies, store="ome-zarr"), "ctrl/r1/a.ome.zarr/notes ->"
    )
    assert _tree(copies) == before and list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("inside", [True, False], ids=["an_original", "a_file_elsewhere"])
def test_a_hard_linked_partial_leaves_the_original_intact(tmp_path, inside):
    # Issue #467 (4): a hard link is no link to the walk (one more name of the same file).
    # Either way it is a .partial this conversion did not write (#448): refused, never
    # written into nor removed (an original hard-linked also gets the warning
    # hard_linked_file from the experiment's walk).
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    original = exp / "ctrl/r1/a.tif" if inside else tmp_path / "elsewhere/notes.bin"
    original.parent.mkdir(parents=True, exist_ok=True)
    if not inside:
        original.write_bytes(b"not an image")
    partial = copies / "ctrl/r1/a.tif.partial"
    partial.parent.mkdir(parents=True)
    try:
        os.link(original, partial)
    except OSError:
        pytest.skip("hard links cannot be made here")
    before, kept = _tree(exp), original.read_bytes()
    result = convert_experiment(exp, SWAPPED, copies)
    assert _tree(exp) == before and original.read_bytes() == kept
    assert os.path.samefile(partial, original)
    refused = [issue for issue in result.issues if issue.is_error]
    assert not result.written and codes(refused) == ["copy_folder_not_empty"]
    assert refused[0].paths == ("ctrl/r1/a.tif.partial",)
    assert ("hard_linked_file" in codes(result.issues)) == inside  # the original's warning
