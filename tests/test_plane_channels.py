"""Every channel of a plane in one read, its whole memory checked first (issue #344)."""

import json
import os
import subprocess
import sys
import tracemalloc

import numpy as np
import pytest
import tifffile

from described_tree import run_described
from morphobricks_preprocessing import plane_channels as channels_module
from morphobricks_preprocessing import prefill_mapping
from morphobricks_preprocessing.plane_binning import Binning
from morphobricks_preprocessing.plane_channels import read_xyc_plane, xyc_plane_memory
from morphobricks_preprocessing.planes import (
    PlaneMemoryError,
    _layout,
    read_plane,
)
from morphobricks_preprocessing.run_inputs import RunInputs
from tiff_tree import write_data
from zarr_tree import write_ngff

RNG = np.random.default_rng(344)


def _smooth(shape, dtype=np.uint8):
    """Compressible pixels: a gradient plus a little noise, in ``dtype``."""
    y, x = np.indices(shape[-2:])
    base = (y // 3 + x // 5).astype(np.int64)
    out = np.empty(shape, dtype=dtype)
    flat = out.reshape(-1, *shape[-2:])
    for i in range(flat.shape[0]):
        flat[i] = (base * (i + 1) + RNG.integers(0, 4, shape[-2:])) % np.iinfo(dtype).max
    return out


def write_rgb(path, data, **kwargs):
    """Write a YXS RGB page (samples interleaved, or ``planarconfig="separate"`` for SYX)."""
    return write_data(path, data, photometric="rgb", **kwargs)


def stacked(path, mapping, count, **coordinates):
    """What RunInputs.read_plane returned before: one read_plane per channel, then a stack."""
    has_c = mapping.index_of("C") is not None
    return np.stack(
        [
            read_plane(path, mapping, c=c if has_c else None, **coordinates).pixels
            for c in range(count)
        ]
    )


def count_decodes(monkeypatch):
    """Count TIFF page decodes (``TiffPage.asarray``) and OME-Zarr plane reads."""
    calls = {"tiff": 0, "zarr": 0}
    original = tifffile.TiffPage.asarray

    def asarray(self, *args, **kwargs):
        calls["tiff"] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(tifffile.TiffPage, "asarray", asarray)
    # TiffFrame.asarray decodes through TiffPage.asarray: each decode is counted once.
    zarr_read = channels_module._zarr_read

    def counted_zarr(*args, **kwargs):
        calls["zarr"] += 1
        return zarr_read(*args, **kwargs)

    monkeypatch.setattr(channels_module, "_zarr_read", counted_zarr)
    return calls


# Bitwise the per-channel reads --------------------------------------------------------------


def _interleaved(tmp_path):
    data = _smooth((3, 96, 160)).transpose(1, 2, 0).copy()
    path = write_rgb(tmp_path / "rgb.tif", data, tile=(32, 32), compression="zlib")
    return path, "YXS", 3, {}


def _interleaved_strips(tmp_path):
    data = _smooth((3, 70, 50)).transpose(1, 2, 0).copy()
    return write_rgb(tmp_path / "rgb.tif", data, rowsperstrip=8), "YXS", 3, {}


def _planar(tmp_path):
    data = _smooth((3, 64, 80), np.uint16)
    path = write_rgb(
        tmp_path / "p.tif", data, planarconfig="separate", tile=(32, 32), compression="zlib"
    )
    return path, "SYX", 3, {}


def _pages(tmp_path):
    data = _smooth((2, 4, 40, 56), np.uint16)
    return write_data(tmp_path / "zc.tif", data, "ZCYX"), "ZCYX", 4, {"z": 1}


def _big_endian(tmp_path):
    data = _smooth((3, 40, 24), np.uint16)
    return write_data(tmp_path / "be.tif", data, "CYX", byteorder=">"), "CYX", 3, {}


def _x_first(tmp_path):
    data = _smooth((2, 3, 30, 20), np.uint16)
    return (
        write_data(tmp_path / "x.tif", data, "TCXY", predictor=True, compression="zlib"),
        ("TCXY"),
        3,
        {"t": 1},
    )


def _no_channel_axis(tmp_path):
    return (
        write_data(tmp_path / "g.tif", _smooth((3, 20, 30), np.uint16), "TYX"),
        ("TYX"),
        1,
        {"t": 2},
    )


LAYOUTS = [
    _interleaved,
    _interleaved_strips,
    _planar,
    _pages,
    _big_endian,
    _x_first,
    _no_channel_axis,
]


@pytest.mark.parametrize("make", LAYOUTS, ids=lambda f: f.__name__.strip("_"))
def test_every_channel_is_bitwise_the_per_channel_read(tmp_path, make):
    path, axes, count, coordinates = make(tmp_path)
    mapping = prefill_mapping(axes)
    expected = stacked(path, mapping, count, **coordinates)
    got = read_xyc_plane(path, mapping, **coordinates)
    assert got.dtype == expected.dtype and got.dtype.isnative
    assert got.flags.c_contiguous
    assert got.shape == expected.shape
    assert got.tobytes() == expected.tobytes()


@pytest.mark.parametrize("chunks", [(1, 16, 16), (3, 16, 16)])
def test_ome_zarr_channels_are_bitwise_the_per_channel_read(tmp_path, chunks):
    data = _smooth((3, 40, 30), np.uint16)
    path = write_ngff(tmp_path / "a.zarr", data, axes="cyx", chunks=chunks)
    mapping = prefill_mapping("CYX")
    got = read_xyc_plane(path, mapping)
    assert got.tobytes() == stacked(path, mapping, 3).tobytes()


def test_fewer_channels_than_the_image_reads_the_first_ones(tmp_path):
    path, axes, _, _ = _pages(tmp_path)
    mapping = prefill_mapping(axes)
    got = read_xyc_plane(path, mapping, z=1, channel_count=2)
    assert got.tobytes() == stacked(path, mapping, 2, z=1).tobytes()
    with pytest.raises(IndexError, match="cannot read 5"):
        read_xyc_plane(path, mapping, channel_count=5)


# Decodes ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("make", "before", "after"),
    [(_interleaved, 3, 1), (_planar, 3, 1), (_pages, 4, 4)],
    ids=["interleaved", "planar", "page per channel"],
)
def test_a_page_holding_several_channels_is_decoded_once(
    tmp_path, monkeypatch, make, before, after
):
    path, axes, count, coordinates = make(tmp_path)
    mapping = prefill_mapping(axes)
    calls = count_decodes(monkeypatch)
    stacked(path, mapping, count, **coordinates)
    assert calls["tiff"] == before
    calls["tiff"] = 0
    read_xyc_plane(path, mapping, **coordinates)
    assert calls["tiff"] == after


# Memory: the estimate bounds the measured peak ----------------------------------------------


def _measured(path, mapping, **coordinates):
    """Return the tracemalloc peak of one read, opening the file included."""
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        start = tracemalloc.get_traced_memory()[0]
        read_xyc_plane(path, mapping, memory_limit=2**40, **coordinates)
        return tracemalloc.get_traced_memory()[1] - start
    finally:
        tracemalloc.stop()


def _large_interleaved(tmp_path, compression="zlib"):
    data = _smooth((3, 1024, 1536)).transpose(1, 2, 0).copy()
    path = write_rgb(tmp_path / "rgb.tif", data, tile=(256, 256), compression=compression)
    return path, prefill_mapping("YXS")


def _large_noisy(tmp_path):
    data = RNG.integers(0, 256, (1024, 1536, 3), dtype=np.uint8)
    path = write_rgb(tmp_path / "noisy.tif", data, tile=(256, 256), compression="zlib")
    return path, prefill_mapping("YXS")


def _large_planar(tmp_path):
    data = _smooth((3, 768, 1024), np.uint16)
    path = write_rgb(
        tmp_path / "p.tif",
        data,
        planarconfig="separate",
        rowsperstrip=64,
        compression="zlib",
        predictor=True,
    )
    return path, prefill_mapping("SYX")


def _large_uncompressed(tmp_path):
    data = _smooth((3, 1024, 1536)).transpose(1, 2, 0).copy()
    return write_rgb(tmp_path / "u.tif", data), prefill_mapping("YXS")


def _large_lzma(tmp_path):
    data = _smooth((3, 512, 768), np.uint16)
    path = write_rgb(
        tmp_path / "l.tif", data, planarconfig="separate", tile=(128, 128), compression="lzma"
    )
    return path, prefill_mapping("SYX")


def _large_zarr(tmp_path):
    data = _smooth((3, 1024, 1536), np.uint16)
    return write_ngff(tmp_path / "a.zarr", data, axes="cyx", chunks=(1, 256, 256)), (
        prefill_mapping("CYX")
    )


LARGE = [
    _large_interleaved,
    _large_noisy,
    _large_planar,
    _large_uncompressed,
    _large_lzma,
    _large_zarr,
]


@pytest.mark.parametrize("make", LARGE, ids=lambda f: f.__name__.removeprefix("_large_"))
def test_the_estimate_bounds_the_traced_peak(tmp_path, make):
    path, mapping = make(tmp_path)
    estimate = xyc_plane_memory(path, mapping)
    peak = _measured(path, mapping)
    assert peak <= estimate, (peak, estimate)
    if make is not _large_lzma:  # LZMA's dictionary is counted at its largest preset's
        # A bound, not a guess far above it.
        assert estimate < 2 * peak, (peak, estimate)


_RSS_SCRIPT = """
import json, sys
from morphobricks_preprocessing import prefill_mapping
from morphobricks_preprocessing.plane_channels import read_xyc_plane, xyc_plane_memory

def kib(key):
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith(key):
                return int(line.split()[1]) * 1024

path, axes = sys.argv[1], sys.argv[2]
mapping = prefill_mapping(axes)
estimate = xyc_plane_memory(path, mapping)
read_xyc_plane(path, mapping, memory_limit=2**40)  # warm-up: imports, codecs, threads
base = kib("VmRSS")
with open("/proc/self/clear_refs", "w") as f:
    f.write("5")  # resets VmHWM to the current RSS
read_xyc_plane(path, mapping, memory_limit=2**40)
print(json.dumps({"estimate": estimate, "peak": kib("VmHWM") - base}))
"""


def _clear_refs_works():
    try:
        with open("/proc/self/clear_refs", "w") as f:
            f.write("5")
    except OSError:
        return False
    return True


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or not _clear_refs_works(),
    reason="the peak RSS is reset through /proc/self/clear_refs (Linux only)",
)
@pytest.mark.parametrize("make", [_large_interleaved, _large_planar], ids=["interleaved", "planar"])
def test_the_estimate_bounds_the_peak_rss(tmp_path, make):
    path, mapping = make(tmp_path)
    axes = "".join(mapping.axes)
    done = subprocess.run(
        [sys.executable, "-c", _RSS_SCRIPT, str(path), axes],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONHASHSEED": "0"},
    )
    measured = json.loads(done.stdout)
    assert 0 < measured["peak"] <= measured["estimate"], measured


def _blank_then_noise(tmp_path):
    """TCYX deflate tiles: t=0 blank (tiny pages), t=1 noise (pages as large as the pixels)."""
    data = np.zeros((2, 3, 1536, 2048), np.uint8)
    data[1] = RNG.integers(0, 256, data.shape[1:], dtype=np.uint8)
    path = write_data(tmp_path / "tc.tif", data, "TCYX", tile=(256, 256), compression="zlib")
    return path, prefill_mapping("TCYX"), data


def test_the_bound_is_sized_from_the_pages_read_not_the_first(tmp_path, monkeypatch):
    # Review of #347 (R1): the bound took the first page's compressed sizes, so a plane of
    # pages compressing far worse than page 0 was accepted at a limit below its peak.
    path, mapping, data = _blank_then_noise(tmp_path)
    blank, noise = (xyc_plane_memory(path, mapping, t=t) for t in (0, 1))
    assert noise > blank
    assert _measured(path, mapping, t=1) <= noise
    assert _measured(path, mapping, t=0) <= blank
    calls = count_decodes(monkeypatch)
    with pytest.raises(PlaneMemoryError, match="all 3 channels"):
        read_xyc_plane(path, mapping, t=1, memory_limit=noise - 1)
    with pytest.raises(PlaneMemoryError):
        read_xyc_plane(path, mapping, t=1, memory_limit=blank)
    assert calls["tiff"] == 0
    np.testing.assert_array_equal(read_xyc_plane(path, mapping, t=1, memory_limit=noise), data[1])
    np.testing.assert_array_equal(read_xyc_plane(path, mapping, t=0, memory_limit=blank), data[0])
    assert calls["tiff"] == 6


def test_run_inputs_refuses_a_noisy_plane_at_the_first_pages_bound(tmp_path):
    path, mapping, data = _blank_then_noise(tmp_path / "exp/ctrl")
    result = run_described(tmp_path / "exp", mapping)
    inputs = RunInputs.open(result.output_folder)
    blank, noise = (xyc_plane_memory(path, mapping, t=t) for t in (0, 1))
    with pytest.raises(PlaneMemoryError):
        inputs.read_plane("ctrl/tc.tif", t=1, memory_limit=blank)
    got = inputs.read_plane("ctrl/tc.tif", t=1, memory_limit=noise)
    assert got.tobytes() == data[1].tobytes()


# Refusal before any pixel is read -----------------------------------------------------------


@pytest.fixture
def rgb_run(tmp_path):
    data = _smooth((3, 1024, 1536)).transpose(1, 2, 0).copy()
    write_rgb(tmp_path / "exp/ctrl/a.tif", data, tile=(256, 256), compression="zlib")
    result = run_described(tmp_path / "exp", prefill_mapping("YXS"))
    return RunInputs.open(result.output_folder), tmp_path / "exp/ctrl/a.tif", data


def test_run_inputs_reads_an_rgb_plane_with_one_decode(rgb_run, monkeypatch):
    inputs, _, data = rgb_run
    calls = count_decodes(monkeypatch)
    got = inputs.read_plane("ctrl/a.tif")
    assert calls["tiff"] == 1
    assert got.tobytes() == np.ascontiguousarray(data.transpose(2, 0, 1)).tobytes()


def test_a_plane_just_over_the_limit_is_refused_before_any_read(rgb_run, monkeypatch):
    inputs, path, data = rgb_run
    mapping = prefill_mapping("YXS")
    estimate = xyc_plane_memory(path, mapping)
    with tifffile.TiffFile(path) as tif:
        single = _layout(tif.series[0], mapping, path, Binning(histogram=False)).memory_estimate
    # One channel's read alone fits the limit: the per-channel check let this read through.
    assert single < estimate - 1
    calls = count_decodes(monkeypatch)
    with pytest.raises(PlaneMemoryError, match="all 3 channels") as refused:
        inputs.read_plane("ctrl/a.tif", memory_limit=estimate - 1)
    assert calls == {"tiff": 0, "zarr": 0}
    assert "more than the limit" in str(refused.value)
    got = inputs.read_plane("ctrl/a.tif", memory_limit=estimate)
    assert calls["tiff"] == 1
    np.testing.assert_array_equal(got, data.transpose(2, 0, 1))


def test_an_ome_zarr_plane_over_the_limit_is_refused_before_any_read(tmp_path, monkeypatch):
    data = _smooth((3, 64, 48), np.uint16)
    path = write_ngff(tmp_path / "a.zarr", data, axes="cyx")
    mapping = prefill_mapping("CYX")
    estimate = xyc_plane_memory(path, mapping)
    calls = count_decodes(monkeypatch)
    with pytest.raises(PlaneMemoryError, match="all 3 channels"):
        read_xyc_plane(path, mapping, memory_limit=estimate - 1)
    assert calls["zarr"] == 0
    np.testing.assert_array_equal(read_xyc_plane(path, mapping, memory_limit=estimate), data)
