"""Conversion integrity (PP-11c; issues #80, #87, #88, #92, #96; contract §9.1, §17).

- One metadata epoch: each image is fingerprinted before and after its header is read, and
  an image that changed meanwhile is refused before anything is written (#96).
- Each copy's SHA-256 is recorded, and reuse, the run's conversion review and ``RunInputs``
  check it, so a same-size change with the time put back is found (#87).
- Reuse compares the copy's channel names and significant bits (#88), and the reasons of
  the unknown sizes, not only which sizes are unknown (#92).
- The command line takes a memory size only as an exact whole number of bytes (#80).
"""

import argparse
import json
import os

import numpy as np
import pytest
import tifffile

import morphobricks_preprocessing.cli_convert as cli_convert
import morphobricks_preprocessing.cli_run as cli_run
import morphobricks_preprocessing.metadata as metadata_module
import morphobricks_preprocessing.ome_tiff_copy as copy_module
from described_tree import main_described, run_described
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
from morphobricks_preprocessing.cli import parse_size
from morphobricks_preprocessing.copy_axes import copy_layout
from morphobricks_preprocessing.metadata import inspect_image
from morphobricks_preprocessing.ome_tiff_copy import CopyWriteError, write_named_copy
from test_convert_run import SWAPPED, codes, snapshot, swapped_experiment

NAMED = AxisMapping("TYX", tuple("TYX"))


def change_in_place(path):
    """Change one pixel byte of a TIFF file, keeping its size and modification time."""
    stat = path.stat()
    with tifffile.TiffFile(path) as tif:
        offset = tif.pages[0].dataoffsets[0]
    data = bytearray(path.read_bytes())
    data[offset] ^= 0xFF
    path.write_bytes(bytes(data))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert (path.stat().st_size, path.stat().st_mtime_ns) == (stat.st_size, stat.st_mtime_ns)


def edit_record(copies, change):
    """Rewrite the conversion record after ``change(data)``; return the new SHA-256."""
    path = copies / CONVERSION_RECORD_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_bytes((json.dumps(data, indent=2) + "\n").encode())
    return file_sha256(path)


# #96: one metadata epoch -------------------------------------------------------------------


def test_an_image_changed_while_its_header_is_read_is_refused_and_nothing_written(
    tmp_path, monkeypatch
):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    target = exp / "ctrl" / "r1" / "b.tif"
    original, changed = metadata_module.inspect_tiff, []

    def inspect_then_change(path, *args, **kwargs):
        meta = original(path, *args, **kwargs)
        if os.path.samefile(path, target) and not changed:
            changed.append(path)  # new pixels and a new calibration, after the header read
            tifffile.imwrite(
                target, np.ones((2, 6, 9), np.uint8), ome=True,
                metadata={"axes": "TYX", "PhysicalSizeZ": 5.0, "PhysicalSizeX": 0.1},
            )  # fmt: skip
        return meta

    monkeypatch.setattr(metadata_module, "inspect_tiff", inspect_then_change)
    result = convert_experiment(exp, SWAPPED, copies)
    assert changed
    assert not result.written and codes(result.issues) == ["source_changed_during_check"]
    assert result.issues[0].paths == ("ctrl/r1/b.tif",)
    assert "Nothing was written" in result.issues[0].message
    assert not copies.exists() or [p for p in copies.rglob("*")] == []
    monkeypatch.undo()
    assert convert_experiment(exp, SWAPPED, copies).written  # settled now: converted


def test_writing_a_copy_does_not_read_the_source_header_again(tmp_path, monkeypatch):
    # #96: the pixel type comes from the inspected header; only the reader opens the file.
    monkeypatch.setattr(
        copy_module, "plane_layout", lambda *a, **k: pytest.fail("header read"), raising=False
    )
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    assert convert_experiment(exp, SWAPPED, copies).written


def test_a_plane_of_another_pixel_type_than_the_header_said_is_refused(tmp_path):
    exp = swapped_experiment(tmp_path / "exp", names=("a.tif",))
    meta = inspect_image(exp / "a.tif", "a.tif")
    layout = copy_layout(meta, SWAPPED)
    with pytest.raises(CopyWriteError, match="not uint16"):
        write_named_copy(exp / "a.tif", tmp_path / "a.tif", layout, SWAPPED, dtype=np.uint16)


# #87: the copies' SHA-256 ------------------------------------------------------------------


def test_each_copys_sha256_is_recorded(tmp_path):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    assert convert_experiment(exp, SWAPPED, copies).written
    data = json.loads((copies / CONVERSION_RECORD_FILENAME).read_text())
    assert data["record_version"] == "3"
    for entry in data["files"]:
        assert entry["copy_sha256"] == file_sha256(copies / entry["relative_path"])


def test_a_same_size_change_with_the_time_put_back_is_found_at_reuse(tmp_path):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    assert convert_experiment(exp, SWAPPED, copies).written
    change_in_place(copies / "ctrl" / "r1" / "b.tif")
    before = snapshot(copies)
    again = convert_experiment(exp, SWAPPED, copies)
    assert not again.written and codes(again.issues) == ["copy_differs"]
    assert again.issues[0].paths == ("ctrl/r1/b.tif",)
    assert snapshot(copies) == before


def test_the_run_on_the_copies_stops_on_a_same_size_change(tmp_path):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    assert convert_experiment(exp, SWAPPED, copies).written
    change_in_place(copies / "drug" / "r1" / "a.tif")
    run = run_described(copies, NAMED, output_folder=tmp_path / "out")
    found = {issue.code: issue for issue in run.issues}
    assert not run.written and found["copy_changed_after_conversion"].is_error
    assert found["copy_changed_after_conversion"].paths == ("drug/r1/a.tif",)


def test_run_inputs_refuses_a_copy_changed_since_it_was_written(tmp_path):
    exp, copies, out = swapped_experiment(tmp_path / "exp"), tmp_path / "copies", tmp_path / "o"
    assert convert_experiment(exp, SWAPPED, copies).written
    assert run_described(copies, NAMED, output_folder=out).written
    RunInputs.open(out)
    change_in_place(copies / "ctrl" / "r1" / "a.tif")
    with pytest.raises(RunInputsError, match="1 named-axis copy changed .*ctrl/r1/a.tif"):
        RunInputs.open(out)


def test_an_ome_zarr_copy_changed_in_place_is_not_reused(tmp_path):
    from zarr_tree import write_ngff

    exp, copies = swapped_experiment(tmp_path / "exp", names=("c/a.tif",)), tmp_path / "copies"
    # Another arrangement than the TIFFs', so it is matched by its declared roles: a "zyx"
    # store under the experiment's swapped roles contradicts its metadata (issue #72).
    pixels = np.arange(2 * 9 * 6, dtype=np.uint8).reshape(2, 1, 9, 6)  # one z: not a Z-stack
    write_ngff(exp / "c" / "z.zarr", pixels, "tzyx")
    assert convert_experiment(exp, SWAPPED, copies).written
    chunk = max(
        (p for p in (copies / "c" / "z.zarr").rglob("*") if p.is_file()),
        key=lambda p: p.stat().st_size,
    )
    stat = chunk.stat()
    data = bytearray(chunk.read_bytes())
    data[-1] ^= 0xFF
    chunk.write_bytes(bytes(data))
    os.utime(chunk, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    again = convert_experiment(exp, SWAPPED, copies)
    assert not again.written and again.issues[0].paths == ("c/z.zarr",)


def test_a_version_1_record_is_refused_with_a_reason(tmp_path):
    exp, copies, out = swapped_experiment(tmp_path / "exp"), tmp_path / "copies", tmp_path / "o"
    assert convert_experiment(exp, SWAPPED, copies).written
    assert run_described(copies, NAMED, output_folder=out).written

    def version_1(data):
        data["record_version"] = "1"
        for entry in data["files"]:
            for key in ("copy_sha256", "channel_names", "significant_bits"):
                del entry[key]

    sha256 = edit_record(copies, version_1)
    again = convert_experiment(exp, SWAPPED, copies)
    assert not again.written and codes(again.issues) == ["conversion_record_unreadable"]
    assert "version 1" in again.issues[0].message and "convert" in again.issues[0].message
    # A run made on such a copy tree before PP-11c: RunInputs refuses, never skips the check.
    manifest = json.loads((out / "run_manifest.json").read_text())
    manifest["conversion_record_sha256"] = sha256
    (out / "run_manifest.json").write_bytes(json.dumps(manifest, indent=2).encode())
    with pytest.raises(RunInputsError, match="version 1.*Convert the originals again"):
        RunInputs.open(out)


# #88 and #92: what reuse compares ----------------------------------------------------------


def channel_experiment(root, sizes=None):
    path = root / "c" / "img.tif"
    path.parent.mkdir(parents=True)
    tifffile.imwrite(
        path, np.zeros((2, 6, 9), np.uint16), ome=True, photometric="minisblack",
        metadata={"axes": "CYX", "Channel": {"Name": ["DAPI", "Actin"]}, "SignificantBits": 12,
                  **(sizes or {})},
    )  # fmt: skip
    return root


CHANNELS = AxisMapping("CYX", ("C", "X", "Y"))


@pytest.mark.parametrize(
    "change",
    [
        lambda entry: entry.update(channel_names=["DAPI", "Tubulin"]),
        lambda entry: entry.update(channel_names=[]),
        lambda entry: entry.update(significant_bits=16),
        lambda entry: entry.update(significant_bits=None),
    ],
    ids=["channel name changed", "channel names missing", "bits changed", "bits missing"],
)
def test_a_copy_recorded_with_other_channel_names_or_bits_is_not_reused(tmp_path, change):
    exp, copies = channel_experiment(tmp_path / "exp"), tmp_path / "copies"
    assert convert_experiment(exp, CHANNELS, copies).written
    entry = read_conversion_record(copies)[0].entry("c/img.tif")
    assert entry.channel_names == ("DAPI", "Actin") and entry.significant_bits == 12
    edit_record(copies, lambda data: change(data["files"][0]))
    again = convert_experiment(exp, CHANNELS, copies)
    assert not again.written and codes(again.issues) == ["copy_differs"]


def test_a_copy_recorded_with_other_reasons_for_its_unknown_sizes_is_not_reused(tmp_path):
    sizes = {"PhysicalSizeX": 0.5, "PhysicalSizeY": 0.7}  # not equal: unknown after the swap
    exp, copies = channel_experiment(tmp_path / "exp", sizes), tmp_path / "copies"
    assert convert_experiment(exp, CHANNELS, copies).written
    unknown = read_conversion_record(copies)[0].files[0].unknown_sizes
    assert set(unknown) == {"X", "Y"}
    assert convert_experiment(exp, CHANNELS, copies).reused == ("c/img.tif",)

    def other_reasons(data):
        data["files"][0]["unknown_sizes"] = {role: "another reason" for role in unknown}

    edit_record(copies, other_reasons)
    again = convert_experiment(exp, CHANNELS, copies)
    assert not again.written and codes(again.issues) == ["copy_differs", "non_square_pixels"]


# #80: exact memory sizes -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "size"),
    [("1.5GiB", 3 * 2**29), ("4096.5KiB", 4096 * 1024 + 512), ("0.5KiB", 512)]
    + [("9007199254740993", 2**53 + 1), ("9007199254740993B", 2**53 + 1)],
)
def test_parse_size_is_exact(text, size):
    assert parse_size(text) == size and isinstance(parse_size(text), int)


@pytest.mark.parametrize("text", ["1.5", "0.5B", "0.5", "1.0000000001KiB", "0GiB", "0.0", "１"])
def test_parse_size_refuses_a_size_that_is_not_whole_bytes(text):
    with pytest.raises(argparse.ArgumentTypeError):
        parse_size(text)


@pytest.mark.parametrize("command", ["run", "convert"])
def test_the_commands_refuse_a_bad_memory_size_before_reading_anything(
    tmp_path, monkeypatch, capsys, command
):
    exp = swapped_experiment(tmp_path / "exp")
    for module in (cli_run, cli_convert):
        monkeypatch.setattr(module, "preflight", lambda *a, **k: pytest.fail("read"))
    args = [command, str(exp), "--axes", "TXY", "--memory-limit", "1.5"]
    if command == "convert":
        args += ["--to", str(tmp_path / "copies")]
    with pytest.raises(SystemExit) as exit_info:
        main_described(args)
    assert exit_info.value.code == 2
    assert "not a whole number of bytes" in capsys.readouterr().err
    assert not (tmp_path / "copies").exists()


def test_an_ome_zarr_copy_holding_a_folder_link_counts_as_changed(tmp_path):
    # Review of PP-11c: os.walk does not enter a link to a folder, so the files behind it
    # would be left out of the SHA-256; such a copy is never hashed in part.
    from morphobricks_preprocessing.content_hash import content_sha256, unchanged_content

    zarr = tmp_path / "z.zarr"
    (zarr / "0").mkdir(parents=True)
    (zarr / "0" / "c").write_bytes(b"a")
    (zarr / "zarr.json").write_text("{}")
    expected = content_sha256(zarr)
    (zarr / "0").rename(tmp_path / "elsewhere")
    try:
        (zarr / "0").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    except OSError:
        pytest.skip("folder links cannot be made here")
    with pytest.raises(OSError, match="link to a folder"):
        content_sha256(zarr)
    assert not unchanged_content(zarr, expected)


def test_an_added_copy_stops_the_run_and_the_earlier_run_is_kept(tmp_path):
    # Owner decision of 2 November 2026 (PP-12): an error, not a warning, since RunInputs
    # would refuse the run; the earlier clean run stays as it was.
    exp, copies, out = swapped_experiment(tmp_path / "exp"), tmp_path / "copies", tmp_path / "o"
    assert convert_experiment(exp, SWAPPED, copies).written
    assert run_described(copies, NAMED, output_folder=out).written
    table = (out / "plane_histograms.csv").read_bytes()
    (copies / "ctrl" / "r1" / "n.tif").write_bytes((copies / "ctrl" / "r1" / "a.tif").read_bytes())
    run = run_experiment(copies, NAMED, output_folder=out)  # the description as published
    found = {issue.code: issue for issue in run.issues}["copy_changed_after_conversion"]
    assert not run.written and found.is_error
    assert found.paths == ("ctrl/r1/n.tif",) and "refuse" in found.message
    assert "Convert the originals again into a new folder" in found.action
    assert (out / "plane_histograms.csv").read_bytes() == table
    with pytest.raises(RunInputsError, match="ctrl/r1/n.tif"):
        RunInputs.open(out)
