"""RunInputs.read_window (issue #179): a run's window by canonical channel IDs."""

import os
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

from described_tree import run_described
from morphobricks_preprocessing import prefill_mapping, source_content
from morphobricks_preprocessing.run_inputs import RunInputs, RunInputsError
from morphobricks_preprocessing.run_inputs_error import ChannelNotFoundError
from morphobricks_preprocessing.testing import make_clean_run
from morphobricks_preprocessing.windows import (
    WindowBoundsError,
    WindowChannelError,
    WindowLevelError,
)
from tiff_tree import write_data
from zarr_tree import write_ngff


@pytest.fixture
def clean(tmp_path):
    return make_clean_run(tmp_path)


def test_a_window_is_the_slice_of_read_plane_in_the_channel_order_asked(clean):
    inputs = RunInputs.open(clean.output)
    for fov in inputs.fovs():
        plane = inputs.read_plane(fov)
        ids = fov.channel_ids
        for channels in ([ids[0]], list(ids), list(ids[::-1])):
            window = inputs.read_window(fov, t=0, z=0, bounds=(3, 11, 0, 17), channels=channels)
            order = [ids.index(c) for c in channels]
            expected = plane[order, 3:11, 0:17]
            assert window.dtype == expected.dtype and window.tobytes() == expected.tobytes()
        window = inputs.read_window(
            fov.relative_path, t=0, z=0, bounds=(0, 16, 0, 20), channels=ids
        )
        assert window.tobytes() == plane.tobytes()


def test_refusals(clean):
    inputs = RunInputs.open(clean.output)
    fov = inputs.fovs()[0]
    ids = list(fov.channel_ids)
    read = inputs.read_window
    with pytest.raises(ChannelNotFoundError, match="A_SCOPE_INCOMPLETE"):
        read(fov, t=0, z=0, bounds=(0, 4, 0, 4), channels=["not_a_channel"])
    for channels in ([], ids[0], None, [ids[0], ids[0]]):
        with pytest.raises(WindowChannelError):
            read(fov, t=0, z=0, bounds=(0, 4, 0, 4), channels=channels)
    with pytest.raises(WindowBoundsError):
        read(fov, t=0, z=0, bounds=(0, 17, 0, 4), channels=ids)  # 16 rows
    with pytest.raises(WindowLevelError):
        read(fov, t=0, z=0, bounds=(0, 4, 0, 4), channels=ids, level=1)
    with pytest.raises(TypeError, match="whole numbers"):
        read(fov, t=0, z=fov.plane_ids[0], bounds=(0, 4, 0, 4), channels=ids)
    with pytest.raises(IndexError):
        read(fov, t=1, z=0, bounds=(0, 4, 0, 4), channels=ids)
    with pytest.raises(TypeError):
        read(fov, 0, 0, (0, 4, 0, 4), ids)  # the plane and the window are named


def test_an_image_changed_since_the_run_is_not_read(clean):
    inputs = RunInputs.open(clean.output)
    fov = inputs.fovs()[0]
    path = clean.experiment / fov.relative_path
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    with pytest.raises(RunInputsError, match="changed"):
        inputs.read_window(fov, t=0, z=0, bounds=(0, 4, 0, 4), channels=fov.channel_ids)


def test_a_window_never_digests_the_image(clean, monkeypatch):
    inputs = RunInputs.open(clean.output)
    digested = []
    for name in ("file_sha256", "content_sha256", "source_content_sha256"):
        for module in [m for k, m in sys.modules.items() if k.startswith("morphobricks_pre")]:
            original = getattr(module, name, None)
            if callable(original):

                def spy(path, *args, original=original, **kwargs):
                    digested.append(Path(path).resolve())
                    return original(path, *args, **kwargs)

                monkeypatch.setattr(module, name, spy)
    fov = inputs.fovs()[0]
    inputs.read_window(fov, t=0, z=0, bounds=(0, 4, 0, 4), channels=fov.channel_ids)
    image = (clean.experiment / fov.relative_path).resolve()
    assert image not in digested
    source_content.current_content(image)  # the spy does see a content digest
    assert image in digested


@pytest.mark.parametrize("kind", ["tiled tiff", "ome-zarr"])
def test_a_described_run_of_chunked_images(tmp_path, kind):
    data = np.random.default_rng(2).integers(0, 4000, (2, 3, 40, 52), dtype=np.uint16)
    if kind == "ome-zarr":
        write_ngff(tmp_path / "exp/ctrl/a.zarr", data, "tcyx", chunks=(1, 2, 16, 16))
    else:
        write_data(tmp_path / "exp/ctrl/a.tif", data, "TCYX", tile=(16, 16), compression="zlib")
    result = run_described(tmp_path / "exp", prefill_mapping("TCYX"))
    assert result.written, result.issues
    inputs = RunInputs.open(result.output_folder)
    (fov,) = inputs.fovs()
    ids = fov.channel_ids
    channels = [ids[2], ids[0]]
    window = inputs.read_window(fov, t=1, z=0, bounds=(10, 40, 7, 33), channels=channels)
    np.testing.assert_array_equal(window, data[1, [2, 0], 10:40, 7:33])
    assert window.tobytes() == inputs.read_plane(fov, t=1)[[2, 0], 10:40, 7:33].tobytes()


def test_a_header_naming_x_before_y_gives_a_y_before_x_window(tmp_path):
    stored = np.arange(2 * 9 * 7, dtype=np.uint16).reshape(2, 9, 7)  # C, X, Y
    write_data(tmp_path / "exp/ctrl/a.tif", stored, "CXY")
    result = run_described(tmp_path / "exp", prefill_mapping("CXY"))
    assert result.written, result.issues
    inputs = RunInputs.open(result.output_folder)
    (fov,) = inputs.fovs()
    window = inputs.read_window(fov, t=0, z=0, bounds=(1, 6, 2, 8), channels=fov.channel_ids)
    np.testing.assert_array_equal(window, stored.transpose(0, 2, 1)[:, 1:6, 2:8])
    with tifffile.TiffFile(tmp_path / "exp/ctrl/a.tif") as tif:
        assert tif.series[0].axes == "CXY"
