import json
import os
import shutil
from pathlib import Path

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    AxisMapping,
    PhysicalSizes,
    PlaneReadError,
    check_metadata,
    default_output_folder,
    inspect_ome_zarr,
    ome_zarr_relative_path,
    plane_layout,
    prefill_mapping,
    read_experiment_design,
    read_manifest,
    read_physical_sizes,
    read_plane_histograms,
    run_experiment,
    write_experiment_design,
)
from morphobricks_preprocessing import planes as planes_module
from morphobricks_preprocessing import run as run_module
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.zarr_export import (
    PYRAMID_TARGET,
    ConversionSettings,
    downsample,
    estimate_conversion,
    free_space,
    is_copy_path,
    pyramid_shapes,
)
from tiff_tree import write_data, write_file

ZYX = prefill_mapping("ZYX")

needs_zarr = pytest.mark.skipif(
    not run_module.zarr_available(), reason="the zarr extra is not installed"
)


def random_data(shape, dtype="uint16", seed=0):
    dt = np.dtype(dtype)
    rng = np.random.default_rng(seed)
    if dt.kind == "f":
        return rng.normal(100, 30, shape).astype(dt)
    info = np.iinfo(dt)
    return rng.integers(info.min, info.max, shape, dtype=dt, endpoint=True)


def open_copy(output, rel):
    import zarr

    return zarr.open_group(str(output / ome_zarr_relative_path(rel)), mode="r")


def multiscale(group):
    return dict(group.attrs)["multiscales"][0]


# Pure helpers (no zarr needed) --------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "copy"),
    [
        ("ctrl/rep1/img_01.tif", "ome_zarr/ctrl/rep1/img_01.ome.zarr"),
        ("a.TIFF", "ome_zarr/a.ome.zarr"),
        ("x/b.ome.tif", "ome_zarr/x/b.ome.zarr"),
        ("x/c.OME.TIFF", "ome_zarr/x/c.ome.zarr"),
    ],
)
def test_copy_location_mirrors_the_experiment(source, copy):
    assert ome_zarr_relative_path(source) == copy


def test_pyramid_shapes():
    assert pyramid_shapes((100, 80)) == [(100, 80)]
    assert pyramid_shapes((1100, 700)) == [(1100, 700), (550, 350), (275, 175)]
    assert max(pyramid_shapes((3000, 10))[-1]) <= PYRAMID_TARGET
    assert len(pyramid_shapes((10**7, 10**7))) == 8


def test_pyramid_shapes_with_a_number_of_levels():
    assert pyramid_shapes((1100, 700), 0) == [(1100, 700)]
    assert pyramid_shapes((100, 80), 2) == [(100, 80), (50, 40), (25, 20)]
    assert pyramid_shapes((2, 2), 5) == [(2, 2), (1, 1)]  # stops at a single pixel


# Conversion settings --------------------------------------------------------------------


def test_conversion_settings_defaults_and_round_trip():
    default = ConversionSettings()
    assert (default.chunk_size, default.compression, default.compression_level) == (1024, "lz4", 5)
    assert default.pyramid_levels is None
    custom = ConversionSettings(256, "zstd", 9, 3)
    assert ConversionSettings.from_dict(custom.to_dict()) == custom
    assert ConversionSettings.from_dict(None) == default
    assert ConversionSettings.from_dict({"compression": "none"}).compression == "none"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"chunk_size": 8},
        {"chunk_size": 9000},
        {"compression": "gzip"},
        {"compression_level": 0},
        {"compression_level": 10},
        {"pyramid_levels": -1},
        {"pyramid_levels": 8},
    ],
)
def test_conversion_settings_reject_bad_values(kwargs):
    with pytest.raises(ValueError):
        ConversionSettings(**kwargs)


def test_conversion_settings_describe():
    assert ConversionSettings().describe() == (
        "chunks of 1024 x 1024 px, Blosc LZ4 level 5, smaller levels until a plane fits 512 px"
    )
    assert ConversionSettings(256, "none", 5, 0).describe() == (
        "chunks of 256 x 256 px, no compression, no smaller levels"
    )


def test_estimate_conversion(tmp_path):
    from morphobricks_preprocessing import TiffMetadata

    path = write_data(tmp_path / "a.tif", random_data((2, 1024, 600), "uint16"), axes="ZYX")
    files = [
        TiffMetadata("a.tif", True, (2, 1024, 600), np.dtype("uint16"), "ZYX", 1, 2),
        TiffMetadata(
            "b.ome.zarr", True, (2, 8, 8), np.dtype("uint16"), "ZYX", 1, 0, kind="ome-zarr"
        ),
        TiffMetadata("bad.tif", False),
    ]
    level0 = 2 * 1024 * 600 * 2
    flat = estimate_conversion(tmp_path, files, ConversionSettings(pyramid_levels=0))
    assert (flat.image_count, flat.copy_bytes) == (1, level0)
    assert flat.source_bytes == path.stat().st_size
    pyramid = estimate_conversion(tmp_path, files)  # 1024 x 600 -> 512 x 300
    assert pyramid.copy_bytes == level0 + 2 * 512 * 300 * 2


def test_free_space_uses_the_nearest_existing_folder(tmp_path, monkeypatch):
    # Compare the folder asked about, not two readings of the free space: other processes
    # can write to the drive in between (seen on CI runners).
    real = shutil.disk_usage
    asked = []

    def spy(path):
        asked.append(Path(path))
        return real(path)

    monkeypatch.setattr(shutil, "disk_usage", spy)
    assert free_space(tmp_path / "not" / "yet" / "there") > 0
    assert asked == [tmp_path.resolve()]


def test_downsample_integers_round_and_keep_type():
    pixels = np.array([[0, 1, 255], [1, 1, 255], [7, 7, 9]], dtype=np.uint8)
    small = downsample(pixels)
    assert small.dtype == np.uint8
    # Blocks: [[0,1],[1,1]] -> 0.75 -> 1; [[255],[255]] padded -> 255; [7,7] row padded -> 7.
    np.testing.assert_array_equal(small, [[1, 255], [7, 9]])


def test_downsample_floats_propagate_nan():
    pixels = np.array([[1.0, 3.0], [np.nan, 0.0]], dtype=np.float32)
    assert np.isnan(downsample(pixels)[0, 0])
    np.testing.assert_allclose(downsample(np.full((4, 4), 2.5)), np.full((2, 2), 2.5))


def test_physical_sizes_from_ome(tmp_path):
    path = tmp_path / "a.ome.tif"
    tifffile.imwrite(
        path,
        np.zeros((2, 3, 8, 8), np.uint16),
        ome=True,
        photometric="minisblack",
        metadata={
            "axes": "TZYX",
            "PhysicalSizeX": 120.0,
            "PhysicalSizeXUnit": "nm",
            "PhysicalSizeY": 120.0,
            "PhysicalSizeYUnit": "nm",
            "PhysicalSizeZ": 500.0,
            "TimeIncrement": 30.0,
            "TimeIncrementUnit": "s",
        },
    )
    # PhysicalSizeZUnit is left out, so Z is in the OME default, micrometres.
    assert read_physical_sizes(path) == PhysicalSizes(
        x=120.0,
        y=120.0,
        z=500.0,
        t=30.0,
        space_unit="nanometer",
        time_unit="second",
        x_unit="nanometer",
        y_unit="nanometer",
        z_unit="micrometer",
    )


def test_physical_sizes_from_imagej(tmp_path):
    path = tmp_path / "a.tif"
    tifffile.imwrite(
        path,
        np.zeros((3, 8, 8), np.uint16),
        imagej=True,
        resolution=(1 / 0.25, 1 / 0.25),
        metadata={"axes": "ZYX", "unit": "um", "spacing": 1.5},
    )
    sizes = read_physical_sizes(path)
    assert (sizes.x, sizes.y, sizes.z, sizes.space_unit) == (0.25, 0.25, 1.5, "micrometer")


def test_physical_sizes_unknown(tmp_path):
    plain = write_data(tmp_path / "a.tif", np.zeros((8, 8), np.uint8))
    assert read_physical_sizes(plain) == PhysicalSizes()
    no_unit = tmp_path / "b.tif"
    tifffile.imwrite(no_unit, np.zeros((8, 8), np.uint8), imagej=True, resolution=(2.0, 2.0))
    assert read_physical_sizes(no_unit).x is None
    assert read_physical_sizes(write_file(tmp_path / "bad.tif", b"garbage")) == PhysicalSizes()
    assert PhysicalSizes().scale("X") == 1.0 and PhysicalSizes().unit("X") is None


def test_only_copies_inside_the_ome_zarr_folder_count(tmp_path):
    assert is_copy_path(tmp_path, "ome_zarr/a/x.ome.zarr")
    assert not is_copy_path(tmp_path, "../keep.ome.zarr")
    assert not is_copy_path(tmp_path, "ome_zarr/../../x.ome.zarr")
    assert not is_copy_path(tmp_path, "ome_zarr/a/x.zarr")
    assert not is_copy_path(tmp_path, "plane_histograms.csv")


def test_missing_zarr_is_a_clear_error(tmp_path, monkeypatch):
    write_data(tmp_path / "exp/a.tif", random_data((2, 8, 8)), axes="ZYX")
    monkeypatch.setattr(run_module, "zarr_available", lambda: False)
    result = run_experiment(tmp_path / "exp", ZYX, convert_tiffs=True)
    (found,) = [i for i in result.issues if i.code == "zarr_not_installed"]
    assert "[zarr]" in found.action and not result.written
    assert run_experiment(tmp_path / "exp", ZYX).written


# Writing copies -----------------------------------------------------------------------


LAYOUTS = [
    pytest.param((6, 7), "YX", {}, None, ("Y", "X"), id="2d"),
    pytest.param((3, 6, 7), "ZYX", {}, None, ("Z", "Y", "X"), id="zstack"),
    pytest.param((3, 2, 6, 7), "ZCYX", {}, None, ("C", "Z", "Y", "X"), id="zcyx"),
    pytest.param(
        (2, 3, 2, 6, 7), "TZCYX", {"imagej": True}, None, ("T", "C", "Z", "Y", "X"), id="imagej"
    ),
    pytest.param(
        (2, 6, 7, 3), "ZYXS", {"photometric": "rgb"}, None, ("C", "Z", "Y", "X"), id="rgb"
    ),
    pytest.param((3, 6, 7), "ZXY", {}, None, ("Z", "Y", "X"), id="x-before-y"),
    pytest.param((1, 3, 6, 7), "QZYX", {}, None, ("Z", "Y", "X"), id="unassigned-singleton"),
    pytest.param((4, 6, 7), None, {}, "TYX", ("T", "Y", "X"), id="pages-as-time"),
]


@needs_zarr
@pytest.mark.parametrize(("shape", "axes", "kwargs", "roles", "ngff"), LAYOUTS)
def test_copy_holds_the_source_values(tmp_path, shape, axes, kwargs, roles, ngff):
    data = random_data(shape, "uint8" if "photometric" in kwargs else "uint16")
    write_data(tmp_path / "exp/a.tif", data, axes=axes, **kwargs)
    info = run_module.preflight(tmp_path / "exp").metadata.files[0]
    mapping = AxisMapping(info.axes, roles) if roles else prefill_mapping(info.axes)
    result = run_experiment(tmp_path / "exp", mapping, convert_tiffs=True)
    assert result.written and result.converted == ("a.tif",)

    group = open_copy(result.output_folder, "a.tif")
    meta = multiscale(group)
    assert [a["name"] for a in meta["axes"]] == [r.lower() for r in ngff]
    assert meta["version"] == "0.4"
    # Reorder the source to OME-NGFF order with NumPy and compare with level 0.
    present = [r for r in mapping.roles if r is not None]
    keep = tuple(slice(None) if r is not None else 0 for r in mapping.roles)
    expected = np.moveaxis(data[keep], [present.index(r) for r in ngff], range(len(ngff)))
    np.testing.assert_array_equal(group["0"][:], expected)
    assert group["0"].dtype == data.dtype


@needs_zarr
@pytest.mark.parametrize(
    "dtype",
    [
        "int8",
        "int16",
        "int32",
        "int64",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "float32",
        "float64",
    ],
)
def test_every_dtype_is_copied_unchanged(tmp_path, dtype):
    data = random_data((2, 9, 11), dtype)
    if data.dtype.kind == "f":
        data[0, 0, :2] = [np.nan, np.inf]
    write_data(tmp_path / "exp/a.tif", data, axes="ZYX")
    result = run_experiment(tmp_path / "exp", ZYX, convert_tiffs=True)
    np.testing.assert_array_equal(open_copy(result.output_folder, "a.tif")["0"][:], data)


@needs_zarr
def test_copies_give_each_axis_its_own_unit(tmp_path):
    path = tmp_path / "exp/a.ome.tif"
    path.parent.mkdir(parents=True)
    tifffile.imwrite(
        path,
        random_data((2, 8, 8), "uint16"),
        ome=True,
        photometric="minisblack",
        metadata={
            "axes": "ZYX",
            "PhysicalSizeX": 120.0,
            "PhysicalSizeXUnit": "nm",
            "PhysicalSizeY": 0.25,
            "PhysicalSizeYUnit": "µm",
            "PhysicalSizeZ": 2.0,
            "PhysicalSizeZUnit": "mm",
        },
    )
    result = run_experiment(tmp_path / "exp", ZYX, convert_tiffs=True)
    meta = multiscale(open_copy(result.output_folder, "a.ome.tif"))
    assert [axis["unit"] for axis in meta["axes"]] == ["millimeter", "micrometer", "nanometer"]
    assert meta["datasets"][0]["coordinateTransformations"][0]["scale"] == [2.0, 0.25, 120.0]


@needs_zarr
def test_pyramid_levels_and_scales(tmp_path):
    data = random_data((2, 1100, 700), "uint16")
    path = tmp_path / "exp/a.ome.tif"
    path.parent.mkdir(parents=True)
    tifffile.imwrite(
        path,
        data,
        ome=True,
        photometric="minisblack",
        metadata={"axes": "ZYX", "PhysicalSizeX": 0.5, "PhysicalSizeY": 0.5, "PhysicalSizeZ": 2},
    )
    result = run_experiment(tmp_path / "exp", ZYX, convert_tiffs=True)
    group = open_copy(result.output_folder, "a.ome.tif")
    meta = multiscale(group)
    assert [d["path"] for d in meta["datasets"]] == ["0", "1", "2"]
    scales = [d["coordinateTransformations"][0]["scale"] for d in meta["datasets"]]
    assert scales == [[2.0, 0.5, 0.5], [2.0, 1.0, 1.0], [2.0, 2.0, 2.0]]
    assert meta["axes"][2] == {"name": "x", "type": "space", "unit": "micrometer"}
    assert group["1"].shape == (2, 550, 350) and group["2"].shape == (2, 275, 175)
    np.testing.assert_array_equal(group["1"][1], downsample(data[1]))
    assert group["0"].chunks == (1, 1024, 700)


@needs_zarr
def test_channel_names_and_display_ranges(tmp_path):
    data = random_data((2, 8, 8), "uint16")
    path = tmp_path / "exp/a.ome.tif"
    path.parent.mkdir(parents=True)
    tifffile.imwrite(
        path,
        data,
        ome=True,
        photometric="minisblack",
        metadata={"axes": "CYX", "Channel": {"Name": ["DAPI", "GFP"]}},
    )
    result = run_experiment(tmp_path / "exp", prefill_mapping("CYX"), convert_tiffs=True)
    omero = dict(open_copy(result.output_folder, "a.ome.tif").attrs)["omero"]
    assert [c["label"] for c in omero["channels"]] == ["DAPI", "GFP"]
    window = omero["channels"][1]["window"]
    assert (window["min"], window["max"]) == (float(data[1].min()), float(data[1].max()))


@needs_zarr
def test_copies_of_unnamed_channels_read_back_unnamed(tmp_path):
    # Issue #37 review: channel names are compared exactly, so a copy must not name a
    # channel its source leaves unnamed (it used to write the placeholder "Channel <i>").
    root = tmp_path / "exp"
    root.mkdir()
    tifffile.imwrite(
        root / "a.ome.tif",
        random_data((2, 8, 8)),
        ome=True,
        photometric="minisblack",
        metadata={"axes": "CYX", "Channel": {"Name": ["DAPI", "GFP"]}},
    )
    write_data(root / "b.tif", random_data((2, 8, 8), seed=1), axes="CYX")
    result = run_experiment(root, prefill_mapping("CYX"), convert_tiffs=True)
    assert result.written
    omero = dict(open_copy(result.output_folder, "b.tif").attrs)["omero"]
    assert all("label" not in channel for channel in omero["channels"])
    copies = result.output_folder / "ome_zarr"
    headers = [inspect_ome_zarr(copies / name, name) for name in ("a.ome.zarr", "b.ome.zarr")]
    assert [h.channel_names for h in headers] == [("DAPI", "GFP"), ()]
    assert "channel_names_differ" not in {i.code for i in check_metadata(headers)}


@needs_zarr
def test_copies_with_placeholder_channel_names_are_written_again(tmp_path):
    root = tmp_path / "exp"
    root.mkdir()
    write_data(root / "a.tif", random_data((2, 8, 8)), axes="CYX")
    first = run_experiment(root, prefill_mapping("CYX"), convert_tiffs=True)
    table = first.csv_path.read_bytes()
    # Make the copy look like one written with placeholder names.
    attrs_path = first.output_folder / ome_zarr_relative_path("a.tif") / ".zattrs"
    attrs = json.loads(attrs_path.read_text(encoding="utf-8"))
    for c, channel in enumerate(attrs["omero"]["channels"]):
        channel["label"] = f"Channel {c}"
    attrs_path.write_text(json.dumps(attrs), encoding="utf-8")
    second = run_experiment(root, prefill_mapping("CYX"), convert_tiffs=True)
    assert second.converted == ("a.tif",)
    assert second.csv_path.read_bytes() == table  # same pixels, same rows
    omero = dict(open_copy(second.output_folder, "a.tif").attrs)["omero"]
    assert all("label" not in channel for channel in omero["channels"])
    assert run_experiment(root, prefill_mapping("CYX"), convert_tiffs=True).converted == ()


@needs_zarr
def test_missing_axis_becomes_length_one(tmp_path):
    write_data(tmp_path / "exp/a.tif", random_data((2, 3, 8, 8)), axes="ZTYX")
    data = random_data((2, 8, 8), seed=1)
    write_data(tmp_path / "exp/b.tif", data, axes="ZYX")
    result = run_experiment(
        tmp_path / "exp", prefill_mapping("ZTYX"), convert_tiffs=True, missing_as_singleton=True
    )
    group = open_copy(result.output_folder, "b.tif")
    assert group["0"].shape == (1, 2, 8, 8)
    np.testing.assert_array_equal(group["0"][0], data)


# Runs and reruns ----------------------------------------------------------------------


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    for i, rel in enumerate(["ctrl/a.tif", "ctrl/b.tif", "drug/a.tif"]):
        write_data(root / rel, random_data((2, 16, 16), "uint8", seed=i), axes="ZYX")
    return root


def copy_mtimes(output):
    base = output / "ome_zarr"
    return {
        p.relative_to(base).as_posix(): p.stat().st_mtime_ns for p in base.rglob("*") if p.is_file()
    }


@needs_zarr
def test_manifest_records_the_copies(experiment):
    result = run_experiment(experiment, ZYX, convert_tiffs=True)
    manifest = read_manifest(result.manifest_path)
    assert manifest.settings.convert_tiffs
    assert [f.ome_zarr_path for f in manifest.files] == [
        "ome_zarr/ctrl/a.ome.zarr",
        "ome_zarr/ctrl/b.ome.zarr",
        "ome_zarr/drug/a.ome.zarr",
    ]
    assert not [p for p in result.output_folder.iterdir() if p.name.startswith(".staging")]


@needs_zarr
def test_rerun_keeps_unchanged_copies(experiment):
    first = run_experiment(experiment, ZYX, convert_tiffs=True)
    before = copy_mtimes(first.output_folder)
    second = run_experiment(experiment, ZYX, convert_tiffs=True)
    assert second.read == second.converted == ()
    assert copy_mtimes(second.output_folder) == before


@needs_zarr
def test_changed_image_is_copied_again(experiment):
    first = run_experiment(experiment, ZYX, convert_tiffs=True)
    path = experiment / "ctrl/b.tif"
    new_data = random_data((2, 16, 16), "uint8", seed=42)
    stat = os.stat(path)
    write_data(path, new_data, axes="ZYX")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    second = run_experiment(experiment, ZYX, convert_tiffs=True)
    assert second.converted == ("ctrl/b.tif",)
    np.testing.assert_array_equal(open_copy(first.output_folder, "ctrl/b.tif")["0"][:], new_data)


@needs_zarr
def test_removed_and_excluded_images_lose_their_copies(experiment):
    first = run_experiment(experiment, ZYX, convert_tiffs=True)
    (experiment / "ctrl/a.tif").unlink()
    run_experiment(experiment, ZYX, convert_tiffs=True, exclude=["drug/a.tif"])
    remaining = sorted(
        p.relative_to(first.output_folder).as_posix()
        for p in (first.output_folder / "ome_zarr").rglob("*.ome.zarr")
    )
    assert remaining == ["ome_zarr/ctrl/b.ome.zarr"]


@needs_zarr
def test_switching_to_conversion_recomputes_the_tiff_rows_from_copies(experiment):
    first = run_experiment(experiment, ZYX)
    table = first.csv_path.read_bytes()
    second = run_experiment(experiment, ZYX, convert_tiffs=True)
    assert second.rebuild_reason is None
    assert second.changed == ("ctrl/a.tif", "ctrl/b.tif", "drug/a.tif") == second.converted
    # The copies hold the source values, so the rows are byte-identical.
    assert second.csv_path.read_bytes() == table
    # A copy deleted by hand is converted again, with its rows.
    import shutil

    shutil.rmtree(second.output_folder / "ome_zarr/drug/a.ome.zarr")
    third = run_experiment(experiment, ZYX, convert_tiffs=True)
    assert third.changed == ("drug/a.tif",) == third.converted


@needs_zarr
def test_histograms_are_computed_from_the_converted_copies(experiment, monkeypatch):
    read_paths = []

    def spy(name):
        original = getattr(run_module, name)

        def reading(path, *args, **kwargs):
            read_paths.append(Path(path))
            return original(path, *args, **kwargs)

        monkeypatch.setattr(run_module, name, reading)

    spy("iter_planes")  # conversion
    spy("iter_xyc_planes")  # histograms
    run_experiment(experiment, ZYX, convert_tiffs=True)
    copies = [p for p in read_paths if p.name.endswith(".ome.zarr")]
    sources = [p for p in read_paths if p.suffix == ".tif"]
    # Each TIFF is read once to convert it, and its copy once for the histograms.
    assert len(sources) == len(copies) == 3
    assert all(".run-" in str(p) for p in copies)


@needs_zarr
def test_switching_back_to_reading_tiffs_removes_the_copies(experiment):
    run_experiment(experiment, ZYX, convert_tiffs=True)
    result = run_experiment(experiment, ZYX)
    assert result.changed == ("ctrl/a.tif", "ctrl/b.tif", "drug/a.tif")
    assert result.converted == ()
    assert all(f.ome_zarr_path is None for f in read_manifest(result.manifest_path).files)
    assert not list((result.output_folder / "ome_zarr").rglob("*.ome.zarr"))


@needs_zarr
def test_failed_run_writes_no_copies(experiment, monkeypatch):
    original = planes_module._read_page

    def broken(series, index, layout, path):
        if str(path).endswith("b.tif"):
            raise PlaneReadError("cannot be decoded")
        return original(series, index, layout, path)

    monkeypatch.setattr(planes_module, "_read_page", broken)
    result = run_experiment(experiment, ZYX, convert_tiffs=True)
    assert not result.written
    assert sorted(p.name for p in result.output_folder.iterdir()) == [
        "discovery_report.json",
        "discovery_report.txt",
    ]


@needs_zarr
def test_failed_rerun_keeps_earlier_copies(experiment, monkeypatch):
    first = run_experiment(experiment, ZYX, convert_tiffs=True)
    before = copy_mtimes(first.output_folder)
    write_data(experiment / "ctrl/c.tif", random_data((2, 16, 16), "uint8", seed=7), axes="ZYX")
    original = planes_module._read_page

    def broken(series, index, layout, path):
        if str(path).endswith("c.tif"):
            raise PlaneReadError("cannot be decoded")
        return original(series, index, layout, path)

    monkeypatch.setattr(planes_module, "_read_page", broken)
    assert not run_experiment(experiment, ZYX, convert_tiffs=True).written
    assert copy_mtimes(first.output_folder) == before
    assert sorted(p.name for p in first.output_folder.iterdir()) == [
        "discovery_report.json",
        "discovery_report.txt",
        "ome_zarr",
        "plane_histograms.csv",
        "run_manifest.json",
    ]


@needs_zarr
def test_table_is_the_same_with_or_without_copies(experiment, tmp_path):
    with_copies = run_experiment(experiment, ZYX, convert_tiffs=True, output_folder=tmp_path / "a")
    without = run_experiment(experiment, ZYX, output_folder=tmp_path / "b")
    a = read_plane_histograms(with_copies.csv_path)
    b = read_plane_histograms(without.csv_path)
    assert [r.plane_id for r in a] == [r.plane_id for r in b]
    assert all(
        np.array_equal(x.channel_histogram(0).counts, y.channel_histogram(0).counts)
        for x, y in zip(a, b, strict=True)
    )


@needs_zarr
def test_cli_option(experiment, capsys):
    # The first run asks for the experiment design; confirm it as a user would.
    assert main(["run", str(experiment), "--axes", "ZYX", "--convert-tiff"]) == 1
    path = default_output_folder(experiment) / "experiment_design.csv"
    design = read_experiment_design(path)
    for group in design.groups:
        design = design.with_confirmed(group, True, "2026-10-26")
    write_experiment_design(design, path)
    assert main(["run", str(experiment), "--axes", "ZYX", "--convert-tiff"]) == 0
    assert "TIFF images converted to OME-Zarr: 3 images" in capsys.readouterr().out


def test_old_manifest_without_ome_zarr_fields_loads(experiment):
    result = run_experiment(experiment, ZYX)
    data = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    del data["settings"]["convert_tiffs"]
    for entry in data["files"]:
        del entry["ome_zarr_path"]
    result.manifest_path.write_text(json.dumps(data), encoding="utf-8")
    manifest = read_manifest(result.manifest_path)
    assert not manifest.settings.convert_tiffs and manifest.files[0].ome_zarr_path is None
    assert run_experiment(experiment, ZYX).read == ()


@pytest.mark.parametrize("code", ["q", "Q", "l", "L", "i", "h", "b", "B", "f", "d"])
def test_canonical_dtype(code):
    from morphobricks_preprocessing.zarr_export import canonical_dtype

    dtype = np.dtype(code)
    canonical = canonical_dtype(dtype)
    assert canonical == dtype and canonical is np.dtype(dtype.name)


@needs_zarr
@pytest.mark.parametrize("code", ["q", "Q"])
def test_long_long_planes_are_written(tmp_path, code):
    # On Linux and macOS, tifffile can return 64-bit integers as numpy "long long",
    # a dtype class zarr 3 does not accept although it equals int64.
    from dataclasses import replace

    from morphobricks_preprocessing import Plane, plane_layout
    from morphobricks_preprocessing.zarr_export import OmeZarrWriter

    data = random_data((2, 6, 7), np.dtype(code).name).astype(np.dtype(code))
    path = write_data(tmp_path / "a.tif", data, axes="ZYX")
    layout = replace(plane_layout(path, ZYX), dtype=np.dtype(code))
    writer = OmeZarrWriter.create(tmp_path / "a.ome.zarr", ZYX, layout)
    for z in range(2):
        writer.write(Plane(z, None, None, np.ascontiguousarray(data[z])))
    writer.finish()
    import zarr

    np.testing.assert_array_equal(zarr.open_group(str(tmp_path / "a.ome.zarr"))["0"][:], data)


# Publication with copies (same protocol as the table and manifest) ----------------------


def output_state(output):
    """Every file under the output folder with its bytes, ignoring temporary folders."""
    return {
        p.relative_to(output).as_posix(): p.read_bytes()
        for p in output.rglob("*")
        if p.is_file() and not p.relative_to(output).parts[0].startswith(".run-")
    }


def change(path, seed):
    stat = os.stat(path)
    write_data(path, random_data((2, 16, 16), "uint8", seed=seed), axes="ZYX")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))


@needs_zarr
@pytest.mark.parametrize("failed", ["copy", "csv", "manifest"])
def test_failed_publication_restores_earlier_copies(experiment, monkeypatch, failed):
    first = run_experiment(experiment, ZYX, convert_tiffs=True)
    (experiment / "drug/a.tif").unlink()  # its copy is removed by the next run
    change(experiment / "ctrl/b.tif", 42)  # its copy is replaced by the next run
    before = output_state(first.output_folder)
    targets = {
        "copy": first.output_folder / "ome_zarr/ctrl/b.ome.zarr",
        "csv": first.csv_path,
        "manifest": first.manifest_path,
    }
    original_replace = os.replace

    def broken_replace(source, destination):
        staged_new = ".run-" in str(source) and "previous" not in Path(source).parts
        if Path(destination) == targets[failed] and staged_new:
            raise PermissionError("simulated publication failure")
        return original_replace(source, destination)

    monkeypatch.setattr(run_module.os, "replace", broken_replace)
    with pytest.raises(PermissionError, match="simulated"):
        run_experiment(experiment, ZYX, convert_tiffs=True)
    monkeypatch.undo()
    assert output_state(first.output_folder) == before
    assert not list(first.output_folder.glob(".run-*"))
    retried = run_experiment(experiment, ZYX, convert_tiffs=True)
    assert retried.converted == ("ctrl/b.tif",) and retried.removed == ("drug/a.tif",)
    assert not (first.output_folder / "ome_zarr/drug/a.ome.zarr").exists()


@needs_zarr
def test_failed_table_write_keeps_earlier_copies(experiment, monkeypatch):
    first = run_experiment(experiment, ZYX, convert_tiffs=True)
    change(experiment / "ctrl/b.tif", 42)
    before = output_state(first.output_folder)

    def broken_csv(*args, **kwargs):
        raise OSError("simulated CSV failure")

    monkeypatch.setattr(run_module, "write_plane_histograms", broken_csv)
    with pytest.raises(OSError, match="simulated"):
        run_experiment(experiment, ZYX, convert_tiffs=True)
    assert output_state(first.output_folder) == before
    assert not list(first.output_folder.glob(".run-*"))


@needs_zarr
def test_failed_rollback_keeps_the_earlier_copy_in_the_backup(experiment, monkeypatch):
    first = run_experiment(experiment, ZYX, convert_tiffs=True)
    change(experiment / "ctrl/b.tif", 42)
    old_copy = output_state(first.output_folder / "ome_zarr/ctrl/b.ome.zarr")
    original_replace = os.replace

    def broken_replace(source, destination):
        source, destination = Path(source), Path(destination)
        if destination == first.manifest_path or "previous" in source.parts:
            raise PermissionError("simulated locked output")
        return original_replace(source, destination)

    monkeypatch.setattr(run_module.os, "replace", broken_replace)
    with pytest.raises(RuntimeError, match="could not be fully restored"):
        run_experiment(experiment, ZYX, convert_tiffs=True)
    (staged,) = first.output_folder.glob(".run-*")
    backup = staged / "previous" / "ome_zarr" / "ctrl" / "b.ome.zarr"
    assert output_state(backup) == old_copy


# Conversion settings in a run ------------------------------------------------------------


@needs_zarr
@pytest.mark.parametrize(
    ("settings", "compressor"),
    [
        (ConversionSettings(), {"cname": "lz4", "clevel": 5}),
        (
            ConversionSettings(compression="zstd", compression_level=9),
            {"cname": "zstd", "clevel": 9},
        ),
        (ConversionSettings(compression="none"), None),
    ],
)
def test_copies_follow_the_conversion_settings(tmp_path, settings, compressor):
    data = random_data((2, 100, 80), "uint16")
    write_data(tmp_path / "exp/a.tif", data, axes="ZYX")
    custom = ConversionSettings(
        chunk_size=32,
        compression=settings.compression,
        compression_level=settings.compression_level,
        pyramid_levels=2,
    )
    result = run_experiment(tmp_path / "exp", ZYX, convert_tiffs=True, conversion=custom)
    group = open_copy(result.output_folder, "a.tif")
    assert sorted(group.array_keys()) == ["0", "1", "2"]
    assert group["0"].chunks == (1, 32, 32) and group["2"].shape == (2, 25, 20)
    np.testing.assert_array_equal(group["0"][:], data)
    zarray = json.loads(
        (result.output_folder / "ome_zarr/a.ome.zarr/0/.zarray").read_text(encoding="utf-8")
    )
    if compressor is None:
        assert zarray["compressor"] is None
    else:
        assert {k: zarray["compressor"][k] for k in compressor} == compressor
    assert read_manifest(result.manifest_path).settings.conversion == custom


@needs_zarr
def test_copies_with_outdated_axis_units_are_written_again(tmp_path):
    root = tmp_path / "exp"
    for name in ("a.ome.tif", "b.ome.tif"):
        (root / "ctrl").mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(
            root / "ctrl" / name,
            random_data((2, 8, 8), "uint8"),
            ome=True,
            photometric="minisblack",
            metadata={
                "axes": "ZYX",
                "PhysicalSizeX": 120.0,
                "PhysicalSizeXUnit": "nm",
                "PhysicalSizeY": 0.25,
                "PhysicalSizeYUnit": "µm",
                "PhysicalSizeZ": 2.0,
            },
        )
    first = run_experiment(root, ZYX, convert_tiffs=True)
    table = first.csv_path.read_bytes()
    # Make a.ome.tif's copy look like one written before per-axis units: X's unit
    # on every space axis.
    attrs_path = first.output_folder / ome_zarr_relative_path("ctrl/a.ome.tif") / ".zattrs"
    attrs = json.loads(attrs_path.read_text(encoding="utf-8"))
    for axis in attrs["multiscales"][0]["axes"]:
        axis["unit"] = "nanometer"
    attrs_path.write_text(json.dumps(attrs), encoding="utf-8")
    second = run_experiment(root, ZYX, convert_tiffs=True)
    assert second.converted == ("ctrl/a.ome.tif",)
    assert second.csv_path.read_bytes() == table  # same pixels, same rows
    units = [
        a["unit"] for a in multiscale(open_copy(second.output_folder, "ctrl/a.ome.tif"))["axes"]
    ]
    assert units == ["micrometer", "micrometer", "nanometer"]
    assert run_experiment(root, ZYX, convert_tiffs=True).converted == ()


@needs_zarr
def test_changed_conversion_settings_rewrite_the_copies(experiment):
    first = run_experiment(experiment, ZYX, convert_tiffs=True)
    table = first.csv_path.read_bytes()
    same = run_experiment(experiment, ZYX, convert_tiffs=True)
    assert same.converted == ()
    changed = run_experiment(
        experiment, ZYX, convert_tiffs=True, conversion=ConversionSettings(chunk_size=16)
    )
    assert changed.converted == ("ctrl/a.tif", "ctrl/b.tif", "drug/a.tif")
    assert changed.csv_path.read_bytes() == table  # same pixels, same rows
    assert open_copy(changed.output_folder, "ctrl/a.tif")["0"].chunks == (1, 16, 16)
    # Without conversion the settings are recorded but change nothing.
    plain = run_experiment(experiment, ZYX, conversion=ConversionSettings(chunk_size=64))
    assert plain.converted == ()


# Planes too large for the memory limit -------------------------------------------------


def levels(group):
    return {name: group[name][:] for name in sorted(group.array_keys())}


@needs_zarr
@pytest.mark.parametrize(
    ("dtype", "storage"),
    [
        ("uint16", {}),
        ("uint16", {"compression": "zlib", "rowsperstrip": 5}),
        ("int32", {"tile": (16, 32)}),
        ("float32", {"compression": "zlib", "rowsperstrip": 3}),
    ],
)
def test_copies_of_planes_in_blocks_equal_whole_copies(tmp_path, dtype, storage):
    data = random_data((2, 91, 70), dtype)  # odd sizes: the edges are repeated when halving
    write_data(tmp_path / "exp/a.tif", data, axes="ZYX", **storage)
    settings = ConversionSettings(chunk_size=16, pyramid_levels=3)
    whole = run_experiment(
        tmp_path / "exp",
        ZYX,
        output_folder=tmp_path / "whole",
        convert_tiffs=True,
        conversion=settings,
    )
    limit = plane_layout(tmp_path / "exp/a.tif", ZYX, memory_limit=1).block_memory_estimate
    blocks = run_experiment(
        tmp_path / "exp",
        ZYX,
        output_folder=tmp_path / "blocks",
        convert_tiffs=True,
        conversion=settings,
        memory_limit=limit + 4096,
    )
    assert whole.written and blocks.written
    assert [i.code for i in blocks.issues] == ["planes_read_in_blocks"]
    assert read_manifest(blocks.manifest_path).files[0].read_in_blocks
    assert not read_manifest(whole.manifest_path).files[0].read_in_blocks
    assert blocks.csv_path.read_bytes() == whole.csv_path.read_bytes()
    copied, expected = (
        open_copy(blocks.output_folder, "a.tif"),
        open_copy(whole.output_folder, "a.tif"),
    )
    np.testing.assert_array_equal(copied["0"][:], data)
    got, want = levels(copied), levels(expected)
    assert list(got) == ["0", "1", "2", "3"] and list(want) == list(got)
    for name in want:
        np.testing.assert_array_equal(got[name], want[name])
    assert dict(copied.attrs) == dict(expected.attrs)  # same display ranges and scales


@needs_zarr
@pytest.mark.parametrize("memory_limit", [2**30, 6000])
def test_writer_builds_smaller_levels_of_planes_in_blocks(tmp_path, memory_limit):
    from morphobricks_preprocessing import Block, BlockReader, OmeZarrWriter, Plane

    data = random_data((1, 77, 53), "uint16")
    mapping = prefill_mapping("ZYX")
    layout = plane_layout(write_data(tmp_path / "a.tif", data, axes="ZYX"), mapping)
    settings = ConversionSettings(chunk_size=16, pyramid_levels=4)

    def uneven_blocks():
        for top, bottom in [(0, 5), (5, 40), (40, 77)]:
            for left, right in [(0, 33), (33, 53)]:
                yield Block(top, left, data[0, top:bottom, left:right])

    reader = BlockReader((77, 53), data.dtype, uneven_blocks)
    in_blocks = OmeZarrWriter.create(
        tmp_path / "blocks.ome.zarr", mapping, layout, settings=settings, memory_limit=memory_limit
    )
    in_blocks.write(Plane(0, None, None, None, reader=reader))
    in_blocks.finish()
    whole = OmeZarrWriter.create(tmp_path / "whole.ome.zarr", mapping, layout, settings=settings)
    whole.write(Plane(0, None, None, data[0]))
    whole.finish()
    import zarr

    got = levels(zarr.open_group(str(tmp_path / "blocks.ome.zarr"), mode="r"))
    want = levels(zarr.open_group(str(tmp_path / "whole.ome.zarr"), mode="r"))
    assert list(got) == list(want) == ["0", "1", "2", "3", "4"]
    for name in want:
        np.testing.assert_array_equal(got[name], want[name])
    assert in_blocks._region_side() == (32 if memory_limit == 2**30 else 8)
    tiny = OmeZarrWriter.create(
        tmp_path / "tiny.ome.zarr", mapping, layout, settings=settings, memory_limit=1
    )
    assert tiny._region_side() == 2  # the smallest region that halves exactly


@needs_zarr
def test_unknown_pixel_sizes_are_marked_not_read_as_1(tmp_path):
    # Issue #39: OME-NGFF 0.4 needs a scale per axis, so an unknown size is written as 1;
    # the copy says which axes those are, and reading the copy gives no size for them.
    from morphobricks_preprocessing import open_ngff

    write_data(tmp_path / "exp" / "a.tif", random_data((2, 8, 8), "uint8"), axes="ZYX")
    result = run_experiment(tmp_path / "exp", ZYX, convert_tiffs=True)
    copy = result.output_folder / ome_zarr_relative_path("a.tif")
    meta = multiscale(open_copy(result.output_folder, "a.tif"))
    assert meta["datasets"][0]["coordinateTransformations"][0]["scale"] == [1.0, 1.0, 1.0]
    assert meta["metadata"]["unknown_scale"] == ["z", "y", "x"]
    assert open_ngff(copy).physical_sizes == PhysicalSizes()
    # A copy written before the marker counts as out of date and is written again.
    attrs_path = copy / ".zattrs"
    attrs = json.loads(attrs_path.read_text(encoding="utf-8"))
    del attrs["multiscales"][0]["metadata"]["unknown_scale"]
    attrs_path.write_text(json.dumps(attrs), encoding="utf-8")
    again = run_experiment(tmp_path / "exp", ZYX, convert_tiffs=True)
    assert again.converted == ("a.tif",)
    assert run_experiment(tmp_path / "exp", ZYX, convert_tiffs=True).converted == ()


@needs_zarr
def test_known_pixel_sizes_are_not_marked(tmp_path):
    path = tmp_path / "exp/a.ome.tif"
    path.parent.mkdir(parents=True)
    tifffile.imwrite(
        path,
        random_data((2, 8, 8), "uint8"),
        ome=True,
        photometric="minisblack",
        metadata={"axes": "ZYX", "PhysicalSizeX": 0.5, "PhysicalSizeY": 0.5},
    )
    result = run_experiment(tmp_path / "exp", ZYX, convert_tiffs=True)
    meta = multiscale(open_copy(result.output_folder, "a.ome.tif"))
    assert meta["metadata"]["unknown_scale"] == ["z"]  # no z-step in the metadata
