"""Only codecs with a measured bound are read by the bounded readers (issue #356, P2).

Owner decision of 18 November 2026: a window read, ``RunInputs.read_plane``'s read of every
channel of a plane (``plane_channels``) and planes over the memory limit read in blocks decode
only the TIFF codecs whose memory is measured (``codec_bounds.TIFF_BOUNDED``); any other is
refused when the read is planned, from the header, before any pixel is read, with the fix
named. Each refused codec is written for real when its encoder is available (imagecodecs is
not a dependency: only that codec's test is skipped without it), and the compression tag of an
uncompressed file is also rewritten to each refused code, so the refusals are tested on every
machine. The allowed codecs read bitwise as before, and whole planes read within the memory
limit are not refused.
"""

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    check_planes,
    codec_bounds,
    iter_planes,
    prefill_mapping,
    read_plane,
    tiff_pieces,
)
from morphobricks_preprocessing.plane_channels import read_xyc_plane, xyc_plane_memory
from morphobricks_preprocessing.plane_errors import CompressionNotBoundedError
from morphobricks_preprocessing.window_reader import WindowReader, cache_plan
from morphobricks_preprocessing.windows import (
    WindowCompressionError,
    WindowLayoutError,
    read_window,
    window_plan,
)

H, W, TILE = 64, 96, 32
BLOCK_LIMIT = 2**10  # far below a plane: blocks are needed
RGB = {"photometric": "rgb", "planarconfig": "contig"}
REFUSED = {  # how each refused codec is written, and how its refusal names it
    "lzw": ({"compression": "lzw"}, "LZW compression (TIFF code 5)"),
    "jpeg": ({"compression": "jpeg"}, "JPEG compression (TIFF code 7)"),
    "zstd": ({"compression": "zstd"}, "ZSTD compression (TIFF code 50000)"),
    "webp": ({"compression": "webp"}, "WEBP compression (TIFF code 50001)"),
    "jpeg2000": ({"compression": "jpeg2000"}, "JPEG2000 compression (TIFF code 34712)"),
    "packbits": ({"compression": "packbits"}, "PACKBITS compression (TIFF code 32773)"),
    "png": ({"compression": "png"}, "PNG compression (TIFF code 34933)"),
    "lerc": ({"compression": "lerc"}, "LERC compression (TIFF code 34887)"),
    "jpegxl": ({"compression": "jpegxl"}, "JPEGXL compression (TIFF code 50002)"),
    "lzma horizontal predictor": (
        {"compression": "lzma", "predictor": True},
        "LZMA compression (TIFF code 34925) with the HORIZONTAL predictor (2)",
    ),
}
FLOAT_PREDICTOR = "ADOBE_DEFLATE compression (TIFF code 8) with the FLOATINGPOINT predictor (3)"
PATCHED = {5: "LZW", 6: "OJPEG", 7: "JPEG", 32773: "PACKBITS", 34712: "JPEG2000", 50000: "ZSTD"}
PATCHED |= {50001: "WEBP", 34887: "LERC", 34933: "PNG", 50002: "JPEGXL", 12345: "an unknown"}


def expected(name, codec, reading, also=""):
    """The exact refusal of ``name`` stored with ``codec``, read as ``reading``."""
    return (
        f"{name} cannot be read {reading}: it is stored with {codec}, whose decoding memory "
        "has not been measured, so the memory of the read cannot be planned before any pixel "
        "is read. Only uncompressed, deflate (zlib, also with the horizontal predictor) and "
        "LZMA TIFF images are read this way (code compression_not_bounded). Save the image "
        "again as a TIFF that is uncompressed or compressed with deflate (zlib) or LZMA "
        f"(tiled, for a large image), or as OME-Zarr.{also}"
    )


BLOCKS = f"in blocks (its planes are over the memory limit of {BLOCK_LIMIT // 2**10}.0 KiB)"
BLOCKS_ALSO = " Or raise the memory limit so that its planes are read whole."
XYC = "every channel at once (RunInputs.read_plane)"


def _rgb():
    return np.random.default_rng(356).integers(0, 256, (H, W, 3), dtype=np.uint8)


def _write(path, data, **kwargs):
    """Write ``data`` tiled with ``kwargs``; skip when this codec's encoder is unavailable."""
    try:
        tifffile.imwrite(path, data, tile=(TILE, TILE), metadata={"axes": "YXS"}, **kwargs)
    except (ImportError, ValueError, KeyError, NotImplementedError, RuntimeError) as exc:
        pytest.skip(f"no encoder for {kwargs} in this environment: {exc}")
    return path


def _assert_refused(path, axes, codec, channels):
    """Every bounded reader refuses ``path`` with the exact message, before any pixel."""
    mapping = prefill_mapping(axes)
    name = path.name
    windows = expected(name, codec, "in windows")
    for call in (
        lambda: window_plan(path, mapping, bounds=(0, 8, 0, 8), channels=channels),
        lambda: read_window(path, mapping, bounds=(0, 8, 0, 8), channels=channels),
        lambda: cache_plan(path, mapping, window=(8, 8), margin=(0, 0), channels=channels),
        lambda: WindowReader(path, mapping, cache_bytes=2**20).read_window(
            bounds=(0, 8, 0, 8), channels=channels
        ),
    ):
        with pytest.raises(WindowCompressionError) as found:
            call()
        assert str(found.value) == windows
        assert isinstance(found.value, WindowLayoutError | CompressionNotBoundedError)
        assert found.value.code == "compression_not_bounded"
    for call in (
        lambda: read_xyc_plane(path, mapping),
        lambda: xyc_plane_memory(path, mapping),
    ):
        with pytest.raises(CompressionNotBoundedError) as found:
            call()
        assert str(found.value) == expected(name, codec, XYC)
    blocks = expected(name, codec, BLOCKS, BLOCKS_ALSO)
    for call in (
        lambda: list(iter_planes(path, mapping, memory_limit=BLOCK_LIMIT)),
        lambda: read_plane(path, mapping, memory_limit=BLOCK_LIMIT, preview=True),
        lambda: read_plane(path, mapping, memory_limit=BLOCK_LIMIT),
    ):
        with pytest.raises(CompressionNotBoundedError) as found:
            call()
        assert str(found.value) == blocks
    [issue] = check_planes(path.parent, {name: mapping}, memory_limit=BLOCK_LIMIT)
    assert issue.code == "compression_not_bounded" and issue.is_error
    assert list(issue.paths) == [name] and codec in issue.message


def test_the_allow_list_is_the_measured_matrix():
    assert set(codec_bounds.TIFF_BOUNDED) == {
        (1, 1),
        (8, 1),
        (8, 2),
        (32946, 1),
        (32946, 2),
        (34925, 1),
    }
    # code 32946 is decoded by the very function that decodes the measured code 8
    assert tifffile.TIFF.DECOMPRESSORS[32946] is tifffile.TIFF.DECOMPRESSORS[8]
    assert set(codec_bounds.ZARR_MEASURED) == {"blosc lz4", "zstd"}


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_a_refused_codec_is_refused_before_any_pixel(tmp_path, name, monkeypatch):
    kwargs, codec = REFUSED[name]
    if name == "lzma horizontal predictor":  # the predictor needs a gray plane
        data = _rgb()[..., 0]
        path = tmp_path / "a.tif"
        try:
            tifffile.imwrite(path, data, tile=(TILE, TILE), metadata={"axes": "YX"}, **kwargs)
        except (ImportError, ValueError, KeyError, NotImplementedError, RuntimeError) as exc:
            pytest.skip(f"no encoder for {kwargs} in this environment: {exc}")
        axes, channels = "YX", [0]
    else:
        path = _write(tmp_path / "a.tif", _rgb(), **RGB, **kwargs)
        axes, channels = "YXS", [0, 1, 2]
    with tifffile.TiffFile(path) as tif:  # what was written is the codec named
        assert codec_bounds.describe(*codec_bounds.tiff_codec(tif.pages[0])) == codec
    _refused_without_pixels(monkeypatch, path, axes, codec, channels)


def test_the_floating_point_predictor_is_refused(tmp_path, monkeypatch):
    data = np.random.default_rng(3).random((H, W)).astype(np.float32)
    path = tmp_path / "f.tif"
    try:
        tifffile.imwrite(
            path, data, tile=(TILE, TILE), compression="zlib", predictor=True, metadata=None
        )
    except (ImportError, ValueError, KeyError, NotImplementedError, RuntimeError) as exc:
        pytest.skip(f"no floating-point predictor encoder in this environment: {exc}")
    _refused_without_pixels(monkeypatch, path, "YX", FLOAT_PREDICTOR, [0])


@pytest.mark.parametrize("code", sorted(PATCHED))
def test_every_refused_code_is_refused_from_the_header(tmp_path, code, monkeypatch):
    # The compression tag of an uncompressed tiled file is rewritten: no encoder is needed and
    # no pixel could be decoded, so the refusal can only come from the header.
    path = tmp_path / "p.tif"
    tifffile.imwrite(path, _rgb()[..., 0], tile=(TILE, TILE), metadata={"axes": "YX"})
    with tifffile.TiffFile(path, mode="r+b") as tif:
        tif.pages[0].tags["Compression"].overwrite(code)
    codec = f"{PATCHED[code]} compression (TIFF code {code})"
    _refused_without_pixels(monkeypatch, path, "YX", codec, [0])


def _refused_without_pixels(monkeypatch, path, axes, codec, channels):
    def refuse(*args, **kwargs):
        raise AssertionError("a pixel was read")

    monkeypatch.setattr(tiff_pieces, "_decode_segment", refuse)
    for cls in (tifffile.TiffPage, tifffile.TiffFrame):
        monkeypatch.setattr(cls, "asarray", refuse)
    monkeypatch.setattr(tifffile.FileHandle, "read_array", refuse)
    _assert_refused(path, axes, codec, channels)


ALLOWED = {
    "none": {},
    "zlib": {"compression": "zlib"},
    "zlib predictor": {"compression": "zlib", "predictor": True},
    "lzma": {"compression": "lzma"},
    "deflate 32946": {"compression": "zlib"},  # its tag rewritten to 32946 below
}


@pytest.mark.parametrize("name", sorted(ALLOWED))
def test_the_allowed_codecs_read_bitwise_through_every_bounded_reader(tmp_path, name):
    data = np.random.default_rng(5).integers(0, 2**8, (2, H, W), dtype=np.uint8)
    path = tmp_path / "a.tif"
    tifffile.imwrite(
        path,
        data,
        tile=(TILE, TILE),
        photometric="minisblack",
        metadata={"axes": "CYX"},
        **ALLOWED[name],
    )
    if name == "deflate 32946":
        with tifffile.TiffFile(path, mode="r+b") as tif:
            for page in tif.pages:
                page.tags["Compression"].overwrite(32946)
    mapping = prefill_mapping("CYX")
    bounds = (5, 60, 7, 90)
    window = read_window(path, mapping, bounds=bounds, channels=[1, 0])
    np.testing.assert_array_equal(window, data[[1, 0], 5:60, 7:90])
    with WindowReader(path, mapping, cache_bytes=2**20) as reader:
        np.testing.assert_array_equal(reader.read_window(bounds=bounds, channels=[1, 0]), window)
    np.testing.assert_array_equal(read_xyc_plane(path, mapping), data)
    for c, plane in enumerate(iter_planes(path, mapping, memory_limit=2**16)):
        assert plane.in_blocks  # each plane read while the file is open
        whole = np.empty((H, W), dtype=np.uint8)
        for block in plane.blocks():
            rows, cols = block.pixels.shape
            whole[block.y : block.y + rows, block.x : block.x + cols] = block.pixels
        np.testing.assert_array_equal(whole, data[c])
    assert [i.code for i in check_planes(tmp_path, {"a.tif": mapping}, memory_limit=2**16)] == [
        "planes_read_in_blocks"
    ]


def test_whole_planes_within_the_limit_are_not_refused(tmp_path):
    # The whole-plane reader's estimate is not stated as a bound for any codec (codec_bounds,
    # "Not covered"): a plane read whole within the limit is read as before, whatever its codec.
    data = _rgb()[..., 0]
    path = tmp_path / "l.tif"
    try:
        tifffile.imwrite(path, data, compression="lzw", metadata={"axes": "YX"})
        tifffile.imread(path)
    except (ImportError, ValueError, KeyError, NotImplementedError, RuntimeError) as exc:
        pytest.skip(f"no LZW codec in this environment: {exc}")
    mapping = prefill_mapping("YX")
    np.testing.assert_array_equal(read_plane(path, mapping).pixels, data)
    [plane] = iter_planes(path, mapping)
    np.testing.assert_array_equal(plane.pixels, data)
    assert check_planes(tmp_path, {"l.tif": mapping}) == []
