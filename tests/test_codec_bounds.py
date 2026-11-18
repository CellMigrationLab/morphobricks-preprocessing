"""Only codecs with a measured bound are read by the bounded readers (issue #356, P2).

Owner decision of 18 November 2026: a window read, ``RunInputs.read_plane``'s read of every
channel of a plane (``plane_channels``) and planes over the memory limit read in blocks decode
only the TIFF codecs whose memory is measured (``codec_bounds.TIFF_BOUNDED``); any other is
refused when the read is planned, from the header, before any pixel is read, with the fix
named. Each refused codec is written for real when its encoder is available (imagecodecs is
not a dependency: only that codec's test is skipped without it), and the compression tag of an
uncompressed file is also rewritten to each refused code, so the refusals are tested on every
machine. The allowed codecs read bitwise as before, and whole planes read within the memory
limit are not refused. LZW and JPEG (8-bit greyscale, RGB or YCbCr subsampled at most 2 × 2) are
allowed since their memory is measured (``tests/test_codec_memory.py``): they read bitwise as
tifffile reads them through windows, every channel of a plane and blocks; a JPEG page in any
other form, and old-style JPEG, are still refused.
"""

import sys

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    check_planes,
    codec_bounds,
    iter_planes,
    plane_layout,
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
PATCHED = {6: "OJPEG", 32773: "PACKBITS", 34712: "JPEG2000", 50000: "ZSTD"}
PATCHED |= {50001: "WEBP", 34887: "LERC", 34933: "PNG", 50002: "JPEGXL", 12345: "an unknown"}


def expected(name, codec, reading, also=""):
    """The exact refusal of ``name`` stored with ``codec``, read as ``reading``."""
    return (
        f"{name} cannot be read {reading}: it is stored with {codec}, whose decoding memory "
        "has not been measured, so the memory of the read cannot be planned before any pixel "
        "is read. Only uncompressed, deflate (zlib) and LZW (also with the horizontal "
        "predictor), LZMA and 8-bit greyscale, RGB or YCbCr JPEG TIFF images are read this way "
        "(code compression_not_bounded). Save the image again as a TIFF that is uncompressed "
        "or compressed with deflate (zlib), LZW or LZMA (tiled, for a large image), or as "
        f"OME-Zarr.{also}"
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
        (5, 1),
        (5, 2),
        (7, 1),
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
        tifffile.imwrite(path, data, compression="packbits", metadata={"axes": "YX"})
        tifffile.imread(path)
    except (ImportError, ValueError, KeyError, NotImplementedError, RuntimeError) as exc:
        pytest.skip(f"no PackBits codec in this environment: {exc}")
    mapping = prefill_mapping("YX")
    np.testing.assert_array_equal(read_plane(path, mapping).pixels, data)
    [plane] = iter_planes(path, mapping)
    np.testing.assert_array_equal(plane.pixels, data)
    assert check_planes(tmp_path, {"l.tif": mapping}) == []


# LZW and JPEG: measured, read bitwise as tifffile reads them (#356) -------------------------

NEW = {
    "lzw": {"compression": "lzw"},
    "lzw predictor": {"compression": "lzw", "predictor": True},
    "jpeg": {"compression": "jpeg"},
}


def _bitwise(path, axes, channels, plane_count=1):
    """Windows, a cached reader, every channel of a plane and blocks equal tifffile's pixels."""
    mapping = prefill_mapping(axes)
    stored = tifffile.imread(path)
    planes = stored if plane_count > 1 else stored[None]  # (C, Y, X) or (1, Y, X[, S])
    if axes.endswith("S"):
        planes = np.moveaxis(planes[0], -1, 0)
    bounds = (5, 60, 7, 90)
    window = read_window(path, mapping, bounds=bounds, channels=channels)
    np.testing.assert_array_equal(window, planes[channels, 5:60, 7:90])
    with WindowReader(path, mapping, cache_bytes=2**22) as reader:
        np.testing.assert_array_equal(reader.read_window(bounds=bounds, channels=channels), window)
    np.testing.assert_array_equal(read_xyc_plane(path, mapping), planes)
    smallest = plane_layout(path, mapping, memory_limit=1).block_memory_estimate
    for c, plane in enumerate(iter_planes(path, mapping, memory_limit=smallest)):
        assert plane.in_blocks
        whole = np.empty(planes.shape[1:], dtype=planes.dtype)
        for block in plane.blocks():
            rows, cols = block.pixels.shape
            whole[block.y : block.y + rows, block.x : block.x + cols] = block.pixels
        np.testing.assert_array_equal(whole, planes[c])


@pytest.mark.parametrize("name", sorted(NEW))
def test_lzw_and_jpeg_planes_read_bitwise_through_every_bounded_reader(tmp_path, name):
    pytest.importorskip("imagecodecs")
    # 4 × 6 tiles per plane: a block of one row of tiles, its codec's state included, is
    # cheaper than the whole plane
    data = np.random.default_rng(5).integers(0, 2**8, (2, 4 * H, 4 * W), dtype=np.uint8)
    path = tmp_path / "a.tif"
    tifffile.imwrite(
        path,
        data,
        tile=(TILE, TILE),
        photometric="minisblack",
        metadata={"axes": "CYX"},
        **NEW[name],
    )
    if name.startswith("lzw"):
        np.testing.assert_array_equal(tifffile.imread(path), data)  # lossless
    _bitwise(path, "CYX", [1, 0], plane_count=2)


@pytest.mark.parametrize("kind", ["ycc420", "ycc422", "rgb"])
@pytest.mark.parametrize("storage", [{"tile": (TILE, TILE)}, {"rows": 16}], ids=["tiles", "strips"])
def test_jpeg_rgb_reads_bitwise_with_its_tables_in_the_header(tmp_path, kind, storage):
    from codec_images import write_jpeg

    path = write_jpeg(tmp_path / "rgb.tif", _rgb(), kind, **storage)
    with tifffile.TiffFile(path) as tif:
        assert tif.pages[0].jpegtables is not None and tif.pages[0].compression == 7
    _bitwise(path, "YXS", [2, 0, 1])


def test_a_jpeg_piece_declaring_more_pixels_is_refused_before_it_is_decoded(tmp_path, monkeypatch):
    # libjpeg sizes its buffers from the stream, not from the TIFF header: a stream declaring
    # a larger image than its tile is refused before libjpeg sees it (windows and blocks).
    pytest.importorskip("imagecodecs")
    import imagecodecs

    from morphobricks_preprocessing.plane_errors import PlaneReadError

    big = imagecodecs.jpeg8_encode(np.zeros((TILE * 4, TILE), np.uint8), level=90)
    pieces = [bytes(big)] * ((H // TILE) * -(-W // TILE))
    path = tmp_path / "big.tif"
    tifffile.imwrite(
        path,
        iter(pieces),
        shape=(H, W),
        dtype=np.uint8,
        tile=(TILE, TILE),
        compression=7,
        photometric="minisblack",
    )
    decoded = []
    monkeypatch.setattr(tifffile.TiffPage, "decode", property(lambda self: decoded.append(1)))
    with pytest.raises(PlaneReadError, match=r"declares 8-bit samples, 128 × 32 pixels"):
        read_window(path, prefill_mapping("YX"), bounds=(0, 8, 0, 8), channels=[0])
    assert decoded == [1]  # the decoder was fetched, never called


JPEG_FORMS = {  # a JPEG page in a form whose memory is not measured, and its refusal
    "separate planes": (
        {"photometric": "rgb", "planarconfig": "separate", "compression": "jpeg"},
        "SYX",
        "with its colour samples stored in separate planes",
    ),
    "subsampling 4 x 1": (
        {"photometric": "rgb", "compression": "jpeg", "subsampling": (4, 1)},
        "YXS",
        "with YCbCr subsampling (4, 1)",
    ),
}


@pytest.mark.parametrize("form", sorted(JPEG_FORMS))
def test_a_jpeg_page_in_an_unmeasured_form_is_refused(tmp_path, form, monkeypatch):
    kwargs, axes, how = JPEG_FORMS[form]
    data = _rgb() if axes == "YXS" else np.moveaxis(_rgb(), -1, 0).copy()
    path = tmp_path / "a.tif"
    try:
        tifffile.imwrite(path, data, tile=(TILE, TILE), metadata=None, **kwargs)
    except (ImportError, ValueError, KeyError, NotImplementedError, RuntimeError) as exc:
        pytest.skip(f"no JPEG encoder for {kwargs} in this environment: {exc}")
    codec = f"JPEG compression (TIFF code 7) {how}"
    with tifffile.TiffFile(path) as tif:
        assert codec_bounds.unbounded(tif.pages[0]) == codec
    _refused_without_pixels(monkeypatch, path, axes, codec, [0, 1, 2])


@pytest.mark.parametrize(
    ("tag", "value", "how"),
    [
        ("BitsPerSample", 12, "of 12-bit samples"),
        ("PhotometricInterpretation", 0, "of 1 sample(s) per pixel in MINISWHITE"),
    ],
)
def test_a_jpeg_header_of_an_unmeasured_form_is_refused(tmp_path, tag, value, how, monkeypatch):
    path = tmp_path / "p.tif"
    tifffile.imwrite(path, _rgb()[..., 0], tile=(TILE, TILE), metadata={"axes": "YX"})
    with tifffile.TiffFile(path, mode="r+b") as tif:
        tif.pages[0].tags["Compression"].overwrite(7)
        tif.pages[0].tags[tag].overwrite(value)
    _refused_without_pixels(monkeypatch, path, "YX", f"JPEG compression (TIFF code 7) {how}", [0])


# The measured decoders: imagecodecs, required and checked at run time (#356, review B2) -------


def test_tifffile_decodes_every_compressed_codec_with_the_measured_imagecodecs():
    import imagecodecs

    for code, names in codec_bounds.DECODERS.items():
        decoder = tifffile.TIFF.DECOMPRESSORS[code]
        assert any(decoder is getattr(imagecodecs, name) for name in names), (code, decoder)
        assert codec_bounds.decoder_unmeasured(code) is None
    assert codec_bounds.decoder_unmeasured(1) is None  # nothing decodes an uncompressed TIFF
    version = tuple(int(n) for n in imagecodecs.__version__.split(".")[:3])
    assert version >= codec_bounds.IMAGECODECS_MEASURED
    assert imagecodecs.jpeg8_version().startswith("libjpeg_turbo 3.")
    # deflate and LZMA too: one measured decoder path, never the standard library's
    assert {tifffile.TIFF.DECOMPRESSORS[c].__name__ for c in (8, 32946, 34925)} <= {
        "deflate_decode",
        "zlib_decode",
        "lzma_decode",
    }


GATES = {  # how the run-time check is made to fail, and what the refusal says
    "imagecodecs missing": (
        lambda m: m.setitem(sys.modules, "imagecodecs", None),
        "without imagecodecs installed (decoded only by imagecodecs 2024.12.30 or later",
    ),
    "imagecodecs too old": (
        lambda m: m.setattr(codec_bounds, "IMAGECODECS_MEASURED", (9999, 1, 1)),
        "(decoded only by imagecodecs 2024.12.30 or later, a required dependency: install or "
        "upgrade it)",
    ),
    "another decoder": (
        lambda m: m.setattr(codec_bounds, "DECODERS", {**codec_bounds.DECODERS, 8: ("nothing",)}),
        "decoded by imagecodecs.deflate_decode (decoded only by imagecodecs",
    ),
    "another libjpeg": (
        lambda m: m.setattr(codec_bounds, "LIBJPEG_MEASURED", "libjpeg 9"),
        "(decoded only with libjpeg-turbo 3)",
    ),
}


@pytest.mark.parametrize("gate", sorted(GATES))
def test_a_decoder_other_than_the_measured_one_is_refused_before_any_pixel(
    tmp_path, gate, monkeypatch
):
    pytest.importorskip("imagecodecs")
    jpeg = gate == "another libjpeg"
    path = tmp_path / "a.tif"
    compression = {"compression": "jpeg"} if jpeg else {"compression": "zlib"}
    tifffile.imwrite(
        path, _rgb()[..., 0], tile=(TILE, TILE), metadata={"axes": "YX"}, **compression
    )
    patch, words = GATES[gate]
    patch(monkeypatch)
    with tifffile.TiffFile(path) as tif:
        codec = codec_bounds.unbounded(tif.pages[0])
    assert codec is not None and words in codec
    _refused_without_pixels(monkeypatch, path, "YX", codec, [0])
