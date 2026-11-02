"""Converting safely, and the run on the copies (design v0.4 §5, §6, PP-11).

A conversion holds the copy tree's lock, writes ``.partial`` files and moves them into place
together, writes the record last, reuses unchanged copies and refuses to overwrite anything
else. The second run, on the copy tree with the same output folder, reads the copies like
any other images (``axes_from: "header"``), recomputes every row, records the record's
SHA-256 and warns when a source or a copy changed after the conversion.
"""

import json
import os

import numpy as np
import pytest
import tifffile

import morphobricks_preprocessing.conversion as conversion_module
from morphobricks_preprocessing import (
    CONVERSION_RECORD_FILENAME,
    AxisMapping,
    RunInputs,
    RunInputsError,
    convert_experiment,
    file_sha256,
    read_conversion_record,
    run_experiment,
)
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.run.lock import LOCK_FILENAME, output_lock
from morphobricks_preprocessing.testing import _confirmed_design

SWAPPED = AxisMapping("ZYX", ("Z", "X", "Y"))
PLAIN = AxisMapping("IYX", ("Z", "Y", "X"))


def swapped_experiment(root, names=("ctrl/r1/a.tif", "ctrl/r1/b.tif", "drug/r1/a.tif")):
    """Images whose OME header names Y and X the other way round from the truth."""
    rng = np.random.default_rng(1)
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = rng.integers(0, 255, (2, 6, 9), dtype=np.uint8)
        tifffile.imwrite(
            path, data, ome=True, photometric="minisblack",
            metadata={"axes": "ZYX", "PhysicalSizeZ": 2.0},
        )  # fmt: skip
    return root


def plain_experiment(root):
    rng = np.random.default_rng(2)
    for name in ("ctrl/r1/a.tif", "drug/r1/a.tif"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = rng.integers(0, 255, (3, 7, 5), dtype=np.uint8)
        tifffile.imwrite(path, data, photometric="minisblack", metadata=None)
    return root


def codes(issues):
    return [issue.code for issue in issues]


def snapshot(folder):
    return {
        p.relative_to(folder).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


# Safe to repeat -------------------------------------------------------------------------


def test_a_second_conversion_reuses_the_unchanged_copies_and_writes_nothing(tmp_path):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    first = convert_experiment(exp, SWAPPED, copies)
    assert first.written and len(first.converted) == 3
    before = snapshot(copies)
    again = convert_experiment(exp, SWAPPED, copies)
    assert again.written and again.converted == () and again.copied == ()
    assert again.reused == first.converted
    files = {k: v for k, v in snapshot(copies).items() if k != CONVERSION_RECORD_FILENAME}
    assert files == {k: v for k, v in before.items() if k != CONVERSION_RECORD_FILENAME}


def test_a_new_image_is_added_to_an_earlier_copy_tree(tmp_path):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    assert convert_experiment(exp, SWAPPED, copies).written
    swapped_experiment(exp, names=("drug/r1/new.tif",))
    again = convert_experiment(exp, SWAPPED, copies)
    assert again.written and again.converted == ("drug/r1/new.tif",)
    assert len(again.reused) == 3
    record = read_conversion_record(copies)[0]
    assert [e.relative_path for e in record.files] == sorted([*again.reused, "drug/r1/new.tif"])


def test_a_different_existing_copy_is_never_overwritten(tmp_path):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    assert convert_experiment(exp, SWAPPED, copies).written
    before = snapshot(copies)
    # Other roles for the same images: the copies would differ.
    other = convert_experiment(exp, AxisMapping("ZYX", ("T", "X", "Y")), copies)
    assert not other.written and codes(other.issues)[0] == "copy_differs"
    assert set(other.issues[0].paths) == {"ctrl/r1/a.tif", "ctrl/r1/b.tif", "drug/r1/a.tif"}
    # A source changed since: refused too.
    source = exp / "ctrl" / "r1" / "a.tif"
    tifffile.imwrite(source, np.ones((2, 6, 9), np.uint8), ome=True, metadata={"axes": "ZYX"})
    changed = convert_experiment(exp, SWAPPED, copies)
    assert not changed.written and changed.issues[0].paths == ("ctrl/r1/a.tif",)
    assert snapshot(copies) == before


def test_a_copy_recorded_with_other_pixel_sizes_is_not_reused(tmp_path):
    # Review of PP-11 (issues #92 and #96's criteria): the copy's scales are checked before
    # it is reused, so copies written under another rule are never mixed with new ones.
    exp, copies = tmp_path / "exp", tmp_path / "copies"
    path = exp / "c" / "img.tif"
    path.parent.mkdir(parents=True)
    tifffile.imwrite(
        path, np.zeros((2, 6, 9), np.uint8), ome=True, photometric="minisblack",
        metadata={"axes": "ZYX", "PhysicalSizeX": 0.5, "PhysicalSizeY": 0.5},
    )  # fmt: skip
    assert convert_experiment(exp, SWAPPED, copies).written
    record = json.loads((copies / CONVERSION_RECORD_FILENAME).read_text())
    sizes = record["files"][0]["pixel_sizes"]
    assert [s["value"] for s in sizes if s["role"] in "XY"] == [0.5, 0.5]
    for size in sizes:
        if size["role"] in "XY":
            size.update(value=None, unit=None, source=None)
    record["files"][0]["unknown_sizes"] = {"X": "relabelled", "Y": "relabelled"}
    (copies / CONVERSION_RECORD_FILENAME).write_text(json.dumps(record))
    again = convert_experiment(exp, SWAPPED, copies)
    assert not again.written and codes(again.issues) == ["copy_differs"]
    assert again.issues[0].paths == ("c/img.tif",)


def test_a_folder_holding_other_files_or_another_experiments_copies_is_refused(tmp_path):
    exp = swapped_experiment(tmp_path / "exp")
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "notes.txt").write_text("mine")
    result = convert_experiment(exp, SWAPPED, busy)
    assert not result.written and codes(result.issues) == ["copy_folder_not_empty"]
    assert sorted(p.name for p in busy.iterdir()) == ["notes.txt"]
    other = swapped_experiment(tmp_path / "other")
    assert convert_experiment(other, SWAPPED, tmp_path / "copies").written
    refused = convert_experiment(exp, SWAPPED, tmp_path / "copies")
    assert not refused.written and "another experiment" in refused.issues[0].message


def test_a_failed_conversion_leaves_the_folder_as_it_was(tmp_path, monkeypatch):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    original, calls = conversion_module.write_named_copy, []

    def failing(source, target, *args, **kwargs):
        calls.append(target)
        if len(calls) == 2:
            target.write_bytes(b"half")
            raise OSError("disk full")
        return original(source, target, *args, **kwargs)

    monkeypatch.setattr(conversion_module, "write_named_copy", failing)
    result = convert_experiment(exp, SWAPPED, copies)
    assert not result.written and codes(result.issues) == ["conversion_failed"]
    assert result.issues[0].paths == ("ctrl/r1/b.tif",) and "disk full" in result.issues[0].message
    assert [p for p in copies.rglob("*") if p.is_file()] == [], "no copy, no record, no lock"
    monkeypatch.undo()
    assert convert_experiment(exp, SWAPPED, copies).written


@pytest.mark.parametrize(
    ("error", "reported"),
    [(ValueError("data too large for non-BigTIFF file"), True), (KeyboardInterrupt(), False)],
)
def test_any_failure_or_interruption_removes_the_partial_files_and_new_folders(
    tmp_path, monkeypatch, error, reported
):
    # Review of PP-11: an error other than OSError (tifffile raises ValueError) or an
    # interruption left a .partial file and the folders behind.
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    original, calls = conversion_module.write_named_copy, []

    def failing(source, target, *args, **kwargs):
        calls.append(target)
        if len(calls) == 2:
            target.write_bytes(b"half")
            raise error
        return original(source, target, *args, **kwargs)

    monkeypatch.setattr(conversion_module, "write_named_copy", failing)
    if reported:
        result = convert_experiment(exp, SWAPPED, copies)
        assert not result.written and codes(result.issues) == ["conversion_failed"]
        assert "data too large" in result.issues[0].message
    else:
        with pytest.raises(KeyboardInterrupt):
            convert_experiment(exp, SWAPPED, copies)
    assert list(copies.iterdir()) == [], "no copy, no folder, no record, no lock"


def test_a_failure_while_moving_the_copies_into_place_removes_the_moved_ones(tmp_path, monkeypatch):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    original, moved = os.replace, []

    def failing(source, target):
        moved.append(target)
        if len(moved) == 2:
            raise OSError("device removed")
        return original(source, target)

    monkeypatch.setattr(conversion_module.os, "replace", failing)
    result = convert_experiment(exp, SWAPPED, copies)
    monkeypatch.undo()
    assert not result.written and codes(result.issues) == ["conversion_failed"]
    assert result.issues[0].paths == ("ctrl/r1/b.tif",)
    assert list(copies.iterdir()) == []
    assert convert_experiment(exp, SWAPPED, copies).written


def test_a_conversion_that_would_not_fit_is_refused_before_writing(tmp_path, monkeypatch):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    needed = 3 * 2 * 6 * 9  # three converted images of 2 x 6 x 9 uint8 pixels
    monkeypatch.setattr(conversion_module, "free_space", lambda path: needed - 1)
    result = convert_experiment(exp, SWAPPED, copies)
    assert not result.written and codes(result.issues) == ["not_enough_space"]
    assert "Nothing was written" in result.issues[0].message
    assert [p for p in copies.rglob("*")] == []
    monkeypatch.setattr(conversion_module, "free_space", lambda path: needed)
    assert convert_experiment(exp, SWAPPED, copies).written


def test_partial_files_left_by_an_interrupted_conversion_are_written_again(tmp_path):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    left = copies / "ctrl" / "r1" / "a.tif.partial"
    left.parent.mkdir(parents=True)
    left.write_bytes(b"interrupted")
    result = convert_experiment(exp, SWAPPED, copies)
    assert result.written and not left.exists()
    assert tifffile.imread(copies / "ctrl" / "r1" / "a.tif").shape == (2, 9, 6)


def test_the_copy_trees_lock_refuses_a_second_conversion(tmp_path):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    copies.mkdir()
    with output_lock(copies):
        result = convert_experiment(exp, SWAPPED, copies)
    assert not result.written and codes(result.issues) == ["output_locked"]
    assert not (copies / CONVERSION_RECORD_FILENAME).exists()
    assert not (copies / LOCK_FILENAME).exists()


# The run on the copies -------------------------------------------------------------------


def test_the_run_on_the_copies_reads_them_with_their_named_axes(tmp_path):
    exp, out = swapped_experiment(tmp_path / "exp"), tmp_path / "out"
    first = run_experiment(exp, SWAPPED, output_folder=out, design=_confirmed_design(exp))
    assert not first.written and "axes_yx_swapped" in codes(first.issues)
    result = convert_experiment(exp, SWAPPED, tmp_path / "copies")
    copies = tmp_path / "copies"
    run = run_experiment(copies, AxisMapping("ZYX", tuple("ZYX")), output_folder=out)
    assert run.written, run.issues
    manifest = json.loads((out / "run_manifest.json").read_text())
    assert manifest["experiment_root"] == copies.as_posix()
    assert manifest["conversion_record_sha256"] == result.record_sha256
    assert result.record_sha256 == file_sha256(copies / CONVERSION_RECORD_FILENAME)
    assert {f["axes_from"] for f in manifest["files"]} == {"header"}
    report = json.loads((out / "discovery_report.json").read_text())
    assert report["conversion"]["source_root"] == exp.as_posix()
    assert "named-axis copies of" in (out / "discovery_report.txt").read_text()
    # The FOV keys and plane identities are the originals' (same relative paths).
    inputs = RunInputs.open(out)
    assert [f.relative_path for f in inputs.files] == sorted(
        p.relative_to(exp).as_posix() for p in exp.rglob("*.tif")
    )
    np.testing.assert_array_equal(
        inputs.read_plane(inputs.fovs()[0], z=1)[0],
        tifffile.imread(exp / "ctrl" / "r1" / "a.tif")[1].T,
    )


def test_run_inputs_refuses_a_conversion_record_changed_or_gone_since_the_run(tmp_path):
    # Owner decision of 2 November 2026 (plan question 59, (14)): RunInputs.open checks the
    # manifest's conversion_record_sha256, like calibration_sha256 and the design's SHA-256.
    exp, out = swapped_experiment(tmp_path / "exp"), tmp_path / "out"
    copies = tmp_path / "copies"
    assert convert_experiment(exp, SWAPPED, copies).written
    assert run_experiment(copies, AxisMapping("ZYX", tuple("ZYX")), output_folder=out).written
    record = copies / CONVERSION_RECORD_FILENAME
    RunInputs.open(out)  # the record as the run read it
    moved = tmp_path / "moved"
    copies.rename(moved)
    RunInputs.open(out, experiment_root=moved)  # the copies moved, with their record
    data = (moved / CONVERSION_RECORD_FILENAME).read_bytes()
    (moved / CONVERSION_RECORD_FILENAME).write_bytes(data.replace(b'"converted"', b'"copied"', 1))
    with pytest.raises(RunInputsError, match="conversion record .* changed since"):
        RunInputs.open(out, experiment_root=moved)
    (moved / CONVERSION_RECORD_FILENAME).unlink()
    with pytest.raises(RunInputsError, match="missing"):
        RunInputs.open(out, experiment_root=moved)
    moved.rename(copies)
    record.write_bytes(data)  # bytes: write_text would add CR on Windows
    RunInputs.open(out)  # the same bytes again: accepted


def test_a_conversion_reusing_every_copy_keeps_the_record_so_the_run_stays_valid(tmp_path):
    # Review of PP-11: a rerun that reused every copy rewrote the record (a new written_at),
    # so its SHA-256 changed and RunInputs then refused the run on the copies.
    exp, out = plain_experiment(tmp_path / "exp"), tmp_path / "out"
    copies = tmp_path / "copies"
    first = convert_experiment(exp, PLAIN, copies)
    assert first.written and "axes_not_named" not in codes(first.issues)
    assert run_experiment(copies, AxisMapping("ZYX", tuple("ZYX")), output_folder=out).written
    record = (copies / CONVERSION_RECORD_FILENAME).read_bytes()
    again = convert_experiment(exp, PLAIN, copies)
    assert again.written and again.reused == first.converted
    assert again.record_sha256 == first.record_sha256
    assert (copies / CONVERSION_RECORD_FILENAME).read_bytes() == record
    RunInputs.open(out)


def test_every_row_is_recomputed_and_equals_the_originals_rows(tmp_path):
    exp, out = plain_experiment(tmp_path / "exp"), tmp_path / "out"
    design = _confirmed_design(exp)
    first = run_experiment(exp, PLAIN, output_folder=out, design=design)
    assert first.written and "axes_not_named" in codes(first.issues)
    rows = (out / "plane_histograms.csv").read_text()
    assert convert_experiment(exp, PLAIN, tmp_path / "copies").written
    run = run_experiment(tmp_path / "copies", AxisMapping("ZYX", tuple("ZYX")), output_folder=out)
    assert run.written and "axes_not_named" not in codes(run.issues)
    assert len(run.read) == 2 and run.unchanged == ()
    assert "experiment_root" in [c.name for c in run.input_changes]
    # Same plane identities and the same histograms: the copies hold the same pixels.
    assert (out / "plane_histograms.csv").read_text() == rows
    report = json.loads((out / "discovery_report.json").read_text())
    assert report["experiment_root_changed"] and report["design"] is not None


def test_the_run_warns_when_a_source_or_a_copy_changed_after_the_conversion(tmp_path):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    assert convert_experiment(exp, SWAPPED, copies).written
    (exp / "ctrl" / "r1" / "a.tif").unlink()
    copy = copies / "drug" / "r1" / "a.tif"
    os.utime(copy, ns=(copy.stat().st_atime_ns, copy.stat().st_mtime_ns + 10**9))
    run = run_experiment(copies, AxisMapping("ZYX", tuple("ZYX")), output_folder=tmp_path / "o")
    assert run.written
    found = {issue.code: issue for issue in run.issues}
    assert found["conversion_source_changed"].paths == ("ctrl/r1/a.tif",)
    assert found["copy_changed_after_conversion"].paths == ("drug/r1/a.tif",)


def test_an_unreadable_conversion_record_stops_the_run(tmp_path):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    assert convert_experiment(exp, SWAPPED, copies).written
    (copies / CONVERSION_RECORD_FILENAME).write_text("{not json")
    run = run_experiment(copies, AxisMapping("ZYX", tuple("ZYX")), output_folder=tmp_path / "o")
    assert not run.written and "conversion_record_unreadable" in codes(run.issues)


def test_masks_are_never_converted_and_are_checked_against_the_copies(tmp_path):
    exp = tmp_path / "exp"
    path = exp / "c" / "img.tif"
    path.parent.mkdir(parents=True)
    tifffile.imwrite(path, np.zeros((6, 9), np.uint8), ome=True, metadata={"axes": "YX"})
    mask = tmp_path / "masks" / "Cells" / "c" / "img.tif"
    mask.parent.mkdir(parents=True)
    tifffile.imwrite(mask, np.ones((9, 6), np.uint16), metadata={"axes": "YX"})
    before = mask.read_bytes()
    roles = AxisMapping("YX", ("X", "Y"))
    assert convert_experiment(exp, roles, tmp_path / "copies").written
    run = run_experiment(
        tmp_path / "copies", AxisMapping("YX", ("Y", "X")), output_folder=tmp_path / "out",
        masks_folder=tmp_path / "masks",
    )  # fmt: skip
    assert run.written, run.issues
    assert mask.read_bytes() == before
    assert not list((tmp_path / "copies").rglob("*Cells*"))


# Command line ----------------------------------------------------------------------------


def test_the_convert_command_writes_the_copies_and_names_the_next_commands(tmp_path, capsys):
    exp = swapped_experiment(tmp_path / "exp")
    copies = tmp_path / "copies"
    assert main(["convert", str(exp), "--to", str(copies), "--axes", "ZXY"]) == 0
    out = capsys.readouterr().out
    assert "[1/3] ctrl/r1/a.tif" in out
    assert "written as OME-TIFF with your roles: 3 images" in out
    assert f"morphobricks-preprocessing run {copies} --output" in out and "--axes ZYX" in out
    assert main(["convert", str(exp), "--to", str(copies), "--axes", "ZXY"]) == 0
    assert "already there and unchanged: 3 images" in capsys.readouterr().out


def test_the_convert_command_refuses_and_exits_1(tmp_path, capsys):
    exp = swapped_experiment(tmp_path / "exp")
    assert main(["convert", str(exp), "--to", str(exp / "inside"), "--axes", "ZXY"]) == 1
    assert "The copies must be kept apart from the originals" in capsys.readouterr().out
    assert main(["convert", str(exp), "--to", str(tmp_path / "c"), "--axes", "ZX"]) == 1
    assert "does not fit the images" in capsys.readouterr().out


@pytest.mark.skipif(os.name != "posix", reason="a stale lock of a finished process")
def test_the_convert_command_removes_a_stale_lock_only_on_request(tmp_path, capsys):
    from run_files import finished_pid

    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    copies.mkdir()
    lock = copies / LOCK_FILENAME
    lock.write_text(json.dumps({"host": os.uname().nodename, "pid": finished_pid()}))
    assert main(["convert", str(exp), "--to", str(copies), "--axes", "ZXY"]) == 1
    assert "Another conversion is writing into this folder" in capsys.readouterr().out
    args = ["convert", str(exp), "--to", str(copies), "--axes", "ZXY", "--remove-stale-lock"]
    assert main(args) == 0
    assert "Removed the lock" in capsys.readouterr().out


def test_an_ome_zarr_image_is_copied_as_it_is(tmp_path):
    from zarr_tree import write_ngff

    exp = plain_experiment(tmp_path / "exp")
    store = write_ngff(exp / "ctrl" / "r1" / "z.zarr", np.ones((3, 7, 5), np.uint8), "zyx")
    result = convert_experiment(exp, PLAIN, tmp_path / "copies")
    assert result.written and result.copied == ("ctrl/r1/z.zarr",)
    copy = tmp_path / "copies" / "ctrl" / "r1" / "z.zarr"
    found = {p.relative_to(copy).as_posix(): p.read_bytes() for p in copy.rglob("*") if p.is_file()}
    assert found == {
        p.relative_to(store).as_posix(): p.read_bytes() for p in store.rglob("*") if p.is_file()
    }
    run = run_experiment(
        tmp_path / "copies", AxisMapping("ZYX", tuple("ZYX")), output_folder=tmp_path / "o"
    )
    assert run.written, run.issues
