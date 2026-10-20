import itertools
from dataclasses import replace

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    DEFAULT_MEMORY_LIMIT,
    AxisMapping,
    PlaneMemoryError,
    PlaneReadError,
    Severity,
    check_planes,
    compute_plane_histogram,
    inspect_images,
    inspect_tiff,
    iter_planes,
    plane_layout,
    prefill_mapping,
    read_plane,
    read_sample_plane,
    review_axes,
)
from morphobricks_preprocessing import planes as planes_module
from morphobricks_preprocessing.histogram import estimate_histogram_memory
from tiff_tree import write_data, write_file

DTYPES = ["int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64"]
DTYPES += ["float32", "float64"]


def random_data(shape, dtype, seed=0):
    rng = np.random.default_rng(seed)
    dt = np.dtype(dtype)
    native = dt.newbyteorder("=")
    if dt.kind == "f":
        return rng.normal(100, 30, shape).astype(dt)
    info = np.iinfo(native)
    data = rng.integers(info.min, info.max, shape, dtype=native, endpoint=True)
    data.flat[0], data.flat[-1] = info.min, info.max
    return data.astype(dt)


@pytest.fixture
def page_reads(monkeypatch):
    """Count decoded pages and fail if a whole series or file is decoded at once."""
    reads = []
    original = planes_module._read_page

    def counting(series, index, layout, path):
        reads.append(index)
        return original(series, index, layout, path)

    def fail(*args, **kwargs):
        raise AssertionError("a whole series was decoded")

    monkeypatch.setattr(planes_module, "_read_page", counting)
    monkeypatch.setattr(tifffile.TiffFile, "asarray", fail)
    monkeypatch.setattr(tifffile.TiffPageSeries, "asarray", fail)
    return reads


def expected_planes(data, roles):
    """Reference: every (coordinates, plane) of ``data`` by NumPy indexing."""
    y, x = roles.index("Y"), roles.index("X")
    others = [d for d in range(data.ndim) if d not in (y, x)]
    result = []
    for index in itertools.product(*(range(data.shape[d]) for d in others)):
        position = dict(zip(others, index, strict=True))
        selection = tuple(position.get(d, slice(None)) for d in range(data.ndim))
        plane = data[selection]
        if x < y:
            plane = plane.T
        coords = {
            role.lower(): (position[roles.index(role)] if role in roles else None) for role in "ZCT"
        }
        result.append((coords, plane))
    return result


# Axis layouts (plan §3) ---------------------------------------------------------------


LAYOUTS = [
    pytest.param((6, 7), "YX", {}, None, id="2d"),
    pytest.param((3, 6, 7), "ZYX", {}, None, id="zstack"),
    pytest.param((2, 6, 7), "CYX", {}, None, id="channels"),
    pytest.param((4, 6, 7), "TYX", {}, None, id="timelapse"),
    pytest.param((2, 3, 6, 7), "ZCYX", {}, None, id="zcyx"),
    pytest.param((3, 2, 6, 7), "CZYX", {}, None, id="czyx"),
    pytest.param((2, 3, 2, 6, 7), "TZCYX", {"imagej": True}, None, id="imagej-hyperstack"),
    pytest.param((2, 3, 2, 6, 7), "TZCYX", {"ome": True}, None, id="ome"),
    pytest.param((2, 3, 6, 7), "TCYX", {"compression": "zlib"}, None, id="compressed"),
    pytest.param((3, 40, 36), "ZYX", {"tile": (16, 16)}, None, id="tiled"),
    pytest.param((4, 32, 32), "ZYX", {"tile": (16, 16, 16), "volumetric": True}, None, id="volume"),
    pytest.param((5, 6, 7), None, {}, "TYX", id="plain-pages-as-time"),
    pytest.param((5, 6, 7), None, {}, "ZYX", id="plain-pages-as-depth"),
    pytest.param((3, 6, 7), "ZXY", {}, None, id="x-before-y"),
    pytest.param((1, 3, 6, 7), "QZYX", {}, None, id="unassigned-singleton"),
]


@pytest.mark.parametrize(("shape", "axes", "kwargs", "roles"), LAYOUTS)
def test_planes_match_the_source(tmp_path, page_reads, shape, axes, kwargs, roles):
    data = random_data(shape, "uint16")
    path = write_data(tmp_path / "a.tif", data, axes=axes, **kwargs)
    info = inspect_tiff(path)
    mapping = prefill_mapping(info.axes)
    if roles is not None:
        mapping = AxisMapping(info.axes, roles)
    assert review_axes([info], mapping).ok

    planes = list(iter_planes(path, mapping))
    expected = expected_planes(data, mapping.roles)
    assert len(planes) == len(expected) == plane_layout(path, mapping).plane_count
    for plane, (coords, pixels) in zip(planes, expected, strict=True):
        assert plane.coordinates == coords
        assert plane.pixels.shape == pixels.shape
        assert plane.pixels.flags.c_contiguous
        np.testing.assert_array_equal(plane.pixels, pixels)


@pytest.mark.parametrize(("shape", "axes", "kwargs", "roles"), LAYOUTS)
def test_every_page_is_decoded_once(tmp_path, page_reads, shape, axes, kwargs, roles):
    path = write_data(tmp_path / "a.tif", random_data(shape, "uint8"), axes=axes, **kwargs)
    info = inspect_tiff(path)
    mapping = AxisMapping(info.axes, roles) if roles else prefill_mapping(info.axes)
    list(iter_planes(path, mapping))
    with tifffile.TiffFile(path) as tif:
        page_count = len(tif.series[0].pages)
    assert page_reads == list(range(page_count))


def test_coordinates_are_zero_based_and_blank_when_absent(tmp_path):
    path = write_data(tmp_path / "a.tif", random_data((2, 3, 6, 7), "uint8"), axes="TCYX")
    coords = [p.coordinates for p in iter_planes(path, prefill_mapping("TCYX"))]
    assert coords[0] == {"z": None, "c": 0, "t": 0}
    assert coords[-1] == {"z": None, "c": 2, "t": 1}
    assert {(c["t"], c["c"]) for c in coords} == set(itertools.product(range(2), range(3)))


def test_2d_image_has_one_plane_without_coordinates(tmp_path):
    data = random_data((6, 7), "uint16")
    path = write_data(tmp_path / "a.tif", data)
    (plane,) = iter_planes(path, prefill_mapping("YX"))
    assert plane.coordinates == {"z": None, "c": None, "t": None}
    np.testing.assert_array_equal(plane.pixels, data)


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"photometric": "rgb"}, id="contig"),
        pytest.param({"photometric": "rgb", "planarconfig": "separate"}, id="planar"),
    ],
)
def test_rgb_samples_are_read_as_channels(tmp_path, page_reads, kwargs):
    data = random_data((2, 6, 7, 3), "uint8")
    if kwargs.get("planarconfig") == "separate":
        data = np.ascontiguousarray(np.moveaxis(data, -1, 1))
        axes = "ZSYX"
    else:
        axes = "ZYXS"
    path = write_data(tmp_path / "a.tif", data, axes=axes, **kwargs)
    info = inspect_tiff(path)
    assert info.axes == axes
    mapping = prefill_mapping(info.axes)
    assert mapping.roles[axes.index("S")] == "C"

    planes = list(iter_planes(path, mapping))
    assert [(p.z, p.c) for p in planes] == [(z, c) for z in range(2) for c in range(3)]
    for plane, (_, pixels) in zip(planes, expected_planes(data, mapping.roles), strict=True):
        np.testing.assert_array_equal(plane.pixels, pixels)
    assert page_reads == [0, 1]  # each RGB page decoded once for its three channels


def test_confirmed_missing_axis_gets_coordinate_zero(tmp_path):
    full = write_data(tmp_path / "a.tif", random_data((2, 3, 6, 7), "uint8"), axes="ZCYX")
    data = random_data((2, 6, 7), "uint8", seed=1)
    part = write_data(tmp_path / "b.tif", data, axes="ZYX")
    report = inspect_images(tmp_path, ["a.tif", "b.tif"])
    review = review_axes(report, prefill_mapping("ZCYX"), missing_as_singleton=True)
    assert review.ok

    planes = list(iter_planes(part, review.file_mappings["b.tif"]))
    assert [p.coordinates for p in planes] == [{"z": z, "c": 0, "t": None} for z in range(2)]
    np.testing.assert_array_equal(planes[1].pixels, data[1])
    assert len(list(iter_planes(full, review.file_mappings["a.tif"]))) == 6


def test_pyramidal_file_is_read_at_full_resolution(tmp_path):
    data = random_data((2, 32, 32), "uint16")
    path = tmp_path / "pyramid.ome.tif"
    with tifffile.TiffWriter(path, ome=True) as tif:
        tif.write(data, subifds=1, metadata={"axes": "ZYX"}, photometric="minisblack")
        tif.write(data[:, ::2, ::2], subfiletype=1, photometric="minisblack")
    info = inspect_tiff(path)
    assert info.series_count == 1 and info.shape == (2, 32, 32)
    assert inspect_images(tmp_path, ["pyramid.ome.tif"]).issues == ()
    planes = list(iter_planes(path, prefill_mapping(info.axes)))
    assert len(planes) == 2
    np.testing.assert_array_equal(planes[1].pixels, data[1])


# Pixel types --------------------------------------------------------------------------


@pytest.mark.parametrize("dtype", DTYPES)
def test_every_supported_dtype_is_read_unchanged(tmp_path, dtype):
    data = random_data((3, 6, 7), dtype)
    path = write_data(tmp_path / "a.tif", data, axes="ZYX")
    planes = list(iter_planes(path, prefill_mapping("ZYX")))
    assert [p.z for p in planes] == [0, 1, 2]
    for z, plane in enumerate(planes):
        assert plane.pixels.dtype == np.dtype(dtype)
        np.testing.assert_array_equal(plane.pixels, data[z])
        histogram = compute_plane_histogram(plane.pixels)
        assert histogram.valid_pixel_count == 42


@pytest.mark.parametrize("dtype", [">u2", ">i4", ">f8"])
def test_big_endian_planes_are_native(tmp_path, dtype):
    data = random_data((2, 6, 7), dtype)
    path = write_data(tmp_path / "a.tif", data, axes="ZYX", byteorder=">")
    plane = next(iter_planes(path, prefill_mapping("ZYX")))
    assert plane.pixels.dtype == np.dtype(dtype).newbyteorder("=")
    np.testing.assert_array_equal(plane.pixels, data[0])


def test_non_finite_float_values_are_kept(tmp_path):
    data = random_data((6, 7), "float32")
    data[0, :3] = [np.nan, np.inf, -np.inf]
    path = write_data(tmp_path / "a.tif", data)
    (plane,) = iter_planes(path, prefill_mapping("YX"))
    np.testing.assert_array_equal(plane.pixels, data)
    assert compute_plane_histogram(plane.pixels).non_finite_pixel_count == 3


# Single planes and the preview --------------------------------------------------------


def test_read_plane_decodes_only_its_page(tmp_path, page_reads):
    data = random_data((2, 3, 6, 7), "uint16")
    path = write_data(tmp_path / "a.tif", data, axes="TZYX")
    plane = read_plane(path, prefill_mapping("TZYX"), t=1, z=2)
    assert (plane.z, plane.c, plane.t) == (2, None, 1)
    np.testing.assert_array_equal(plane.pixels, data[1, 2])
    assert page_reads == [5]


def test_read_plane_defaults_to_the_first_plane(tmp_path):
    data = random_data((2, 3, 6, 7), "uint16")
    path = write_data(tmp_path / "a.tif", data, axes="TZYX")
    plane = read_plane(path, prefill_mapping("TZYX"))
    assert (plane.z, plane.c, plane.t) == (0, None, 0)
    np.testing.assert_array_equal(plane.pixels, data[0, 0])


def test_read_plane_rejects_bad_coordinates(tmp_path):
    path = write_data(tmp_path / "a.tif", random_data((3, 6, 7), "uint8"), axes="ZYX")
    mapping = prefill_mapping("ZYX")
    with pytest.raises(IndexError, match="outside"):
        read_plane(path, mapping, z=3)
    with pytest.raises(IndexError, match="outside"):
        read_plane(path, mapping, z=-1)
    with pytest.raises(IndexError, match="no C axis"):
        read_plane(path, mapping, c=0)


def test_read_plane_accepts_zero_for_a_confirmed_singleton(tmp_path):
    path = write_data(tmp_path / "a.tif", random_data((3, 6, 7), "uint8"), axes="ZYX")
    mapping = AxisMapping("ZYX", "ZYX", singleton_roles=("C",))
    assert read_plane(path, mapping, c=0).c == 0
    with pytest.raises(IndexError):
        read_plane(path, mapping, c=1)


def test_sample_plane_is_the_middle_slice(tmp_path, page_reads):
    data = random_data((5, 2, 6, 7), "uint16")
    path = write_data(tmp_path / "a.tif", data, axes="ZCYX")
    plane = read_sample_plane(path, prefill_mapping("ZCYX"))
    assert (plane.z, plane.c, plane.t) == (2, 0, None)
    np.testing.assert_array_equal(plane.pixels, data[2, 0])
    assert len(page_reads) == 1


def test_sample_plane_of_a_2d_image(tmp_path, page_reads):
    data = random_data((6, 7), "float32")
    path = write_data(tmp_path / "a.tif", data)
    plane = read_sample_plane(path, prefill_mapping("YX"))
    np.testing.assert_array_equal(plane.pixels, data)


# Memory limit (plan §5) ---------------------------------------------------------------


def test_default_memory_limit_is_one_gib():
    assert DEFAULT_MEMORY_LIMIT == 2**30


def test_memory_estimate_covers_page_plane_and_histogram(tmp_path):
    data = random_data((2, 60, 70, 3), "uint8")
    path = write_data(tmp_path / "a.tif", data, axes="ZYXS", photometric="rgb")
    layout = plane_layout(path, prefill_mapping("ZYXS"))
    assert layout.page_shape == (60, 70, 3)
    assert layout.page_nbytes == 60 * 70 * 3
    histogram = estimate_histogram_memory(60 * 70, np.dtype("uint8"))
    assert layout.memory_estimate == (
        layout.page_nbytes + layout.encoded_nbytes + 60 * 70 + histogram
    )


def test_memory_estimate_counts_the_whole_page_of_a_volume(tmp_path):
    data = random_data((4, 32, 32), "uint16")
    path = write_data(tmp_path / "a.tif", data, tile=(16, 16, 16), volumetric=True)
    layout = plane_layout(path, prefill_mapping("ZYX"))
    assert layout.page_shape == (4, 32, 32) and layout.page_nbytes == data.nbytes


def test_histogram_memory_estimate_grows_with_the_plane():
    small = estimate_histogram_memory(1_000, np.dtype("float32"))
    large = estimate_histogram_memory(1_000_000, np.dtype("float32"))
    assert large > small > 1_000 * 8
    # Wide integers allow for the per-value count table of up to 2**24 values.
    assert estimate_histogram_memory(10, np.dtype("int32")) > 2**24 * 8
    assert estimate_histogram_memory(10, np.dtype("uint8")) < 2**20


def test_plane_over_the_limit_is_read_in_blocks_not_whole(tmp_path, page_reads):
    path = write_data(tmp_path / "a.tif", random_data((2, 64, 64), "float32"), axes="ZYX")
    mapping = prefill_mapping("ZYX")
    estimate = plane_layout(path, mapping).memory_estimate
    planes = list(iter_planes(path, mapping, memory_limit=estimate - 1))
    assert len(planes) == 2 and all(plane.in_blocks for plane in planes)
    with pytest.raises(PlaneMemoryError, match="only be read in blocks") as excinfo:
        read_plane(path, mapping, memory_limit=estimate - 1)
    assert "a.tif" in str(excinfo.value)
    assert page_reads == []
    assert not any(p.in_blocks for p in iter_planes(path, mapping, memory_limit=estimate))


def test_plane_that_cannot_be_split_is_refused_before_reading(tmp_path, page_reads):
    # Compressed in one strip: the whole plane has to be decoded at once.
    data = random_data((2, 64, 64), "float32")
    path = write_data(tmp_path / "a.tif", data, axes="ZYX", compression="zlib", rowsperstrip=64)
    mapping = prefill_mapping("ZYX")
    layout = plane_layout(path, mapping, memory_limit=1)
    assert layout.block_memory_estimate is None and not layout.fits
    estimate = layout.memory_estimate
    with pytest.raises(PlaneMemoryError, match="limit") as excinfo:
        next(iter_planes(path, mapping, memory_limit=estimate - 1))
    assert "a.tif" in str(excinfo.value) and "even in blocks" in str(excinfo.value)
    with pytest.raises(PlaneMemoryError):
        read_plane(path, mapping, memory_limit=estimate - 1, preview=True)
    with pytest.raises(PlaneMemoryError):
        read_sample_plane(path, mapping, memory_limit=estimate - 1)
    assert page_reads == []
    assert len(list(iter_planes(path, mapping, memory_limit=estimate))) == 2


def test_memory_estimate_depends_on_bin_count(tmp_path):
    path = write_data(tmp_path / "a.tif", random_data((8, 8), "float64"))
    mapping = prefill_mapping("YX")
    small = plane_layout(path, mapping, bin_count=2).memory_estimate
    large = plane_layout(path, mapping, bin_count=65_536).memory_estimate
    assert large - small == 4 * 8 * (65_536 - 2)


def test_check_planes_reports_planes_over_the_limit(tmp_path):
    big = random_data((2, 64, 64), "uint16")
    write_data(tmp_path / "c/big.tif", big, axes="ZYX", compression="zlib", rowsperstrip=64)
    write_data(tmp_path / "c/small.tif", random_data((2, 8, 8), "uint16"), axes="ZYX")
    report = inspect_images(tmp_path, ["c/big.tif", "c/small.tif"])
    review = review_axes(report, prefill_mapping("ZYX"))
    small = plane_layout(tmp_path / "c/small.tif", prefill_mapping("ZYX")).memory_estimate

    assert check_planes(tmp_path, review) == []
    (found,) = check_planes(tmp_path, review, memory_limit=small)
    assert found.code == "plane_over_memory_limit"
    assert found.severity is Severity.ERROR
    assert found.paths == ("c/big.tif",)
    assert "MiB" in found.message and "not even in blocks" in found.message
    assert "memory limit" in found.action and "tiled" in found.action
    # The per-file mappings work as well as the review.
    assert check_planes(tmp_path, dict(review.file_mappings), memory_limit=small) == [found]


def test_check_planes_reports_planes_read_in_blocks(tmp_path, monkeypatch):
    write_data(tmp_path / "c/big.tif", random_data((2, 64, 64), "uint16"), axes="ZYX")
    write_data(tmp_path / "c/small.tif", random_data((2, 8, 8), "uint16"), axes="ZYX")
    review = review_axes(
        inspect_images(tmp_path, ["c/big.tif", "c/small.tif"]), prefill_mapping("ZYX")
    )
    small = plane_layout(tmp_path / "c/small.tif", prefill_mapping("ZYX")).memory_estimate
    big = plane_layout(tmp_path / "c/big.tif", prefill_mapping("ZYX")).memory_estimate

    def fail(*args, **kwargs):
        raise AssertionError("pixel data was read")

    for cls in (tifffile.TiffFile, tifffile.TiffPage, tifffile.TiffFrame, tifffile.TiffPageSeries):
        monkeypatch.setattr(cls, "asarray", fail)
    monkeypatch.setattr(planes_module, "_read_segment", fail)
    (found,) = check_planes(tmp_path, review, memory_limit=small)
    assert found.code == "planes_read_in_blocks"
    assert found.severity is Severity.WARNING
    assert found.paths == ("c/big.tif",)
    assert "exactly the same" in found.message
    assert planes_module.format_bytes(big) in found.action


# Planes in blocks (plan §5) -----------------------------------------------------------


@pytest.fixture
def segment_reads(monkeypatch):
    """Count strips and tiles read, and fail if a whole page or series is decoded."""
    reads = []
    original = planes_module._read_segment

    def counting(page, index, *args):
        reads.append(index)
        return original(page, index, *args)

    def fail(*args, **kwargs):
        raise AssertionError("a whole page or series was decoded")

    monkeypatch.setattr(planes_module, "_read_segment", counting)
    for cls in (tifffile.TiffFile, tifffile.TiffPage, tifffile.TiffFrame, tifffile.TiffPageSeries):
        monkeypatch.setattr(cls, "asarray", fail)
    return reads


def smallest_limit(path, mapping, bin_count=None):
    """The memory limit that gives the smallest blocks a file can be read in."""
    layout = plane_layout(path, mapping, memory_limit=1, bin_count=bin_count)
    assert layout.block_memory_estimate is not None, "the planes cannot be split"
    return layout.block_memory_estimate


def assert_blocks_tile_the_plane(plane, expected, block_shape):
    """The blocks hold every pixel of ``expected`` exactly once, at the right place."""
    covered = np.zeros(expected.shape, dtype=int)
    for block in plane.blocks():
        rows, cols = block.pixels.shape
        assert rows <= block_shape[0] and cols <= block_shape[1]
        assert block.pixels.dtype == expected.dtype
        where = (slice(block.y, block.y + rows), slice(block.x, block.x + cols))
        np.testing.assert_array_equal(block.pixels, expected[where])
        covered[where] += 1
    assert (covered == 1).all()


BLOCK_LAYOUTS = [
    pytest.param((3, 50, 70), "ZYX", {}, None, id="uncompressed"),
    pytest.param((3, 50, 70), "ZYX", {"compression": "zlib", "rowsperstrip": 7}, None, id="strips"),
    pytest.param((2, 50, 70), "ZYX", {"compression": "zlib", "tile": (16, 32)}, None, id="tiles"),
    pytest.param((2, 50, 70), "ZYX", {"tile": (16, 32)}, None, id="uncompressed-tiles"),
    pytest.param((2, 3, 40, 30), "ZCYX", {"compression": "zlib", "rowsperstrip": 4}, None, id="zc"),
    pytest.param((2, 3, 2, 40, 30), "TZCYX", {"ome": True}, None, id="ome"),
    pytest.param((2, 3, 2, 40, 30), "TZCYX", {"imagej": True}, None, id="imagej"),
    pytest.param((50, 70, 3), "YXS", {"photometric": "rgb"}, None, id="rgb"),
    pytest.param(
        (50, 70, 3),
        "YXS",
        {"photometric": "rgb", "compression": "zlib", "rowsperstrip": 5},
        None,
        id="rgb-strips",
    ),
    pytest.param(
        (3, 50, 70),
        "SYX",
        {
            "photometric": "rgb",
            "planarconfig": "separate",
            "compression": "zlib",
            "rowsperstrip": 5,
        },
        None,
        id="rgb-planar",
    ),
    pytest.param(
        (5, 48, 64),
        "ZYX",
        {"tile": (2, 16, 16), "volumetric": True, "compression": "zlib"},
        None,
        id="volume-tiles",
    ),
    pytest.param((5, 48, 64), "ZYX", {"tile": (2, 16, 16), "volumetric": True}, None, id="volume"),
    pytest.param((3, 50, 70), "ZXY", {}, None, id="x-before-y"),
    pytest.param(
        (3, 50, 70), "ZXY", {"compression": "zlib", "tile": (16, 16)}, None, id="x-y-tiles"
    ),
    pytest.param((4, 40, 30), None, {}, "TYX", id="plain-pages-as-time"),
]


@pytest.mark.parametrize(("shape", "axes", "kwargs", "roles"), BLOCK_LAYOUTS)
def test_planes_in_blocks_match_the_source(tmp_path, segment_reads, shape, axes, kwargs, roles):
    dtype = "uint8" if "photometric" in kwargs else "uint16"
    data = random_data(shape, dtype)
    path = write_data(tmp_path / "a.tif", data, axes=axes, **kwargs)
    info = inspect_tiff(path)
    mapping = AxisMapping(info.axes, roles) if roles else prefill_mapping(info.axes)
    limit = smallest_limit(path, mapping)
    layout = plane_layout(path, mapping, memory_limit=limit)
    assert layout.in_blocks and layout.block_memory_estimate <= limit

    expected = expected_planes(data, mapping.roles)
    planes = iter_planes(path, mapping, memory_limit=limit)
    for plane, (coords, pixels) in zip(planes, expected, strict=True):
        assert plane.in_blocks and plane.pixels is None
        assert plane.coordinates == coords
        assert plane.shape == pixels.shape and plane.dtype == pixels.dtype
        assert_blocks_tile_the_plane(plane, pixels, layout.block_shape)
        whole = compute_plane_histogram(pixels)
        in_blocks = plane.histogram()
        assert (in_blocks.binning_min, in_blocks.binning_max) == (whole.pixel_min, whole.pixel_max)
        np.testing.assert_array_equal(in_blocks.counts, whole.counts)
    assert segment_reads  # read piece by piece, never as whole pages


@pytest.mark.parametrize("dtype", DTYPES + [">u2", ">i4", ">f4", ">f8"])
@pytest.mark.parametrize("kwargs", [{}, {"compression": "zlib", "rowsperstrip": 3}])
def test_every_dtype_in_blocks_gives_the_same_histograms(tmp_path, segment_reads, dtype, kwargs):
    data = random_data((2, 20, 24), dtype)
    if data.dtype.kind == "f":
        data[0, 1, :3] = [np.nan, np.inf, -np.inf]
        data[1] = np.nan  # a plane without finite pixels
    path = write_data(tmp_path / "a.tif", data, axes="ZYX", **kwargs)
    mapping = prefill_mapping("ZYX")
    for bins in (None, 5):
        limit = smallest_limit(path, mapping, bin_count=bins)
        planes = iter_planes(path, mapping, memory_limit=limit, bin_count=bins)
        for plane, source in zip(planes, data, strict=True):
            assert plane.in_blocks
            expected = compute_plane_histogram(source.astype(source.dtype.newbyteorder("=")), bins)
            hist = plane.histogram(bins)
            assert hist.dtype == expected.dtype
            assert (hist.pixel_min, hist.pixel_max) == (expected.pixel_min, expected.pixel_max)
            assert (hist.total_pixel_count, hist.valid_pixel_count) == (
                expected.total_pixel_count,
                expected.valid_pixel_count,
            )
            np.testing.assert_array_equal(hist.counts, expected.counts)


def test_constant_plane_in_blocks(tmp_path):
    data = np.full((2, 30, 20), 7, dtype=np.uint32)
    path = write_data(tmp_path / "a.tif", data, axes="ZYX", compression="zlib", rowsperstrip=4)
    mapping = prefill_mapping("ZYX")
    for plane in iter_planes(path, mapping, memory_limit=smallest_limit(path, mapping)):
        hist = plane.histogram(16)
        assert (hist.pixel_min, hist.pixel_max) == (7, 7) and hist.counts[0] == 600


@pytest.mark.parametrize(("dtype", "passes"), [("uint16", 1), ("float32", 2)])
def test_each_strip_is_read_once_per_pass(tmp_path, segment_reads, dtype, passes):
    data = random_data((2, 21, 16), dtype)
    path = write_data(tmp_path / "a.tif", data, axes="ZYX", compression="zlib", rowsperstrip=4)
    mapping = prefill_mapping("ZYX")
    for plane in iter_planes(path, mapping, memory_limit=smallest_limit(path, mapping)):
        plane.histogram()
    strips = 6  # ceil(21 / 4) per plane, numbered within each page
    assert sorted(segment_reads) == sorted(list(range(strips)) * 2 * passes)


def test_larger_limits_give_larger_blocks_within_the_limit(tmp_path):
    data = random_data((1, 64, 48), "uint16")
    path = write_data(tmp_path / "a.tif", data, axes="ZYX", compression="zlib", rowsperstrip=2)
    mapping = prefill_mapping("ZYX")
    smallest = smallest_limit(path, mapping)
    whole = plane_layout(path, mapping).memory_estimate
    sizes = []
    for limit in np.linspace(smallest, whole - 1, 6).astype(int):
        layout = plane_layout(path, mapping, memory_limit=int(limit))
        assert layout.in_blocks and layout.block_memory_estimate <= limit
        assert layout.block_shape[0] % 2 == 0 or layout.block_shape[0] == 64  # whole strips
        sizes.append(layout.block_shape[0] * layout.block_shape[1])
    assert sizes == sorted(sizes) and sizes[-1] > sizes[0]
    assert not plane_layout(path, mapping, memory_limit=whole).in_blocks


def test_compressed_tiles_are_grouped_across_and_down(tmp_path):
    data = random_data((1, 64, 64), "uint8")
    path = write_data(tmp_path / "a.tif", data, axes="ZYX", compression="zlib", tile=(16, 16))
    mapping = prefill_mapping("ZYX")
    smallest = plane_layout(path, mapping, memory_limit=smallest_limit(path, mapping))
    assert smallest.block_shape == (16, 16)  # a single tile
    larger = plane_layout(path, mapping, memory_limit=smallest.block_memory_estimate + 50_000)
    assert larger.block_shape[0] % 16 == 0 and larger.block_shape[1] % 16 == 0
    assert larger.block_shape[0] * larger.block_shape[1] > 16 * 16


def test_damaged_strip_raises_plane_read_error(tmp_path):
    data = random_data((1, 32, 16), "uint16")
    path = write_data(tmp_path / "a.tif", data, axes="ZYX", compression="zlib", rowsperstrip=4)
    with tifffile.TiffFile(path) as tif:
        offset = tif.pages[0].dataoffsets[3]
    raw = bytearray(path.read_bytes())
    raw[offset : offset + 8] = b"\xff" * 8
    path.write_bytes(bytes(raw))
    mapping = prefill_mapping("ZYX")
    plane = next(iter_planes(path, mapping, memory_limit=smallest_limit(path, mapping)))
    with pytest.raises(PlaneReadError, match="cannot be read"):
        plane.histogram()


def test_preview_of_a_plane_in_blocks_is_reduced(tmp_path):
    data = random_data((3, 64, 50), "uint16")
    path = write_data(tmp_path / "a.tif", data, axes="ZYX", compression="zlib", rowsperstrip=8)
    mapping = prefill_mapping("ZYX")
    limit = plane_layout(path, mapping).memory_estimate - 1
    with pytest.raises(PlaneMemoryError, match="in blocks"):
        read_plane(path, mapping, z=2, memory_limit=limit)
    plane = read_plane(path, mapping, z=2, memory_limit=limit, preview=True)
    assert plane.step >= 2 and plane.z == 2 and plane.shape == (64, 50)
    np.testing.assert_array_equal(plane.pixels, data[2, :: plane.step, :: plane.step])
    with pytest.raises(ValueError, match="reduced"):
        next(plane.blocks())
    sample = read_sample_plane(path, mapping, memory_limit=limit)
    assert sample.z == 1 and sample.step == plane.step
    np.testing.assert_array_equal(sample.pixels, data[1, :: sample.step, :: sample.step])
    # A plane that fits is shown whole, as before.
    whole = read_plane(path, mapping, z=2, memory_limit=limit + 1, preview=True)
    assert whole.step == 1 and not whole.in_blocks
    np.testing.assert_array_equal(whole.pixels, data[2])


def test_preview_step_keeps_previews_small(tmp_path):
    layout = planes_module.PlaneLayout(
        shape=(20_000, 30_000),
        dtype=np.dtype("uint16"),
        plane_shape=(20_000, 30_000),
        plane_count=1,
        page_shape=(20_000, 30_000),
        page_nbytes=0,
        encoded_nbytes=0,
        memory_estimate=2**34,
    )
    step = planes_module._preview_step(layout)
    assert max(-(-20_000 // step), -(-30_000 // step)) <= planes_module.PREVIEW_SIDE
    small = planes_module._preview_step(replace(layout, memory_limit=2**24))
    pixels = -(-20_000 // small) * -(-30_000 // small)
    assert small > step and pixels * (2 + 16) <= 2**24 // 4


def test_check_planes_reads_no_pixels(tmp_path, monkeypatch):
    write_data(tmp_path / "a.tif", random_data((2, 8, 8), "uint16"), axes="ZYX")
    review = review_axes(inspect_images(tmp_path, ["a.tif"]), prefill_mapping("ZYX"))

    def fail(*args, **kwargs):
        raise AssertionError("pixel data was read")

    for cls in (tifffile.TiffFile, tifffile.TiffPage, tifffile.TiffFrame, tifffile.TiffPageSeries):
        monkeypatch.setattr(cls, "asarray", fail)
    assert check_planes(tmp_path, review) == []


def test_check_planes_reports_unreadable_layouts(tmp_path):
    write_data(tmp_path / "a.tif", random_data((2, 8, 8), "uint16"), axes="ZYX")
    write_file(tmp_path / "bad.tif", b"garbage")
    mappings = {"a.tif": prefill_mapping("CYX"), "bad.tif": prefill_mapping("ZYX")}
    (found,) = check_planes(tmp_path, mappings)
    assert found.code == "unsupported_layout"
    assert found.paths == ("a.tif", "bad.tif")
    assert "Traceback" not in str(found)


# Mapping does not fit the file --------------------------------------------------------


def test_mapping_for_other_axes_is_refused(tmp_path):
    path = write_data(tmp_path / "a.tif", random_data((2, 8, 8), "uint8"), axes="ZYX")
    with pytest.raises(PlaneReadError, match="'CYX'"):
        next(iter_planes(path, prefill_mapping("CYX")))


def test_unassigned_non_singleton_axis_is_refused(tmp_path):
    path = write_data(tmp_path / "a.tif", random_data((3, 8, 8), "uint8"), axes="QYX")
    with pytest.raises(PlaneReadError, match="no role"):
        next(iter_planes(path, prefill_mapping("QYX")))


def test_mapping_without_x_is_refused(tmp_path):
    path = write_data(tmp_path / "a.tif", random_data((3, 8, 8), "uint8"), axes="ZYX")
    with pytest.raises(PlaneReadError, match="one X and one Y"):
        plane_layout(path, AxisMapping("ZYX", "ZY-"))


def test_duplicate_roles_are_refused(tmp_path):
    path = write_data(tmp_path / "a.tif", random_data((2, 3, 8, 8), "uint8"), axes="ZCYX")
    with pytest.raises(PlaneReadError, match="several axes"):
        plane_layout(path, AxisMapping("ZCYX", "ZZYX"))


@pytest.mark.parametrize(
    ("n", "text"),
    [(512, "512 bytes"), (1536, "1.5 KiB"), (3 * 2**20, "3.0 MiB"), (2**30, "1.0 GiB")],
)
def test_format_bytes(n, text):
    assert planes_module.format_bytes(n) == text


def test_planes_across_pages_are_refused(tmp_path):
    # Calling the page axis Y would make every plane span several TIFF pages.
    path = write_data(tmp_path / "a.tif", random_data((3, 8, 8), "uint8"), axes="ZYX")
    with pytest.raises(PlaneReadError, match="single TIFF pages"):
        plane_layout(path, AxisMapping("ZYX", "YZX"))


def test_unreadable_file_raises_plane_read_error(tmp_path):
    path = write_file(tmp_path / "bad.tif", b"garbage")
    with pytest.raises(PlaneReadError, match="cannot be opened"):
        next(iter_planes(path, prefill_mapping("YX")))
