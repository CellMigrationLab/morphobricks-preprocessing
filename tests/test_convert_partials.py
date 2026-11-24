"""A conversion removes only what it can prove it wrote (issues #448 and #467).

Issue #448: a ``<name>.partial`` file or folder at the path of a planned copy was taken for
one left by an interrupted conversion and removed (``shutil.rmtree`` for a folder), whoever
had put it there. A conversion now writes its copies in its own work folder (``.mbconv``
since #482, ``.morphobricks-conversion.partial`` before), which it makes itself and marks
with an owner file before it writes any copy; a later conversion removes a work folder only
when that owner file is there. A ``.partial`` elsewhere is never removed: without a record
it is refused as any other file, with a record it is left alone.

Issue #467 (5): a link swapped into the copy tree while the copies are written is caught
by a second check right before each copy is moved into place, and the clean-up after a
failure never removes anything through a link.
"""

import hashlib
import json
import os
import socket

import pytest
import tifffile

import morphobricks_preprocessing.conversion as convert_module
import morphobricks_preprocessing.conversion_plan as plan_module
import morphobricks_preprocessing.conversion_write as write_module
from morphobricks_preprocessing import convert_experiment
from morphobricks_preprocessing.conversion_record import RECORD_FILENAME
from test_convert_links import _tree
from test_convert_run import SWAPPED, codes, swapped_experiment
from test_links import _can_link

WORK = ".mbconv"
LEGACY = ".morphobricks-conversion.partial"  # the work folder's name before #482
STORES = [
    pytest.param("ome-tiff", "ctrl/r1/a.tif", id="ome-tiff"),
    pytest.param("ome-zarr", "ctrl/r1/a.ome.zarr", id="ome-zarr"),
]


def _store(store):
    if store == "ome-zarr":
        pytest.importorskip("zarr")


def _digests(folder):
    """The SHA-256 of every file of the copy tree but its record and work folder.

    An OME-TIFF copy is taken by its pixels and axes: its OME-XML holds a new UUID each time.
    """
    found = {}
    for name, data in _tree(folder).items():
        if name == RECORD_FILENAME or name.startswith(WORK):
            continue
        if name.endswith(".tif"):
            with tifffile.TiffFile(folder / name) as tif:
                series = tif.series[0]
                data = series.axes.encode() + series.asarray().tobytes()
        found[name] = hashlib.sha256(data).hexdigest()
    return found


# Issue #448: a .partial the conversion did not write is never removed --------------------


@pytest.mark.parametrize(("store", "copy"), STORES)
def test_a_partial_folder_of_someone_else_is_refused_and_kept_byte_for_byte(tmp_path, store, copy):
    _store(store)
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    notes = copies / f"{copy}.partial" / "nested" / "unrelated_notes.txt"
    notes.parent.mkdir(parents=True)
    notes.write_bytes(b"field notes")
    before = _tree(copies)
    result = convert_experiment(exp, SWAPPED, copies, store=store)
    assert not result.written and codes(result.issues) == ["copy_folder_not_empty"]
    assert result.issues[0].paths == (f"{copy}.partial/nested/unrelated_notes.txt",)
    assert _tree(copies) == before


@pytest.mark.parametrize(("store", "copy"), STORES)
def test_a_partial_file_of_someone_else_is_refused_and_kept_byte_for_byte(tmp_path, store, copy):
    # Before #448 such a file was taken for the leftover of an interrupted conversion.
    _store(store)
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    left = copies / f"{copy}.partial"
    left.parent.mkdir(parents=True)
    left.write_bytes(b"not a copy")
    result = convert_experiment(exp, SWAPPED, copies, store=store)
    assert not result.written and codes(result.issues) == ["copy_folder_not_empty"]
    assert result.issues[0].paths == (f"{copy}.partial",)
    assert left.read_bytes() == b"not a copy"


def test_with_a_record_a_partial_of_someone_else_is_left_alone(tmp_path):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    assert convert_experiment(exp, SWAPPED, copies).written
    swapped_experiment(exp, names=("drug/r1/b.tif",))
    notes = copies / "drug/r1/b.tif.partial/unrelated_notes.txt"
    notes.parent.mkdir(parents=True)
    notes.write_bytes(b"field notes")
    result = convert_experiment(exp, SWAPPED, copies)
    assert result.written and result.converted == ("drug/r1/b.tif",)
    assert notes.read_bytes() == b"field notes"
    assert not (copies / WORK).exists()


@pytest.mark.parametrize("recorded", [False, True], ids=["no_record", "record"])
def test_a_work_folder_without_its_owner_file_is_refused_and_kept(tmp_path, recorded):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    if recorded:
        assert convert_experiment(exp, SWAPPED, copies).written
        swapped_experiment(exp, names=("drug/r1/b.tif",))
    mine = copies / WORK / "files/ctrl/r1/a.tif"
    mine.parent.mkdir(parents=True)
    mine.write_bytes(b"mine")
    (copies / WORK / "owner.json").write_text('{"kind": "something else"}')
    before = _tree(copies)
    result = convert_experiment(exp, SWAPPED, copies)
    assert not result.written and codes(result.issues) == ["conversion_work_folder_unowned"]
    assert result.issues[0].paths == (WORK,)
    assert "Nothing was removed" in result.issues[0].message
    assert _tree(copies) == before


@pytest.mark.parametrize(("store", "copy"), STORES)
def test_the_work_folder_of_a_killed_conversion_is_removed_and_the_copies_match(
    tmp_path, monkeypatch, store, copy
):
    # A conversion killed while writing (no clean-up ran) leaves its work folder with its
    # owner file; the next one removes it and writes the same copies as a clean conversion.
    _store(store)
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    clean = tmp_path / "clean"
    assert convert_experiment(exp, SWAPPED, clean, store=store).written
    real, calls = write_module._write_partial, []

    def killed(root, work, item, memory_limit):
        calls.append(item)
        real(root, work, item, memory_limit)
        if len(calls) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(write_module, "_write_partial", killed)
    monkeypatch.setattr(write_module, "_clean_up", lambda *args: None)
    with pytest.raises(KeyboardInterrupt):
        convert_experiment(exp, SWAPPED, copies, store=store)
    monkeypatch.undo()
    owner = json.loads((copies / WORK / "owner.json").read_text())
    assert owner["kind"] == "morphobricks-preprocessing conversion work folder"
    assert owner["holder"]["pid"] == os.getpid()
    assert any(p.name.startswith("a.") for p in (copies / WORK / "files/ctrl/r1").iterdir())
    result = convert_experiment(exp, SWAPPED, copies, store=store)
    assert result.written and not (copies / WORK).exists()
    assert _digests(copies) == _digests(clean) and any(n.startswith(copy) for n in _digests(clean))


# Issue #482 (1): the owner file must prove the folder is a conversion's --------------------

KIND = "morphobricks-preprocessing conversion work folder"


def _holder(pid=None, host=None, token="0123456789abcdef0123456789abcdef"):
    return {
        "host": host or socket.gethostname(),
        "pid": pid or 999_999_999,
        "started_at": "2026-11-24T10:00:00+00:00",
        "token": token,
    }


def _leftover(copies, owner, extra=None, name=WORK):
    """A work folder holding one written copy, its owner file and maybe ``extra``."""
    work = copies / name
    mine = work / "files/ctrl/r1/a.tif"
    mine.parent.mkdir(parents=True)
    mine.write_bytes(b"mine")
    (work / "owner.json").write_text(json.dumps(owner) if not isinstance(owner, str) else owner)
    if extra is not None:
        (work / extra).write_bytes(b"notes")
    return work


@pytest.mark.parametrize(
    ("owner", "extra"),
    [
        pytest.param({"kind": KIND}, None, id="kind_only"),
        pytest.param({"kind": KIND, "holder": {"pid": 1}}, None, id="holder_incomplete"),
        pytest.param({"kind": KIND, "holder": _holder(token="")}, None, id="no_token"),
        pytest.param({"kind": KIND, "holder": _holder(pid=True)}, None, id="pid_not_a_number"),
        pytest.param({"kind": KIND, "holder": _holder(), "x": 1}, None, id="other_key"),
        pytest.param({"kind": KIND, "holder": _holder()}, "notes.txt", id="other_file"),
    ],
)
def test_a_forged_owner_file_or_other_content_is_refused_and_nothing_is_removed(
    tmp_path, owner, extra
):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    _leftover(copies, owner, extra)
    before = _tree(copies)
    result = convert_experiment(exp, SWAPPED, copies)
    assert not result.written and codes(result.issues) == ["conversion_work_folder_unowned"]
    assert _tree(copies) == before


@pytest.mark.skipif(os.name != "posix", reason="only POSIX tells whether a process runs")
def test_a_work_folder_whose_holder_still_runs_here_is_refused_and_kept(tmp_path):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    _leftover(copies, {"kind": KIND, "holder": _holder(pid=os.getppid())})
    before = _tree(copies)
    result = convert_experiment(exp, SWAPPED, copies)
    assert not result.written and codes(result.issues) == ["conversion_work_folder_in_use"]
    assert f"process {os.getppid()}" in result.issues[0].message
    assert _tree(copies) == before


def test_a_well_formed_work_folder_of_a_process_that_is_gone_is_removed(tmp_path):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    _leftover(copies, {"kind": KIND, "holder": _holder()})
    result = convert_experiment(exp, SWAPPED, copies)
    assert result.written and not (copies / WORK).exists()


@pytest.mark.parametrize("fails", [False, True], ids=["success", "failure"])
def test_a_work_folder_whose_token_changed_is_not_removed_at_the_clean_up(
    tmp_path, monkeypatch, fails
):
    # The lock was removed by mistake and another conversion took the work folder over: its
    # owner file now holds another token, so this conversion removes nothing of it.
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    real, calls = write_module.write_named_copy, []
    theirs = {"kind": KIND, "holder": _holder(token="f" * 32)}

    def taken_over(source, target, *args, **kwargs):
        calls.append(target)
        real(source, target, *args, **kwargs)
        if len(calls) == 2:
            (copies / WORK / "owner.json").write_text(json.dumps(theirs))
            (copies / WORK / "files/theirs.tif").write_bytes(b"theirs")
            if fails:
                raise OSError("device removed")

    monkeypatch.setattr(write_module, "write_named_copy", taken_over)
    result = convert_experiment(exp, SWAPPED, copies)
    assert result.written is not fails
    assert json.loads((copies / WORK / "owner.json").read_text()) == theirs
    assert (copies / WORK / "files/theirs.tif").read_bytes() == b"theirs"


# Issue #482 (2): a kill between making the work folder and writing its owner file --------

_WRITTEN = json.dumps({"kind": KIND, "holder": _holder()}, sort_keys=True).encode()


@pytest.mark.parametrize(
    "owner",
    [
        pytest.param(None, id="empty_folder"),
        pytest.param(b"", id="empty_owner_file"),
        pytest.param(_WRITTEN[:7], id="owner_file_cut_short_early"),
        pytest.param(_WRITTEN[:-1], id="owner_file_cut_short_late"),
    ],
)
def test_an_empty_or_unfinished_work_folder_is_removed(tmp_path, owner):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    (copies / WORK).mkdir(parents=True)
    if owner is not None:
        (copies / WORK / "owner.json").write_bytes(owner)
    result = convert_experiment(exp, SWAPPED, copies)
    assert result.written and not (copies / WORK).exists()


@pytest.mark.parametrize(
    "files",
    [
        pytest.param({"owner.json": b"my notes"}, id="other_text"),
        pytest.param({"owner.json": b"", "notes.txt": b"x"}, id="empty_owner_and_a_file"),
        pytest.param({"notes.txt": b""}, id="other_empty_file"),
    ],
)
def test_a_work_folder_with_anything_else_is_still_refused_and_kept(tmp_path, files):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    (copies / WORK).mkdir(parents=True)
    for name, data in files.items():
        (copies / WORK / name).write_bytes(data)
    before = _tree(copies)
    result = convert_experiment(exp, SWAPPED, copies)
    assert not result.written and codes(result.issues) == ["conversion_work_folder_unowned"]
    assert _tree(copies) == before


# Issue #482 (3): a kill while the copies are moved into place ----------------------------


def test_copies_moved_before_a_kill_are_named_as_from_an_interrupted_move(tmp_path, monkeypatch):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    real, moved = os.replace, []

    def killed(source, target):
        real(source, target)
        moved.append(target)
        raise KeyboardInterrupt  # killed right after the first copy was moved into place

    monkeypatch.setattr(write_module.os, "replace", killed)
    monkeypatch.setattr(write_module, "_clean_up", lambda *args: None)
    with pytest.raises(KeyboardInterrupt):
        convert_experiment(exp, SWAPPED, copies)
    monkeypatch.undo()
    first = moved[0].relative_to(copies).as_posix()
    result = convert_experiment(exp, SWAPPED, copies)
    assert not result.written and codes(result.issues) == ["copy_folder_not_empty"]
    issue = result.issues[0]
    assert issue.paths == (first,)
    assert "interrupted" in issue.message and "remove them" in issue.action
    assert (copies / WORK).is_dir()
    moved[0].unlink()  # what the message says to do
    result = convert_experiment(exp, SWAPPED, copies)
    assert result.written and not (copies / WORK).exists()


# Issue #482 (4): the space is checked once a killed conversion's work folder is gone ------


def test_a_killed_conversions_work_folder_is_removed_before_the_space_is_checked(
    tmp_path, monkeypatch
):
    # The killed conversion filled the drive: only once its work folder is gone do the
    # copies fit.
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    _leftover(copies, {"kind": KIND, "holder": _holder()})
    monkeypatch.setattr(
        convert_module, "free_space", lambda dest: 0 if (dest / WORK).exists() else 2**40
    )
    result = convert_experiment(exp, SWAPPED, copies)
    assert result.written and not (copies / WORK).exists()


# Issue #482 (5): a short work folder name; paths too long for Windows refused ------------


def test_the_copies_are_written_in_the_short_work_folder(tmp_path, monkeypatch):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    seen = []

    def written(source, target, *args, **kwargs):
        seen.append(target.relative_to(copies).as_posix())
        raise OSError("stop")

    monkeypatch.setattr(write_module, "write_named_copy", written)
    assert not convert_experiment(exp, SWAPPED, copies).written
    assert seen == [".mbconv/files/ctrl/r1/a.tif"]


def test_a_work_folder_of_a_conversion_before_482_is_recovered(tmp_path):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    _leftover(copies, {"kind": KIND, "holder": _holder()}, name=LEGACY)
    result = convert_experiment(exp, SWAPPED, copies)
    assert result.written and not (copies / LEGACY).exists() and not (copies / WORK).exists()


def test_a_forged_work_folder_under_the_name_before_482_is_refused_and_kept(tmp_path):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    _leftover(copies, {"kind": KIND}, name=LEGACY)
    before = _tree(copies)
    result = convert_experiment(exp, SWAPPED, copies)
    assert not result.written and codes(result.issues) == ["conversion_work_folder_unowned"]
    assert result.issues[0].paths == (LEGACY,)
    assert _tree(copies) == before


@pytest.mark.parametrize(("store", "copy"), STORES)
def test_a_copy_path_too_long_for_windows_is_refused_when_planned(
    tmp_path, monkeypatch, store, copy
):
    _store(store)
    exp = swapped_experiment(tmp_path / "exp")
    copies = tmp_path / ("c" * (259 - len(str(tmp_path)) - len("/.mbconv/files/ctrl/r1/a.") + 1))
    monkeypatch.setattr(plan_module, "_windows", lambda: True)
    result = convert_experiment(exp, SWAPPED, copies, store=store)
    assert not result.written and codes(result.issues) == ["copy_path_too_long"]
    assert copy in result.issues[0].paths and "259 characters" in result.issues[0].message
    assert not copies.exists()
    monkeypatch.setattr(plan_module, "_windows", lambda: False)  # elsewhere: no such limit
    assert convert_experiment(exp, SWAPPED, copies, store=store).written


def test_a_copy_path_that_fits_windows_is_written(tmp_path, monkeypatch):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    monkeypatch.setattr(plan_module, "_windows", lambda: True)
    assert convert_experiment(exp, SWAPPED, copies).written


def test_a_successful_conversion_leaves_no_work_folder(tmp_path):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    assert convert_experiment(exp, SWAPPED, copies).written
    assert not (copies / WORK).exists() and not list(copies.rglob("*.partial"))


# Issue #467 (5): a link swapped in during the conversion ----------------------------------


@pytest.fixture
def linkable(tmp_path):
    if not _can_link(tmp_path):
        pytest.skip("symbolic links cannot be made here (Windows without the privilege)")
    return tmp_path


def test_a_link_swapped_in_while_the_copies_are_written_is_refused_before_the_move(
    linkable, monkeypatch
):
    exp, copies, elsewhere = (
        swapped_experiment(linkable / "exp"),
        linkable / "copies",
        linkable / "elsewhere",
    )
    real, calls, at_swap = write_module.write_named_copy, [], {}

    def swapping(source, target, *args, **kwargs):
        calls.append(target)
        real(source, target, *args, **kwargs)
        if len(calls) == 3:  # every copy written: a link appears where one will go
            if (copies / "drug").exists():
                os.rename(copies / "drug", elsewhere)
            else:
                elsewhere.mkdir()
            (copies / "drug").symlink_to(elsewhere, target_is_directory=True)
            at_swap.update(_tree(elsewhere))

    monkeypatch.setattr(write_module, "write_named_copy", swapping)
    result = convert_experiment(exp, SWAPPED, copies)
    assert not result.written and codes(result.issues) == ["copy_folder_has_links"]
    assert result.issues[0].paths[0].startswith("drug ->")
    assert _tree(elsewhere) == at_swap and not (elsewhere / "r1/a.tif").exists()
    assert sorted(p.name for p in copies.iterdir()) == ["drug"]  # the link, nothing else


def test_the_clean_up_after_a_failure_never_removes_through_a_link(linkable, monkeypatch):
    exp, copies, elsewhere = (
        swapped_experiment(linkable / "exp"),
        linkable / "copies",
        linkable / "elsewhere",
    )
    elsewhere.mkdir()
    before = _tree(exp)
    real, moved = os.replace, []

    def swapping(source, target):
        moved.append(target)
        if len(moved) == 2:  # ctrl/r1/a.tif was moved into place: ctrl becomes a link
            os.rename(copies / "ctrl", elsewhere / "ctrl")
            (copies / "ctrl").symlink_to(exp / "ctrl", target_is_directory=True)
            raise OSError("device removed")
        return real(source, target)

    monkeypatch.setattr(write_module.os, "replace", swapping)
    result = convert_experiment(exp, SWAPPED, copies)
    monkeypatch.undo()
    assert not result.written and codes(result.issues) == ["conversion_failed"]
    assert _tree(exp) == before, "an original was removed through the link"


def test_a_file_that_appears_where_a_copy_goes_is_never_overwritten(tmp_path, monkeypatch):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    real, calls = write_module.write_named_copy, []
    theirs = copies / "drug/r1/a.tif"

    def appearing(source, target, *args, **kwargs):
        calls.append(target)
        real(source, target, *args, **kwargs)
        if len(calls) == 3:  # every copy written: someone saves a file where one will go
            theirs.parent.mkdir(parents=True, exist_ok=True)
            theirs.write_bytes(b"theirs")

    monkeypatch.setattr(write_module, "write_named_copy", appearing)
    result = convert_experiment(exp, SWAPPED, copies)
    assert not result.written and codes(result.issues) == ["conversion_failed"]
    assert "appeared during the conversion" in result.issues[0].message
    assert theirs.read_bytes() == b"theirs"
    assert not (copies / "ctrl").exists() and not (copies / WORK).exists()
