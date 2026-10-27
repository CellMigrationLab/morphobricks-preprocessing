import hashlib
import json
import os

import pytest

from morphobricks_preprocessing import (
    FileEntry,
    ManifestFormatError,
    RunManifest,
    RunSettings,
    prefill_mapping,
    read_manifest,
    write_manifest,
)
from morphobricks_preprocessing.manifest import file_entry_for, file_sha256, package_versions
from tiff_tree import write_tiff


def entry(path="cond/rep/a.tif", **kwargs):
    values = {
        "relative_path": path,
        "size": 100,
        "mtime_ns": 1_700_000_000_123_456_789,
        "labels": ("cond", "rep", "single"),
        "axes": "ZYX",
        "plane_count": 3,
    }
    return FileEntry(**{**values, **kwargs})


def manifest(**kwargs):
    values = {
        "experiment_root": "/data/exp",
        "dtype": "uint16",
        "settings": RunSettings(65_536, 2**30, False, ("b.tif", "a.tif", "a.tif")),
        "mapping": prefill_mapping("ZYX"),
        "files": (
            entry(),
            entry("cond/rep/b.tif", channel_names=("DAPI", None), significant_bits=12),
            entry("other/c.tif", labels=("other", "single", "single"), singleton_roles=("C",)),
        ),
        "csv_sha256": "0" * 64,
        "versions": {"numpy": "2.0"},
        "written_at": "2026-10-17T12:00:00+00:00",
    }
    return RunManifest(**{**values, **kwargs})


def test_settings_sort_and_deduplicate_exclusions():
    assert RunSettings(10, 20, exclude=("b", "a", "b")).exclude == ("a", "b")


def test_round_trip(tmp_path):
    original = manifest()
    path = write_manifest(original, tmp_path / "run_manifest.json")
    assert read_manifest(path) == original


def test_written_json_is_readable_and_stable(tmp_path):
    path = write_manifest(manifest(), tmp_path / "m.json")
    raw = path.read_bytes()
    assert b"\r\n" not in raw and raw.endswith(b"\n")
    data = json.loads(raw)
    assert data["manifest_version"] == "1" and data["schema_version"] == "3"
    assert data["axis_mapping"] == {"axes": "ZYX", "roles": ["Z", "Y", "X"]}
    assert data["settings"]["exclude"] == ["a.tif", "b.tif"]
    assert data["labels"] == [
        {
            "folder": "cond/rep",
            "condition": "cond",
            "biological_repeat": "rep",
            "technical_repeat": "single",
        },
        {
            "folder": "other",
            "condition": "other",
            "biological_repeat": "single",
            "technical_repeat": "single",
        },
    ]
    assert data["files"][1]["channel_names"] == ["DAPI", None]
    assert data["files"][1]["significant_bits"] == 12
    # Nanosecond times survive JSON exactly.
    assert data["files"][0]["mtime_ns"] == 1_700_000_000_123_456_789
    assert not list(tmp_path.glob("*.tmp"))


def test_leaves_and_file_lookup():
    m = manifest()
    assert m.leaves == {
        "cond/rep": ("cond", "rep", "single"),
        "other": ("other",) + ("single",) * 2,
    }
    assert m.file("other/c.tif").singleton_roles == ("C",)
    assert m.file("missing.tif") is None


def test_root_level_files_have_root_as_folder():
    m = manifest(files=(entry("a.tif", labels=("single",) * 3),))
    assert m.leaves == {".": ("single", "single", "single")}


@pytest.mark.parametrize(
    "change",
    [
        {"size": 101},
        {"mtime_ns": 1},
        {"axes": "CYX"},
        {"singleton_roles": ("C",)},
        {"labels": ("x", "rep", "single")},
    ],
)
def test_same_source_detects_changes(change):
    assert entry().same_source(entry())
    assert not entry().same_source(entry(**change))


def test_same_source_ignores_informational_fields():
    assert entry().same_source(entry(plane_count=9, channel_names=("A",), significant_bits=12))
    assert entry().same_source(entry(read_in_blocks=True))


def test_read_in_blocks_round_trips_and_defaults_to_false(tmp_path):
    original = manifest(files=(entry(read_in_blocks=True), entry("cond/rep/b.tif")))
    path = write_manifest(original, tmp_path / "m.json")
    assert read_manifest(path) == original
    data = json.loads(path.read_bytes())
    assert [f["read_in_blocks"] for f in data["files"]] == [True, False]
    del data["files"][0]["read_in_blocks"]  # written before planes could be read in blocks
    path.write_text(json.dumps(data), encoding="utf-8")
    assert not read_manifest(path).files[0].read_in_blocks


def test_position_id_round_trips_and_is_none_when_absent(tmp_path):
    original = manifest(files=(entry(position_id="a"), entry("cond/rep/b.tif", position_id="b")))
    path = write_manifest(original, tmp_path / "m.json")
    assert read_manifest(path) == original
    data = json.loads(path.read_bytes())
    assert [f["position_id"] for f in data["files"]] == ["a", "b"]
    del data["files"][0]["position_id"]  # written before position identifiers were recorded
    path.write_text(json.dumps(data), encoding="utf-8")
    assert read_manifest(path).files[0].position_id is None


def test_image_type_round_trips_and_is_none_when_absent(tmp_path):
    original = manifest(image_type="rgb", channels_from_rgb_samples=True)
    path = write_manifest(original, tmp_path / "m.json")
    assert read_manifest(path) == original
    data = json.loads(path.read_bytes())
    assert (data["image_type"], data["channels_from_rgb_samples"]) == ("rgb", True)
    assert data["manifest_version"] == "1"
    del data["image_type"], data["channels_from_rgb_samples"]  # written before P4
    path.write_text(json.dumps(data), encoding="utf-8")
    read = read_manifest(path)
    assert read.image_type is None and read.channels_from_rgb_samples is None


def test_file_entry_for_reads_size_and_time(tmp_path):
    path = write_tiff(tmp_path / "a.tif")
    found = file_entry_for(tmp_path, "a.tif", ("single",) * 3, prefill_mapping("YX"))
    stat = os.stat(path)
    assert (found.size, found.mtime_ns, found.axes) == (stat.st_size, stat.st_mtime_ns, "YX")
    assert found.position_id == "a"


def test_file_sha256(tmp_path):
    path = tmp_path / "x.bin"
    path.write_bytes(b"abc" * 1_000_000)
    assert file_sha256(path) == hashlib.sha256(b"abc" * 1_000_000).hexdigest()


def test_package_versions():
    versions = package_versions()
    assert set(versions) == {
        "morphobricks-preprocessing",
        "morphobricks-normalization",
        "numpy",
        "tifffile",
        "python",
    }
    assert all(versions.values())


@pytest.mark.parametrize(
    "content",
    ["", "{not json", "[1, 2]", '{"manifest_version": "1"}', '{"manifest_version": "99"}'],
    ids=["empty", "invalid", "list", "incomplete", "future-version"],
)
def test_malformed_manifests_are_refused(tmp_path, content):
    path = tmp_path / "m.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ManifestFormatError):
        read_manifest(path)


def test_bad_role_in_manifest_is_refused():
    data = manifest().to_dict()
    data["axis_mapping"]["roles"] = ["W", "Y", "X"]
    with pytest.raises(ManifestFormatError):
        RunManifest.from_dict(data)
