"""A conversion removes only what it can prove it wrote (issues #448 and #467).

Issue #448: a ``<name>.partial`` file or folder at the path of a planned copy was taken for
one left by an interrupted conversion and removed (``shutil.rmtree`` for a folder), whoever
had put it there. A conversion now writes its copies in its own work folder
(``.morphobricks-conversion.partial``), which it makes itself and marks with an owner file
before it writes any copy; a later conversion removes a work folder only when that owner
file is there. A ``.partial`` elsewhere is never removed: without a record it is refused as
any other file, with a record it is left alone.
"""

import hashlib
import json
import os

import pytest
import tifffile

import morphobricks_preprocessing.conversion_write as write_module
from morphobricks_preprocessing import convert_experiment
from morphobricks_preprocessing.conversion_record import RECORD_FILENAME
from test_convert_links import _tree
from test_convert_run import SWAPPED, codes, swapped_experiment

WORK = ".morphobricks-conversion.partial"
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


def test_a_successful_conversion_leaves_no_work_folder(tmp_path):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    assert convert_experiment(exp, SWAPPED, copies).written
    assert not (copies / WORK).exists() and not list(copies.rglob("*.partial"))
