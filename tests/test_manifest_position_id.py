"""Round-2 audit A-m5: a version 2 manifest records each image's position_id, by its name.

``position_id`` is the image's name without its extension (``image_names.position_id``,
MorphoBricks D02 and D85), never parsed or edited. The v2 reader accepted a missing or null
one, and ``RunInputs`` then keyed the position by ``""``; an edited one was read as given.
Both are now refused naming the entry, by the reader and by the writer; every manifest the
writer produces opens, v0.4.0rc4's included (``tests/data/rc4_run_manifest.json``, written
by ``tests/data/make_rc4_manifest.py``). A version 1 manifest is read for reporting as
before.
"""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from morphobricks_preprocessing import (
    ManifestFormatError,
    RunManifest,
    prefill_mapping,
    read_manifest,
    run_experiment,
    write_manifest,
)
from morphobricks_preprocessing.image_names import position_id
from morphobricks_preprocessing.manifest_fields import recorded_position_id
from morphobricks_preprocessing.run_inputs import RunInputs, RunInputsError, _fov
from tiff_tree import write_data

RC4 = Path(__file__).parent / "data" / "rc4_run_manifest.json"
NAMES = ("a.tif", "B.TIFF", "img.01.ome.tif", "c d.tif", "x.ome.tiff")


@pytest.fixture
def run(tmp_path):
    root = tmp_path / "exp"
    pixels = np.arange(64, dtype=np.uint8).reshape(8, 8)
    for name in NAMES:
        write_data(root / "ctrl/rep1" / name, pixels, "YX")
    result = run_experiment(root, prefill_mapping("YX"))
    return Path(result.output_folder), Path(result.manifest_path)


def edited(path, **changes):
    data = json.loads(path.read_text(encoding="utf-8"))
    for key, value in changes.items():
        if value is KeyError:
            del data["files"][0][key]
        else:
            data["files"][0][key] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    return data["files"][0]["relative_path"]


def test_every_written_entry_opens_with_the_position_of_its_name(run):
    output, path = run
    manifest = read_manifest(path)
    assert sorted(f.position_id for f in manifest.files) == sorted(["a", "B", "img.01", "c d", "x"])
    inputs = RunInputs.open(output)
    assert {fov.key[-1] for fov in inputs.fovs()} == {"a", "B", "img.01", "c d", "x"}


def test_the_rc4_manifest_still_opens():
    data = json.loads(RC4.read_text(encoding="utf-8"))
    assert data["manifest_version"] == "2"
    assert data["versions"]["morphobricks-preprocessing"] == "0.4.0rc4"
    manifest = RunManifest.from_dict(data)
    assert {f.relative_path: f.position_id for f in manifest.files} == {
        "ctrl/rep1/B.TIFF": "B",
        "ctrl/rep1/a.tif": "a",
        "ctrl/rep1/c d.tif": "c d",
        "ctrl/rep1/img.01.ome.tif": "img.01",
        "treated/rep1/a.tif": "a",
        "treated/rep1/z.ome.zarr": "z",
    }


@pytest.mark.parametrize("value", [KeyError, None])
def test_a_missing_or_null_position_id_is_refused(run, value):
    output, path = run
    relative = edited(path, position_id=value)
    with pytest.raises(ManifestFormatError, match="has no position_id") as raised:
        read_manifest(path)
    assert repr(relative) in str(raised.value)
    assert repr(position_id(relative)) in str(raised.value)
    with pytest.raises(ManifestFormatError, match="has no position_id"):
        RunInputs.open(output)  # before the fix: the position's key ended in ""


@pytest.mark.parametrize(
    "value", ["", "other", str.swapcase, str.lower, " {}", "{} ", "{}.TIFF", "{}.ome", 5, ["B"]]
)
def test_a_position_id_that_is_not_the_names_is_refused(run, value):
    output, path = run
    name = position_id(json.loads(path.read_text(encoding="utf-8"))["files"][0]["relative_path"])
    assert name == "B"  # the first entry, ctrl/rep1/B.TIFF: its case is part of the identifier
    if callable(value):
        value = value(name)
    elif isinstance(value, str) and "{}" in value:
        value = value.format(name)
    relative = edited(path, position_id=value)
    expected = position_id(relative)  # one differing only in case is still another identifier
    with pytest.raises(ManifestFormatError, match="position_id") as raised:
        read_manifest(path)
    assert f"gives {expected!r}" in str(raised.value) or "has no position_id" in str(raised.value)
    with pytest.raises(ManifestFormatError):
        RunInputs.open(output)


def test_the_writer_refuses_what_the_reader_refuses(run, tmp_path):
    _, path = run
    manifest = read_manifest(path)
    for value in (None, "other"):
        files = (replace(manifest.files[0], position_id=value), *manifest.files[1:])
        with pytest.raises(ValueError, match="position_id"):
            write_manifest(replace(manifest, files=files), tmp_path / "m.json")
    assert not (tmp_path / "m.json").exists()


def test_a_version_1_manifest_is_still_read_without_or_with_any_position(run):
    _, path = run
    data = json.loads(path.read_text(encoding="utf-8"))
    data["manifest_version"] = "1"
    del data["files"][0]["position_id"]
    data["files"][1]["position_id"] = "edited"
    files = RunManifest.from_dict(data).files
    assert files[0].position_id is None and files[1].position_id == "edited"


def test_a_name_that_is_only_an_extension_is_refused():
    with pytest.raises(ValueError, match="only an extension"):
        recorded_position_id(".tif", "ctrl/.tif")
    assert recorded_position_id("img.01", "ctrl/img.01.ome.tif") == "img.01"
    assert recorded_position_id(None, "ctrl/a.tif", strict=False) is None


def test_a_run_after_a_manifest_without_positions_reads_every_image_again(run):
    # A v2 manifest without them is not trusted: no row of it is reused.
    output, path = run
    table = (output / "plane_histograms.csv").read_bytes()
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data["files"]:
        del entry["position_id"]
    path.write_text(json.dumps(data), encoding="utf-8")
    result = run_experiment(output.parent / "exp", prefill_mapping("YX"), output_folder=output)
    assert result.written and "manifest could not be read" in result.rebuild_reason
    assert result.unchanged == () and (output / "plane_histograms.csv").read_bytes() == table


def test_run_inputs_never_keys_a_position_by_an_empty_name(run):
    # Unreachable through the reader, which refuses such an entry first; kept as a guard.
    _, path = run
    manifest = read_manifest(path)
    with pytest.raises(RunInputsError, match="records no position_id"):
        _fov(replace(manifest.files[0], position_id=None), [], manifest)
