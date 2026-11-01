"""Manifest version 2, the row key and per-file plain-stack confirmation (design v0.4 PP-4).

- Every file records its roles, where they come from (``axes_from``) and its pixel sizes
  with their source; masks record their fingerprint and roles (B3).
- A version 1 manifest (before the axis check) gives no reusable row.
- A row is reused only when its :class:`RowKey` is the earlier run's (D5).
- A plain stack's confirmation covers only the files it was given for (audit 2 B-1).
"""

import json
from dataclasses import replace

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    AxisMapping,
    prefill_mapping,
    read_manifest,
    review_axes,
    run_experiment,
)
from morphobricks_preprocessing.axis_check import ConfirmedAxes
from morphobricks_preprocessing.fingerprint import Fingerprint, RowKey, confirm_axes
from morphobricks_preprocessing.manifest import (
    MANIFEST_VERSION,
    FileEntry,
    ManifestFormatError,
    RunSettings,
    source_fingerprint,
    write_manifest,
)
from morphobricks_preprocessing.recorded_sizes import RecordedSize
from tiff_tree import write_data, write_plain


def stack(seed=0):
    return np.random.default_rng(seed).integers(0, 200, (3, 6, 8), dtype=np.uint8)


def codes(issues):
    return [i.code for i in issues if i.is_error]


# What version 2 records ------------------------------------------------------------------


def test_version_2_records_roles_their_source_and_pixel_sizes(tmp_path):
    root = tmp_path / "exp"
    (root / "ctrl").mkdir(parents=True)
    tifffile.imwrite(
        root / "ctrl/a.ome.tif",
        stack(),
        ome=True,
        photometric="minisblack",
        metadata={"axes": "ZYX", "PhysicalSizeX": 0.5, "PhysicalSizeY": 0.25},
    )
    write_plain(root / "ctrl/b.tif", stack(1))
    result = run_experiment(root, AxisMapping("ZYX", "ZYX"))
    assert not result.written  # one arrangement per experiment: ZYX and IYX differ
    result = run_experiment(root, prefill_mapping("ZYX"), exclude=["ctrl/b.tif"])
    data = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert data["manifest_version"] == MANIFEST_VERSION == "2"
    (entry,) = data["files"]
    assert entry["roles"] == ["Z", "Y", "X"] and entry["axes_from"] == "header"
    sizes = {size["role"]: size for size in entry["pixel_sizes"]}
    assert sizes["X"] == {"role": "X", "value": 0.5, "unit": "micrometer", "source": "header"}
    assert sizes["Y"]["value"] == 0.25
    assert sizes["Z"] == {"role": "Z", "value": None, "unit": None, "source": None}
    assert data["conversion_record_sha256"] is None


def test_a_plain_stack_is_recorded_as_confirmed_and_uncalibrated(tmp_path):
    root = tmp_path / "exp"
    write_plain(root / "ctrl/a.tif", stack())
    result = run_experiment(root, AxisMapping("IYX", "TYX"))
    (entry,) = read_manifest(result.manifest_path).files
    assert (entry.roles, entry.axes_from) == (("T", "Y", "X"), "confirmed")
    assert all(size.value is None and size.source is None for size in entry.pixel_sizes)


def test_masks_record_their_fingerprint_and_roles(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "ctrl/a.tif", np.zeros((6, 8), np.uint8), "YX")
    mask = write_data(tmp_path / "masks/Cells/ctrl/a.tif", np.ones((6, 8), np.uint16), "YX")
    result = run_experiment(root, prefill_mapping("YX"), masks_folder=tmp_path / "masks")
    (recorded,) = read_manifest(result.manifest_path).masks["files"]
    size, mtime_ns = source_fingerprint(mask)
    assert recorded == {
        "image": "ctrl/a.tif",
        "type": "Cells",
        "path": "Cells/ctrl/a.tif",
        "source": "folder",
        "axes": "YX",
        "shape": [6, 8],
        "size": size,
        "mtime_ns": mtime_ns,
        "roles": "YX",
        "axes_from": "header",
        "shared": False,
    }


def test_the_reader_requires_roles_and_their_source_in_version_2(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "ctrl/a.tif", stack(), "ZYX")
    path = run_experiment(root, prefill_mapping("ZYX")).manifest_path
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["files"][0]["axes_from"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ManifestFormatError, match="has no roles or axes_from"):
        read_manifest(path)


def test_the_writer_refuses_an_entry_without_roles(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "ctrl/a.tif", stack(), "ZYX")
    manifest = read_manifest(run_experiment(root, prefill_mapping("ZYX")).manifest_path)
    broken = replace(manifest, files=(replace(manifest.files[0], axes_from=None),))
    with pytest.raises(ValueError, match="has no roles or axes_from"):
        write_manifest(broken, tmp_path / "m.json")


def test_recorded_sizes_are_checked():
    assert RecordedSize.from_dict({"role": "X", "value": 1, "unit": "nm", "source": "header"})
    with pytest.raises(ValueError, match="both a value and a source"):
        RecordedSize.from_dict({"role": "X", "value": 1, "unit": "nm", "source": None})
    with pytest.raises(ValueError, match="unknown role or source"):
        RecordedSize.from_dict({"role": "C", "value": None, "source": None})


# Version 1 and the row key -----------------------------------------------------------------


def test_a_version_1_run_gives_no_reusable_row(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "ctrl/a.tif", stack(), "ZYX")
    first = run_experiment(root, prefill_mapping("ZYX"))
    data = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    data["manifest_version"] = "1"
    for entry in data["files"]:
        del entry["axes_from"], entry["pixel_sizes"]
    first.manifest_path.write_text(json.dumps(data), encoding="utf-8")
    assert read_manifest(first.manifest_path).manifest_version == "1"
    again = run_experiment(root, prefill_mapping("ZYX"))
    assert (
        again.rebuild_reason == "the earlier run was written before v0.4.0, without the axis check"
    )
    assert again.read == ("ctrl/a.tif",)
    assert read_manifest(again.manifest_path).manifest_version == "2"


def test_rows_are_reused_only_for_the_same_row_key(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "ctrl/a.tif", stack(), "ZYX")
    first = run_experiment(root, prefill_mapping("ZYX"))
    assert run_experiment(root, prefill_mapping("ZYX")).read == ()
    # The same experiment moved elsewhere: its rows are read again from the new place.
    moved = tmp_path / "moved"
    root.rename(moved)
    again = run_experiment(moved, prefill_mapping("ZYX"), output_folder=first.output_folder)
    assert again.changed == ("ctrl/a.tif",) and again.read == ("ctrl/a.tif",)


def test_the_row_key():
    settings = RunSettings(256, 2**30)

    entry = FileEntry(
        "a.tif", 10, 20, ("c", "b", "t"), "ZYX", roles=("Z", "Y", "X"), axes_from="header"
    )
    key = RowKey.of(entry, settings, "/exp")
    assert key == RowKey.of(entry, settings, "/exp")
    assert key.fingerprint == Fingerprint(10, 20)
    for changed in (
        replace(entry, size=11),
        replace(entry, roles=("T", "Y", "X")),
        replace(entry, axes_from="confirmed"),
        replace(entry, labels=("c", "b", "u")),
    ):
        assert RowKey.of(changed, settings, "/exp") != key
    assert RowKey.of(entry, settings, "/moved") != key
    assert RowKey.of(entry, RunSettings(512, 2**30), "/exp") != key


def test_fingerprint_of_a_file(tmp_path):
    path = write_data(tmp_path / "a.tif", stack(), "ZYX")
    assert Fingerprint.of(path) == Fingerprint(*source_fingerprint(path))


# A plain stack's confirmation covers only its files (audit 2 B-1) ---------------------------


def test_a_confirmation_covers_only_the_files_it_was_given_for(tmp_path):
    root = tmp_path / "exp"
    write_plain(root / "ctrl/a.tif", stack())
    write_plain(root / "ctrl/b.tif", stack(1))  # added after the roles were confirmed for a.tif
    confirmed = confirm_axes(AxisMapping("IYX", "TYX"), root, ["ctrl/a.tif"])
    result = run_experiment(root, confirmed)
    assert codes(result.issues) == ["plain_stack_not_confirmed"]
    (issue,) = [i for i in result.issues if i.code == "plain_stack_not_confirmed"]
    assert issue.paths == ("ctrl/b.tif",) and "confirm them again" in issue.action
    both = confirm_axes(AxisMapping("IYX", "TYX"), root, ["ctrl/a.tif", "ctrl/b.tif"])
    assert run_experiment(root, both).written
    # A plain mapping confirms every file the run finds, as before.
    assert run_experiment(root, AxisMapping("IYX", "TYX")).written


def test_named_images_need_no_confirmation(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "ctrl/a.tif", stack(), "ZYX")
    write_data(root / "ctrl/b.tif", stack(1), "ZYX")
    assert review_axes([], ConfirmedAxes(prefill_mapping("ZYX"), {})).ok
    confirmed = confirm_axes(prefill_mapping("ZYX"), root, ["ctrl/a.tif"])
    assert run_experiment(root, confirmed).written


def test_a_plain_stack_changed_since_its_confirmation_is_not_covered(tmp_path):
    root = tmp_path / "exp"
    write_plain(root / "ctrl/a.tif", stack())
    write_plain(root / "ctrl/b.tif", stack(1))
    confirmed = confirm_axes(AxisMapping("IYX", "TYX"), root, ["ctrl/a.tif", "ctrl/b.tif"])
    assert run_experiment(root, confirmed).written
    write_plain(root / "ctrl/b.tif", np.ones((4, 6, 8), np.uint8))  # changed since
    result = run_experiment(root, confirmed)
    assert not result.written and result.read == ()
    (issue,) = [i for i in result.issues if i.code == "plain_stack_not_confirmed"]
    assert issue.paths == ("ctrl/b.tif",)
    again = confirm_axes(AxisMapping("IYX", "TYX"), root, ["ctrl/a.tif", "ctrl/b.tif"])
    assert run_experiment(root, again).read == ("ctrl/b.tif",)


def test_a_confirmation_records_fingerprints():
    with pytest.raises(TypeError, match="fingerprint"):
        ConfirmedAxes(AxisMapping("IYX", "TYX"), {"ctrl/a.tif"})
