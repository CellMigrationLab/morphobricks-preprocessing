import os

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    AxisMapping,
    NgffError,
    PhysicalSizes,
    Severity,
    TiffMetadata,
    declared_mapping,
    discover_experiment,
    file_mapping_for,
    inspect_image,
    inspect_images,
    iter_planes,
    ome_zarr_relative_path,
    open_ngff,
    plane_layout,
    prefill_mapping,
    preflight,
    read_manifest,
    read_physical_sizes,
    read_plane,
    read_plane_histograms,
    read_sample_plane,
    reference_axes,
    review_axes,
    run_experiment,
    zarr_input,
)
from morphobricks_preprocessing import run as run_module
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.histogram import compute_plane_histogram
from morphobricks_preprocessing.zarr_export import ConversionSettings
from morphobricks_preprocessing.zarr_input import ngff_axes
from tiff_tree import write_data, write_file
from zarr_tree import write_bioformats2raw, write_ngff, write_plate, zarr_major

pytest.importorskip("zarr")

VERSIONS = ["0.4", "0.5"]


def codes(issues):
    return [issue.code for issue in issues]


def issue(issues, code):
    (found,) = [i for i in issues if i.code == code]
    return found


def random_data(shape, dtype="uint16", seed=0):
    dt = np.dtype(dtype)
    rng = np.random.default_rng(seed)
    if dt.kind == "f":
        return rng.normal(100, 30, shape).astype(dt)
    info = np.iinfo(dt)
    return rng.integers(info.min, info.max, shape, dtype=dt, endpoint=True)


def meta(path, shape, axes, kind="ome-zarr"):
    return TiffMetadata(path, True, tuple(shape), np.dtype("uint16"), axes, 1, 0, kind=kind)


# Axis metadata --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("axes", "ndim", "letters"),
    [
        ([{"name": "t", "type": "time"}, {"name": "y", "type": "space"}, {"name": "x"}], 3, "TYX"),
        (
            [{"name": "c", "type": "channel"}, {"name": "z", "type": "space"}]
            + [{"name": "y"}, {"name": "x"}],
            4,
            "CZYX",
        ),
        (["t", "c", "z", "y", "x"], 5, "TCZYX"),  # OME-NGFF 0.3 names only
        (None, 5, "TCZYX"),  # 0.1 and 0.2: fixed order
        ([{"name": "angle", "type": "angle"}, {"name": "y"}, {"name": "x"}], 3, "QYX"),
        ([{"name": "frames", "type": "time"}, {"name": "y"}, {"name": "x"}], 3, "TYX"),
        ([{"name": "lambda", "type": "space"}, {"name": "y"}, {"name": "x"}], 3, "QYX"),
    ],
)
def test_ngff_axes_become_letters(axes, ndim, letters):
    assert ngff_axes(axes, ndim) == letters


def test_ngff_axes_without_metadata_need_five_dimensions():
    with pytest.raises(NgffError):
        ngff_axes(None, 3)


# Opening stores -------------------------------------------------------------------------


@pytest.mark.parametrize("version", VERSIONS)
def test_open_ngff(tmp_path, version):
    data = random_data((2, 3, 8, 9))
    path = write_ngff(
        tmp_path / "a.ome.zarr", data, "czyx", version=version, channel_names=["DAPI", "GFP"]
    )
    image = open_ngff(path)
    assert image.axes == "CZYX" and image.shape == (2, 3, 8, 9)
    assert image.dtype == np.dtype("uint16") and image.version == version
    assert image.image_count == 1 and image.dataset_path == "0"
    assert image.channel_names == ("DAPI", "GFP")
    assert image.chunks == (1, 1, 8, 9)


# Physical sizes (OME-NGFF coordinateTransformations and axis units) --------------------

MICRONS = {"z": "micrometer", "y": "micrometer", "x": "micrometer"}


@pytest.mark.parametrize("version", ["0.4", "0.5"])
def test_physical_sizes_with_units(tmp_path, version):
    path = write_ngff(
        tmp_path / "a.ome.zarr",
        np.zeros((2, 3, 8, 8), np.uint8),
        "czyx",
        version=version,
        scale=[1.0, 2.0, 0.5, 0.25],
        units={**MICRONS, "y": "nanometer"},
    )
    sizes = open_ngff(path).physical_sizes
    assert sizes == PhysicalSizes(
        x=0.25,
        y=0.5,
        z=2.0,
        space_unit="micrometer",
        x_unit="micrometer",
        y_unit="nanometer",
        z_unit="micrometer",
    )
    assert [sizes.unit(role) for role in "XYZCT"] == [
        "micrometer",
        "nanometer",
        "micrometer",
        None,
        None,
    ]


@pytest.mark.parametrize("version", ["0.4", "0.5"])
def test_physical_sizes_without_units(tmp_path, version):
    path = write_ngff(
        tmp_path / "a.ome.zarr",
        np.zeros((3, 8, 8), np.uint8),
        "zyx",
        version=version,
        scale=[2.0, 0.5, 0.25],
    )
    sizes = open_ngff(path).physical_sizes
    assert (sizes.x, sizes.y, sizes.z, sizes.t) == (0.25, 0.5, 2.0, None)
    assert [sizes.unit(role) for role in "XYZ"] == [None, None, None]
    assert sizes.space_unit is None


@pytest.mark.parametrize("version", ["0.4", "0.5"])
def test_physical_sizes_with_a_multiscale_level_scale(tmp_path, version):
    # OME-NGFF applies the multiscales entry's transformations after the dataset's.
    path = write_ngff(
        tmp_path / "a.ome.zarr",
        np.zeros((4, 3, 8, 8), np.uint8),
        "tzyx",
        version=version,
        scale=[1.0, 1.5, 0.2, 0.25],
        units={**MICRONS, "t": "second"},
        multiscale_transforms=[{"type": "scale", "scale": [30.0, 2.0, 3.0, 4.0]}],
    )
    sizes = open_ngff(path).physical_sizes
    assert (sizes.t, sizes.z, sizes.y, sizes.x) == pytest.approx((30.0, 3.0, 0.6, 1.0))
    assert (sizes.unit("T"), sizes.unit("X")) == ("second", "micrometer")


def test_multiscale_level_translation_does_not_change_sizes(tmp_path):
    path = write_ngff(
        tmp_path / "a.ome.zarr",
        np.zeros((8, 8), np.uint8),
        "yx",
        scale=[0.5, 0.25],
        multiscale_transforms=[{"type": "translation", "translation": [10.0, 20.0]}],
    )
    sizes = open_ngff(path).physical_sizes
    assert (sizes.x, sizes.y) == (0.25, 0.5)


@pytest.mark.parametrize(
    "transforms",
    [
        [],  # no coordinateTransformations at all (OME-NGFF before 0.4)
        [{"type": "scale", "path": "scale.bin"}],  # stored in a file: not read
        [{"type": "scale", "scale": [0.5, 0.25]}],  # not one number per axis
        [{"type": "translation", "translation": [0.0, 0.0, 0.0]}],
    ],
)
def test_physical_sizes_absent_when_not_given(tmp_path, transforms):
    path = write_ngff(
        tmp_path / "a.ome.zarr",
        np.zeros((3, 8, 8), np.uint8),
        "zyx",
        dataset_transforms=transforms,
        units=MICRONS,
    )
    assert open_ngff(path).physical_sizes == PhysicalSizes()


def test_unusable_multiscale_level_scale_makes_sizes_unknown(tmp_path):
    path = write_ngff(
        tmp_path / "a.ome.zarr",
        np.zeros((8, 8), np.uint8),
        "yx",
        scale=[0.5, 0.25],
        multiscale_transforms=[{"type": "scale", "path": "scale.bin"}],
    )
    assert open_ngff(path).physical_sizes == PhysicalSizes()


def test_scales_that_are_not_positive_numbers_are_unknown(tmp_path):
    path = write_ngff(
        tmp_path / "a.ome.zarr",
        np.zeros((3, 8, 8), np.uint8),
        "zyx",
        scale=[0.0, -1.0, 0.25],
        units=MICRONS,
    )
    sizes = open_ngff(path).physical_sizes
    assert (sizes.x, sizes.y, sizes.z) == (0.25, None, None)
    assert (sizes.unit("X"), sizes.unit("Y")) == ("micrometer", None)


@pytest.mark.parametrize("version", ["0.4", "0.5"])
def test_header_of_a_store_carries_its_physical_sizes(tmp_path, version):
    path = write_ngff(
        tmp_path / "a.ome.zarr",
        np.zeros((3, 8, 8), np.uint8),
        "zyx",
        version=version,
        scale=[2.0, 0.5, 0.25],
        units=MICRONS,
    )
    info = inspect_image(path)
    assert info.is_ome_zarr and info.physical_sizes == open_ngff(path).physical_sizes
    assert (info.physical_sizes.x, info.physical_sizes.unit("Z")) == (0.25, "micrometer")


def test_converted_copies_give_the_sizes_of_their_tiff(tmp_path):
    source = tmp_path / "exp/a.ome.tif"
    source.parent.mkdir(parents=True)
    tifffile.imwrite(
        source,
        np.zeros((2, 3, 8, 8), np.uint16),
        ome=True,
        photometric="minisblack",
        metadata={
            "axes": "TZYX",
            "PhysicalSizeX": 120.0,
            "PhysicalSizeXUnit": "nm",
            "PhysicalSizeY": 0.25,
            "PhysicalSizeYUnit": "µm",
            "PhysicalSizeZ": 2.0,
            "TimeIncrement": 30.0,
            "TimeIncrementUnit": "s",
        },
    )
    result = run_experiment(tmp_path / "exp", prefill_mapping("TZYX"), convert_tiffs=True)
    copy = result.output_folder / ome_zarr_relative_path("a.ome.tif")
    assert open_ngff(copy).physical_sizes == read_physical_sizes(source)


def test_bioformats2raw_layout(tmp_path):
    one = write_bioformats2raw(tmp_path / "one.ome.zarr", [random_data((2, 8, 8))])
    image = open_ngff(one)
    assert image.image_count == 1 and image.dataset_path == "0/0"
    two = write_bioformats2raw(tmp_path / "two.ome.zarr", [random_data((2, 8, 8))] * 2)
    assert open_ngff(two).image_count == 2


def test_plates_and_several_multiscales_hold_several_images(tmp_path):
    plate = write_plate(tmp_path / "plate.ome.zarr", random_data((2, 8, 8)))
    assert open_ngff(plate).image_count >= 2
    several = write_ngff(
        tmp_path / "s.ome.zarr", random_data((2, 8, 8)), "czyx"[1:], extra_multiscales=1
    )
    assert open_ngff(several).image_count == 2


@pytest.mark.parametrize("version", VERSIONS)
def test_big_endian_arrays_are_native(tmp_path, version):
    data = random_data((2, 8, 8)).astype(">u2")
    path = write_ngff(tmp_path / "a.ome.zarr", data, "zyx", version=version)
    (plane, _) = list(iter_planes(path, prefill_mapping("ZYX")))
    assert plane.pixels.dtype == np.dtype("uint16")
    np.testing.assert_array_equal(plane.pixels, data[0])


def test_folders_that_are_not_ome_zarr_images(tmp_path):
    empty = tmp_path / "empty.zarr"
    empty.mkdir()
    with pytest.raises(NgffError):
        open_ngff(empty)
    info = inspect_image(empty, "empty.zarr")
    assert not info.readable and info.is_ome_zarr
    report = inspect_images(tmp_path, ["empty.zarr"])
    found = issue(report.issues, "unreadable_ome_zarr")
    assert found.severity is Severity.ERROR and found.paths == ("empty.zarr",)
    assert "Traceback" not in str(found)


def test_zarr_format_3_needs_zarr_3(tmp_path, monkeypatch):
    path = tmp_path / "a.ome.zarr"
    path.mkdir()
    (path / "zarr.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(zarr_input, "_zarr_major", lambda: 2)
    with pytest.raises(NgffError, match="needs zarr 3"):
        open_ngff(path)


def test_missing_zarr_package_is_reported(tmp_path, monkeypatch):
    path = write_ngff(tmp_path / "a.ome.zarr", random_data((2, 8, 8)), "zyx")

    def missing():
        raise zarr_input.ZarrUnavailableError("no zarr")

    monkeypatch.setattr(zarr_input, "_zarr", missing)
    report = inspect_images(tmp_path, ["a.ome.zarr"])
    found = issue(report.issues, "zarr_not_installed")
    assert found.paths == ("a.ome.zarr",) and "[zarr]" in found.action
    assert path.exists()


# Discovery and metadata -------------------------------------------------------------------


def test_ome_zarr_folders_are_images_not_subfolders(tmp_path):
    root = tmp_path / "exp"
    write_ngff(root / "ctrl/rep1/a.ome.zarr", random_data((2, 8, 8)), "zyx")
    write_ngff(root / "ctrl/rep1/b.zarr", random_data((2, 8, 8)), "zyx")
    write_data(root / "ctrl/rep1/c.tif", random_data((2, 8, 8)), axes="ZYX")
    write_ngff(root / "drug/rep1/tech/x.ome.zarr", random_data((2, 8, 8)), "zyx")
    discovery = discover_experiment(root)
    assert discovery.images == (
        "ctrl/rep1/a.ome.zarr",
        "ctrl/rep1/b.zarr",
        "ctrl/rep1/c.tif",
        "drug/rep1/tech/x.ome.zarr",
    )
    assert discovery.labels_for("ctrl/rep1/a.ome.zarr") == ("ctrl", "rep1", "single")
    # The store's own folders (arrays, chunks) are never taken for experiment folders.
    assert "folder_too_deep" not in codes(discovery.issues)


def test_ome_zarr_next_to_subfolders_is_outside_a_leaf(tmp_path):
    root = tmp_path / "exp"
    write_ngff(root / "ctrl/a.ome.zarr", random_data((2, 8, 8)), "zyx")
    write_data(root / "ctrl/rep1/b.tif", random_data((2, 8, 8)), axes="ZYX")
    found = issue(discover_experiment(root).issues, "tiff_outside_leaf")
    assert found.paths == ("ctrl/a.ome.zarr",) and "1 image is" in found.message


def test_metadata_of_a_store(tmp_path):
    write_ngff(tmp_path / "a.ome.zarr", random_data((2, 3, 8, 9)), "czyx", channel_names=["A", "B"])
    (info,) = inspect_images(tmp_path, ["a.ome.zarr"]).files
    assert (info.kind, info.axes, info.shape, info.series_count) == (
        "ome-zarr",
        "CZYX",
        (2, 3, 8, 9),
        1,
    )
    assert info.channel_names == ("A", "B")


def test_several_images_in_a_store_are_blocked(tmp_path):
    write_bioformats2raw(tmp_path / "two.ome.zarr", [random_data((2, 8, 8))] * 2)
    write_plate(tmp_path / "plate.ome.zarr", random_data((2, 8, 8)))
    found = issue(
        inspect_images(tmp_path, ["plate.ome.zarr", "two.ome.zarr"]).issues, "multiple_series"
    )
    assert found.paths == ("plate.ome.zarr", "two.ome.zarr")
    assert "OME-Zarr" in found.message and "exclude" in found.action


def test_mixed_formats_with_different_axis_orders_are_not_a_dimension_conflict(tmp_path):
    write_data(tmp_path / "a.tif", random_data((3, 2, 8, 8)), axes="ZCYX", imagej=True)
    write_ngff(tmp_path / "b.ome.zarr", random_data((2, 3, 8, 8)), "czyx")
    report = inspect_images(tmp_path, ["a.tif", "b.ome.zarr"])
    assert report.issues == ()


# Axes ------------------------------------------------------------------------------------


EXPERIMENT = prefill_mapping("ZCYX")


@pytest.mark.parametrize(
    ("axes", "shape", "roles", "singletons", "problem"),
    [
        ("CZYX", (2, 3, 8, 8), ("C", "Z", "Y", "X"), (), ""),
        ("TCZYX", (1, 2, 3, 8, 8), (None, "C", "Z", "Y", "X"), (), ""),  # singleton T dropped
        ("ZYX", (3, 8, 8), ("Z", "Y", "X"), ("C",), "missing"),
        ("TZYX", (4, 3, 8, 8), None, None, "mismatch"),  # time planes the experiment lacks
        ("QZYX", (2, 3, 8, 8), None, None, "mismatch"),  # unknown axis with planes
        ("QZYX", (1, 3, 8, 8), (None, "Z", "Y", "X"), ("C",), "missing"),
        ("CZY", (2, 3, 8), None, None, "mismatch"),  # no X
    ],
)
def test_declared_mapping(axes, shape, roles, singletons, problem):
    resolved, found = declared_mapping(meta("a.ome.zarr", shape, axes), EXPERIMENT)
    assert found == problem
    if roles is None:
        assert resolved is None
    else:
        assert resolved == AxisMapping(axes, roles, singletons)


def test_file_mapping_for_each_kind():
    assert file_mapping_for(meta("a", (3, 2, 8, 8), "ZCYX", "tiff"), EXPERIMENT) is EXPERIMENT
    zarr = meta("b", (2, 3, 8, 8), "CZYX")
    assert file_mapping_for(zarr, EXPERIMENT).roles == ("C", "Z", "Y", "X")
    lacking = meta("c", (3, 8, 8), "ZYX")
    assert file_mapping_for(lacking, EXPERIMENT, missing_as_singleton=False) is None
    assert file_mapping_for(lacking, EXPERIMENT).singleton_roles == ("C",)
    tiff_lacking = meta("d", (3, 8, 8), "ZYX", "tiff")
    assert file_mapping_for(tiff_lacking, EXPERIMENT).singleton_roles == ("C",)


def test_reference_axes_prefer_tiffs():
    files = [meta("a", (3, 2, 8, 8), "ZCYX", "tiff"), meta("b", (1, 2, 3, 8, 8), "TCZYX")]
    assert reference_axes(files) == "ZCYX"
    zarr_only = [meta("a", (2, 3, 8, 8), "CZYX"), meta("b", (1, 2, 3, 8, 8), "TCZYX")]
    assert reference_axes(zarr_only) == "TCZYX"


def test_review_of_a_mixed_experiment():
    files = [
        meta("a.tif", (3, 2, 8, 8), "ZCYX", "tiff"),
        meta("b.ome.zarr", (2, 3, 8, 8), "CZYX"),
        meta("c.ome.zarr", (3, 8, 8), "ZYX"),
        meta("d.ome.zarr", (4, 3, 8, 8), "TZYX"),
    ]
    review = review_axes(files, EXPERIMENT)
    assert set(review.file_mappings) == {"a.tif", "b.ome.zarr"}
    assert issue(review.issues, "missing_axes").paths == ("c.ome.zarr",)
    found = issue(review.issues, "ome_zarr_axes_mismatch")
    assert found.severity is Severity.ERROR and found.paths == ("d.ome.zarr",)
    confirmed = review_axes(files[:3], EXPERIMENT, missing_as_singleton=True)
    assert confirmed.ok and confirmed.file_mappings["c.ome.zarr"].singleton_roles == ("C",)


# Reading planes ------------------------------------------------------------------------


LAYOUTS = [
    pytest.param("yx", (6, 7), id="2d"),
    pytest.param("zyx", (3, 6, 7), id="zstack"),
    pytest.param("czyx", (2, 3, 6, 7), id="czyx"),
    pytest.param("tczyx", (2, 2, 3, 6, 7), id="tczyx"),
    pytest.param("tyx", (4, 6, 7), id="timelapse"),
]


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize(("axes", "shape"), LAYOUTS)
def test_planes_match_the_array(tmp_path, version, axes, shape):
    data = random_data(shape)
    path = write_ngff(tmp_path / "a.ome.zarr", data, axes, version=version)
    mapping = prefill_mapping(axes.upper())
    planes = list(iter_planes(path, mapping))
    others = [i for i, a in enumerate(axes) if a not in "yx"]
    assert len(planes) == int(np.prod([shape[i] for i in others]))
    for plane in planes:
        index = tuple(plane.coordinates[a] if a not in "yx" else slice(None) for a in axes)
        np.testing.assert_array_equal(plane.pixels, data[index])
        assert plane.pixels.flags.c_contiguous


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
def test_every_dtype_is_read_unchanged(tmp_path, dtype):
    data = random_data((2, 6, 7), dtype)
    path = write_ngff(tmp_path / "a.ome.zarr", data, "zyx")
    planes = list(iter_planes(path, prefill_mapping("ZYX")))
    for z, plane in enumerate(planes):
        assert plane.pixels.dtype == np.dtype(dtype)
        np.testing.assert_array_equal(plane.pixels, data[z])


def test_chunks_spanning_several_planes(tmp_path):
    data = random_data((4, 3, 16, 16))
    path = write_ngff(tmp_path / "a.ome.zarr", data, "czyx", chunks=(2, 3, 8, 8))
    mapping = prefill_mapping("CZYX")
    layout = plane_layout(path, mapping)
    # Each plane touches 4 chunks of 2 x 3 x 8 x 8 values.
    assert layout.page_shape == (2, 3, 8, 8)
    assert layout.page_nbytes == 4 * 2 * 3 * 8 * 8 * 2
    assert layout.encoded_nbytes == 0
    for plane in iter_planes(path, mapping):
        np.testing.assert_array_equal(plane.pixels, data[plane.c, plane.z])


def test_read_plane_and_sample_plane(tmp_path):
    data = random_data((2, 5, 8, 8))
    path = write_ngff(tmp_path / "a.ome.zarr", data, "czyx")
    mapping = prefill_mapping("CZYX")
    plane = read_plane(path, mapping, c=1, z=3)
    np.testing.assert_array_equal(plane.pixels, data[1, 3])
    sample = read_sample_plane(path, mapping)
    assert (sample.z, sample.c, sample.t) == (2, 0, None)
    with pytest.raises(IndexError):
        read_plane(path, mapping, z=5)


def test_memory_limit_applies_to_stores(tmp_path):
    from morphobricks_preprocessing import PlaneMemoryError

    path = write_ngff(tmp_path / "a.ome.zarr", random_data((2, 32, 32)), "zyx")
    mapping = prefill_mapping("ZYX")
    with pytest.raises(PlaneMemoryError, match="even in blocks"):
        next(iter_planes(path, mapping, memory_limit=1000))


def smallest_limit(path, mapping, bin_count=None):
    layout = plane_layout(path, mapping, memory_limit=1, bin_count=bin_count)
    return layout.block_memory_estimate


@pytest.mark.parametrize(
    ("shape", "axes", "chunks"),
    [
        pytest.param((2, 40, 36), "zyx", None, id="plane-chunks"),
        pytest.param((2, 40, 36), "zyx", (1, 16, 16), id="small-chunks"),
        pytest.param((2, 40, 36), "zyx", (2, 16, 16), id="chunks-across-planes"),
        pytest.param((2, 3, 40, 36), "czyx", (1, 1, 32, 8), id="tall-chunks"),
    ],
)
@pytest.mark.parametrize("version", VERSIONS)
def test_planes_of_stores_in_blocks_match_the_source(tmp_path, shape, axes, chunks, version):
    data = random_data(shape, "uint16")
    path = write_ngff(tmp_path / "a.ome.zarr", data, axes, version=version, chunks=chunks)
    mapping = prefill_mapping(axes.upper())
    limit = smallest_limit(path, mapping)
    layout = plane_layout(path, mapping, memory_limit=limit)
    assert layout.in_blocks and layout.block_memory_estimate <= limit
    planes = iter_planes(path, mapping, memory_limit=limit)
    for plane, index in zip(planes, np.ndindex(*shape[:-2]), strict=True):
        assert plane.in_blocks
        expected = data[index]
        covered = np.zeros(expected.shape, dtype=int)
        for block in plane.blocks():
            rows, cols = block.pixels.shape
            where = (slice(block.y, block.y + rows), slice(block.x, block.x + cols))
            np.testing.assert_array_equal(block.pixels, expected[where])
            covered[where] += 1
        assert (covered == 1).all()
        np.testing.assert_array_equal(
            plane.histogram().counts, compute_plane_histogram(expected).counts
        )


@pytest.mark.parametrize("dtype", ["int8", "uint32", "int64", "float32", "float64"])
def test_store_planes_in_blocks_give_the_same_histograms(tmp_path, dtype):
    data = random_data((2, 24, 20), dtype)
    if data.dtype.kind == "f":
        data[1, 3, :2] = [np.nan, np.inf]
    path = write_ngff(tmp_path / "a.ome.zarr", data, "zyx", chunks=(1, 8, 8))
    mapping = prefill_mapping("ZYX")
    for plane, source in zip(
        iter_planes(path, mapping, memory_limit=smallest_limit(path, mapping, 9), bin_count=9),
        data,
        strict=True,
    ):
        expected, hist = compute_plane_histogram(source, 9), plane.histogram(9)
        assert plane.in_blocks
        assert (hist.pixel_min, hist.pixel_max) == (expected.pixel_min, expected.pixel_max)
        np.testing.assert_array_equal(hist.counts, expected.counts)


def test_large_chunks_are_read_a_few_rows_at_a_time(tmp_path):
    # A single chunk per plane, larger than the limit allows for the whole plane.
    data = random_data((1, 64, 48), "uint16")
    path = write_ngff(tmp_path / "a.ome.zarr", data, "zyx")
    mapping = prefill_mapping("ZYX")
    layout = plane_layout(path, mapping, memory_limit=smallest_limit(path, mapping))
    assert layout.block_shape[0] < 64
    preview = read_plane(path, mapping, memory_limit=layout.memory_estimate - 1, preview=True)
    assert preview.step >= 2
    np.testing.assert_array_equal(preview.pixels, data[0, :: preview.step, :: preview.step])


def test_missing_role_reads_as_coordinate_zero(tmp_path):
    data = random_data((3, 8, 8))
    path = write_ngff(tmp_path / "a.ome.zarr", data, "zyx")
    mapping = AxisMapping("ZYX", "ZYX", singleton_roles=("C",))
    assert [p.coordinates for p in iter_planes(path, mapping)] == [
        {"z": z, "c": 0, "t": None} for z in range(3)
    ]


# Runs ------------------------------------------------------------------------------------


@pytest.fixture
def zarr_experiment(tmp_path):
    root = tmp_path / "exp"
    for i, rel in enumerate(
        ["ctrl/rep1/a.ome.zarr", "ctrl/rep1/b.ome.zarr", "drug/rep1/a.ome.zarr"]
    ):
        write_ngff(root / rel, random_data((2, 3, 12, 12), "uint8", seed=i), "czyx")
    return root


def test_ome_zarr_experiment_end_to_end(zarr_experiment):
    pre = preflight(zarr_experiment)
    assert pre.ok and pre.suggested_mapping == prefill_mapping("CZYX")
    result = run_experiment(zarr_experiment, pre.suggested_mapping)
    assert result.written and result.converted == ()
    records = read_plane_histograms(result.csv_path)
    assert len(records) == 18
    import zarr

    for record in records:
        data = zarr.open_group(str(zarr_experiment / record.source_relative_path), mode="r")["0"]
        plane = np.asarray(data[record.c, record.z])
        expected = compute_plane_histogram(plane)
        np.testing.assert_array_equal(record.histogram.counts, expected.counts)
        assert record.t == 0
    assert records[0].plane_id == "ctrl/rep1/a.ome.zarr::z=0,c=0,t=0"
    assert {(r.condition, r.position_id) for r in records} == {
        ("ctrl", "a"),
        ("ctrl", "b"),
        ("drug", "a"),
    }


def test_rerun_detects_a_changed_store(zarr_experiment):
    mapping = prefill_mapping("CZYX")
    first = run_experiment(zarr_experiment, mapping)
    table = first.csv_path.read_bytes()
    assert run_experiment(zarr_experiment, mapping).read == ()
    import zarr

    store = zarr_experiment / "ctrl/rep1/b.ome.zarr"
    array = zarr.open_group(str(store), mode="r+")["0"]
    array[1, 2] = np.full((12, 12), 7, np.uint8)
    newest = max(p.stat().st_mtime_ns for p in store.rglob("*") if p.is_file())
    for p in store.rglob("*"):
        if p.is_file():
            os.utime(p, ns=(newest + 10**9, newest + 10**9))
    second = run_experiment(zarr_experiment, mapping)
    assert second.changed == ("ctrl/rep1/b.ome.zarr",) and second.read == second.changed
    entry = read_manifest(second.manifest_path).file("ctrl/rep1/b.ome.zarr")
    assert entry.size == sum(p.stat().st_size for p in store.rglob("*") if p.is_file())
    assert second.csv_path.read_bytes() != table


def test_mixed_tiff_and_ome_zarr_experiment(tmp_path):
    root = tmp_path / "exp"
    tiff_data = random_data((3, 2, 8, 8), "uint8")
    zarr_data = random_data((2, 3, 8, 8), "uint8", seed=1)
    write_data(root / "ctrl/a.tif", tiff_data, axes="ZCYX", imagej=True)
    write_ngff(root / "ctrl/b.ome.zarr", zarr_data, "czyx")
    write_ngff(root / "drug/c.ome.zarr", random_data((3, 8, 8), "uint8", seed=2), "zyx")
    pre = preflight(root)
    assert pre.suggested_mapping == prefill_mapping("ZCYX")
    blocked = run_experiment(root, pre.suggested_mapping)
    assert codes(blocked.issues) == ["missing_axes"]
    result = run_experiment(root, pre.suggested_mapping, missing_as_singleton=True)
    assert result.written
    rows = {r.plane_id: r for r in read_plane_histograms(result.csv_path)}
    # The same coordinates mean the same plane, whatever the storage order.
    b = rows["ctrl/b.ome.zarr::z=2,c=1,t=0"]
    np.testing.assert_array_equal(
        b.histogram.counts, compute_plane_histogram(zarr_data[1, 2]).counts
    )
    a = rows["ctrl/a.tif::z=2,c=1,t=0"]
    np.testing.assert_array_equal(
        a.histogram.counts, compute_plane_histogram(tiff_data[2, 1]).counts
    )
    assert {(r.z, r.c) for r in rows.values() if r.source_relative_path == "drug/c.ome.zarr"} == {
        (0, 0),
        (1, 0),
        (2, 0),
    }


def test_conversion_leaves_ome_zarr_images_alone(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "a.tif", random_data((3, 8, 8), "uint8"), axes="ZYX")
    write_ngff(root / "b.ome.zarr", random_data((3, 8, 8), "uint8", seed=1), "zyx")
    result = run_experiment(root, prefill_mapping("ZYX"), convert_tiffs=True)
    assert result.converted == ("a.tif",)
    manifest = read_manifest(result.manifest_path)
    assert manifest.file("b.ome.zarr").ome_zarr_path is None
    assert manifest.file("a.tif").ome_zarr_path == "ome_zarr/a.ome.zarr"
    # A converted image keeps the position identifier of its source.
    assert {r.position_id for r in read_plane_histograms(result.csv_path)} == {"a", "b"}
    assert manifest.file("a.tif").position_id == "a"


def test_sample_planes_cover_both_formats(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "a.tif", random_data((3, 2, 8, 8), "uint8"), axes="ZCYX", imagej=True)
    write_ngff(root / "b.ome.zarr", random_data((2, 3, 8, 8), "uint8"), "czyx")
    pre = preflight(root)
    samples = run_module.sample_planes(pre, pre.suggested_mapping)
    assert [(s.relative_path, s.plane.z, s.plane.c) for s in samples] == [
        ("a.tif", 1, 0),
        ("b.ome.zarr", 1, 0),
    ]


@pytest.mark.skipif(zarr_major() < 3, reason="OME-NGFF 0.5 needs zarr 3")
def test_ome_ngff_0_5_experiment(tmp_path):
    root = tmp_path / "exp"
    write_ngff(root / "a.ome.zarr", random_data((2, 3, 8, 8), "uint8"), "czyx", version="0.5")
    result = run_experiment(root, prefill_mapping("CZYX"))
    assert result.written and result.plane_count == 6


def test_cli_check_names_the_formats(tmp_path, capsys):
    root = tmp_path / "exp"
    write_data(root / "a.tif", random_data((3, 2, 8, 8), "uint8"), axes="ZCYX", imagej=True)
    write_ngff(root / "b.ome.zarr", random_data((2, 3, 8, 8), "uint8"), "czyx")
    assert main(["check", str(root)]) == 0
    out = capsys.readouterr().out
    assert "Formats: 1 TIFF, 1 OME-Zarr" in out and "--axes ZCYX" in out


def test_unreadable_store_can_be_excluded(tmp_path):
    root = tmp_path / "exp"
    write_ngff(root / "a.ome.zarr", random_data((2, 8, 8), "uint8"), "zyx")
    (root / "bad.ome.zarr").mkdir()
    write_file(root / "bad.ome.zarr" / "junk.txt")
    assert "unreadable_ome_zarr" in codes(preflight(root).issues)
    result = run_experiment(root, prefill_mapping("ZYX"), exclude=["bad.ome.zarr"])
    assert result.written and result.excluded == ("bad.ome.zarr",)


def test_tiff_reading_is_unchanged_next_to_stores(tmp_path):
    # A plain TIFF still reads through tifffile (one page at a time).
    path = write_data(tmp_path / "a.tif", random_data((2, 8, 8)), axes="ZYX")
    assert tifffile.imread(path).shape == (2, 8, 8)
    assert len(list(iter_planes(path, prefill_mapping("ZYX")))) == 2


# Physical sizes never block an image -----------------------------------------------------


def test_scale_too_large_for_a_float_is_unknown_and_does_not_block(tmp_path):
    root = tmp_path / "exp"
    path = write_ngff(
        root / "ctrl/a.ome.zarr",
        np.zeros((2, 8, 8), np.uint8),
        "zyx",
        scale=[10**400, 0.5, 0.25],
        units={"z": "micrometer", "y": "micrometer", "x": "micrometer"},
    )
    sizes = open_ngff(path).physical_sizes
    assert (sizes.z, sizes.y, sizes.x) == (None, 0.5, 0.25)
    assert preflight(root, tmp_path / "out").ok


def test_unexpected_scale_metadata_does_not_block_the_image(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("not understood")

    monkeypatch.setattr(zarr_input, "ngff_physical_sizes", broken)
    root = tmp_path / "exp"
    path = write_ngff(root / "ctrl/a.ome.zarr", np.zeros((2, 8, 8), np.uint8), "zyx")
    assert open_ngff(path).physical_sizes == PhysicalSizes()
    assert preflight(root, tmp_path / "out").ok


@pytest.mark.parametrize("where", ["dataset", "multiscale"])
def test_transformations_that_are_not_a_list_give_no_sizes(tmp_path, where):
    malformed = {"type": "scale", "scale": [2.0, 0.5, 0.25]}
    kwargs = (
        {"dataset_transforms": malformed}
        if where == "dataset"
        else {"scale": [2.0, 0.5, 0.25], "multiscale_transforms": malformed}
    )
    path = write_ngff(tmp_path / "a.ome.zarr", np.zeros((3, 8, 8), np.uint8), "zyx", **kwargs)
    assert open_ngff(path).physical_sizes == PhysicalSizes()


def test_sizes_come_from_level_0_of_a_pyramid(tmp_path):
    # The brick's own copy has three levels with scales 0.5, 1 and 2 along X and Y.
    source = tmp_path / "exp/a.ome.tif"
    source.parent.mkdir(parents=True)
    tifffile.imwrite(
        source,
        np.zeros((2, 64, 64), np.uint16),
        ome=True,
        photometric="minisblack",
        metadata={"axes": "ZYX", "PhysicalSizeX": 0.5, "PhysicalSizeY": 0.5, "PhysicalSizeZ": 2},
    )
    result = run_experiment(
        tmp_path / "exp",
        prefill_mapping("ZYX"),
        convert_tiffs=True,
        conversion=ConversionSettings(chunk_size=16, pyramid_levels=2),
    )
    copy = result.output_folder / ome_zarr_relative_path("a.ome.tif")
    image = open_ngff(copy)
    assert image.dataset_path == "0" and image.shape == (2, 64, 64)
    sizes = image.physical_sizes
    assert (sizes.x, sizes.y, sizes.z) == (0.5, 0.5, 2.0)
