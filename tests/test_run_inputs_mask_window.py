"""RunInputs.read_mask_window, mask_window_plan and plane_shape: a run's masks and planes."""

import os
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

from described_tree import check_masks, describe_tree, run_described
from morphobricks_preprocessing import prefill_mapping, run_experiment, source_content
from morphobricks_preprocessing.axes import AxisMapping
from morphobricks_preprocessing.run_inputs import RunInputs, RunInputsError
from morphobricks_preprocessing.run_inputs_error import MaskUncheckedError
from morphobricks_preprocessing.windows import (
    WindowBoundsError,
    WindowLevelError,
    WindowMemoryError,
)
from test_mask_windows import binary, instances, parent_labels
from tiff_tree import write_data, write_plain
from zarr_tree import write_labels, write_ngff

H, W = 40, 52
IMAGE = "ctrl/a.tif"
BOUNDS = [(0, H, 0, W), (0, 1, 0, 1), (H - 1, H, W - 1, W), (5, 33, 11, 50), (16, 32, 16, 48)]


def described_run(root, mapping, representation="instances", *, masks=None, mask_axes=None):
    """Publish the tree's description with ``representation`` masks, run and (optionally) check."""
    describe_tree(
        root, None, mapping, masks_folder=masks, mask_axes=mask_axes, representation=representation
    )
    result = run_experiment(root, mapping, masks_folder=masks, mask_axes=mask_axes)
    assert result.written, result.issues
    return result.output_folder


# representation: (values, dtype)
REPRESENTATIONS = {
    "instances": (instances, np.uint16),
    "binary": (binary, np.uint8),
    "parent_labels": (parent_labels, np.int32),
}


@pytest.mark.parametrize("representation", sorted(REPRESENTATIONS))
def test_a_tiled_mask_window_is_the_slice_of_read_mask_for_every_representation(
    tmp_path, representation
):
    root, masks = tmp_path / "exp", tmp_path / "masks"
    write_data(root / IMAGE, np.zeros((2, H, W), np.uint8), "TYX")
    values, dtype = REPRESENTATIONS[representation]
    labels = values((2, H, W), dtype)
    write_data(masks / "Cells" / IMAGE, labels, "TYX", tile=(16, 16), compression="zlib")
    output = check_masks(described_run(root, prefill_mapping("TYX"), representation, masks=masks))
    inputs = RunInputs.open(output)
    assert inputs.mask_semantics()["Cells"].representation == representation
    for t in range(2):
        plane = inputs.read_mask(IMAGE, "Cells", t=t)
        for bounds in BOUNDS:
            y0, y1, x0, x1 = bounds
            window = inputs.read_mask_window(IMAGE, "Cells", t=t, z=0, bounds=bounds)
            assert window.dtype == plane.dtype == dtype
            assert window.tobytes() == plane[y0:y1, x0:x1].tobytes()
            np.testing.assert_array_equal(window, labels[t, y0:y1, x0:x1])
    plan = inputs.mask_window_plan(IMAGE, "Cells", bounds=(5, 33, 11, 50))
    assert (plan.shape, plan.dtype, plan.pieces) == ((28, 39), np.dtype(dtype), 12)
    assert plan.memory_estimate >= 28 * 39 * np.dtype(dtype).itemsize


def test_a_label_image_window_by_the_recorded_pairing(tmp_path):
    pytest.importorskip("zarr")
    root = tmp_path / "exp"
    path = write_ngff(root / "ctrl/a.ome.zarr", np.zeros((2, H, W), np.uint8), "tyx")
    labels = instances((2, H, W), np.uint32)
    write_labels(path, {"cells": labels}, "tyx", chunks=(1, 16, 24))
    inputs = RunInputs.open(check_masks(described_run(root, prefill_mapping("TYX"))))
    fov = inputs.fovs()[0]
    for bounds in BOUNDS:
        y0, y1, x0, x1 = bounds
        window = inputs.read_mask_window(fov, "cells", t=1, z=0, bounds=bounds)
        plane = inputs.read_mask(fov, "cells", t=1)
        assert window.dtype == plane.dtype == np.uint32
        assert window.tobytes() == plane[y0:y1, x0:x1].tobytes()
    assert inputs.mask_window_plan(fov, "cells", bounds=(0, 16, 0, 24)).pieces == 1


def test_a_stated_plain_stack_mask_window(tmp_path):
    root, masks = tmp_path / "exp", tmp_path / "masks"
    write_data(root / IMAGE, np.zeros((3, H, W), np.uint8), "TYX")
    labels = instances((3, H, W), np.uint16)
    write_plain(masks / "Cells" / IMAGE, labels)
    output = described_run(root, prefill_mapping("TYX"), masks=masks, mask_axes={"Cells": "TYX"})
    inputs = RunInputs.open(check_masks(output))
    window = inputs.read_mask_window(IMAGE, "Cells", t=2, z=0, bounds=(3, 30, 7, 41))
    np.testing.assert_array_equal(window, labels[2, 3:30, 7:41])
    assert window.tobytes() == inputs.read_mask(IMAGE, "Cells", t=2)[3:30, 7:41].tobytes()


@pytest.fixture
def unchecked(tmp_path):
    """A described run whose mask the mask step has not checked yet."""
    root, masks = tmp_path / "exp", tmp_path / "masks"
    write_data(root / IMAGE, np.zeros((H, W), np.uint8), "YX")
    write_data(masks / "Cells" / IMAGE, instances((H, W), np.uint16), "YX", tile=(16, 16))
    return described_run(root, prefill_mapping("YX"), masks=masks), masks, root


def test_a_mask_the_mask_step_has_not_checked_is_refused_as_read_mask_refuses_it(unchecked):
    output, _, _ = unchecked
    inputs = RunInputs.open(output)
    with pytest.raises(MaskUncheckedError, match="A_MASK_UNCHECKED") as whole:
        inputs.read_mask(IMAGE, "Cells")
    with pytest.raises(MaskUncheckedError) as window:
        inputs.read_mask_window(IMAGE, "Cells", t=0, z=0, bounds=(0, 4, 0, 4))
    with pytest.raises(MaskUncheckedError) as plan:
        inputs.mask_window_plan(IMAGE, "Cells", bounds=(0, 4, 0, 4))
    assert str(window.value) == str(plan.value) == str(whole.value)
    check_masks(output)
    assert inputs.read_mask_window(IMAGE, "Cells", t=0, z=0, bounds=(0, 4, 0, 4)).shape == (4, 4)


def test_refusals(unchecked):
    output, masks, _ = unchecked
    inputs = RunInputs.open(check_masks(output))
    read = inputs.read_mask_window
    with pytest.raises(RunInputsError, match="no Nuclei mask"):
        read(IMAGE, "Nuclei", t=0, z=0, bounds=(0, 4, 0, 4))
    with pytest.raises(RunInputsError, match="not an image of this run"):
        read("ctrl/b.tif", "Cells", t=0, z=0, bounds=(0, 4, 0, 4))
    for bounds in [(0, H + 1, 0, 4), (4, 4, 0, 4), (0.0, 4, 0, 4), None]:
        with pytest.raises(WindowBoundsError):
            read(IMAGE, "Cells", t=0, z=0, bounds=bounds)
        with pytest.raises(WindowBoundsError):
            inputs.mask_window_plan(IMAGE, "Cells", bounds=bounds)
    with pytest.raises(WindowLevelError):
        read(IMAGE, "Cells", t=0, z=0, bounds=(0, 4, 0, 4), level=1)
    with pytest.raises(WindowMemoryError):
        read(IMAGE, "Cells", t=0, z=0, bounds=(0, H, 0, W), memory_limit=1000)
    with pytest.raises(TypeError, match="whole numbers"):
        read(IMAGE, "Cells", t=0, z=None, bounds=(0, 4, 0, 4))
    with pytest.raises(IndexError):
        read(IMAGE, "Cells", t=1, z=0, bounds=(0, 4, 0, 4))
    with pytest.raises(TypeError):
        read(IMAGE, "Cells", 0, 0, (0, 4, 0, 4))  # the plane and the window are named
    path = masks / "Cells" / IMAGE
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    with pytest.raises(RunInputsError, match="changed"):
        read(IMAGE, "Cells", t=0, z=0, bounds=(0, 4, 0, 4))
    with pytest.raises(RunInputsError, match="changed"):
        inputs.mask_window_plan(IMAGE, "Cells", bounds=(0, 4, 0, 4))


def test_a_mask_window_never_digests_the_mask(unchecked, monkeypatch):
    output, masks, root = unchecked
    inputs = RunInputs.open(check_masks(output))
    digested = []
    for name in ("file_sha256", "content_sha256", "source_content_sha256"):
        for module in [m for k, m in sys.modules.items() if k.startswith("morphobricks_pre")]:
            original = getattr(module, name, None)
            if callable(original):

                def spy(path, *args, original=original, **kwargs):
                    digested.append(Path(path).resolve())
                    return original(path, *args, **kwargs)

                monkeypatch.setattr(module, name, spy)
    inputs.read_mask_window(IMAGE, "Cells", t=0, z=0, bounds=(0, 4, 0, 4))
    inputs.mask_window_plan(IMAGE, "Cells", bounds=(0, 4, 0, 4))
    inputs.plane_shape(IMAGE)
    mask = (masks / "Cells" / IMAGE).resolve()
    assert mask not in digested and (root / IMAGE).resolve() not in digested
    source_content.current_content(mask)  # the spy does see a content digest
    assert mask in digested


# plane_shape -----------------------------------------------------------------------------


def rgb(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, data, photometric="rgb", metadata={"axes": "YXS"}, tile=(16, 16))


# name: (writer, mapping, stored shape)
IMAGES = {
    "tiled TCYX": (lambda p, d: write_data(p, d, "TCYX", tile=(16, 16)), "TCYX", (2, 3, H, W)),
    "strips CYX, zlib": (
        lambda p, d: write_data(p, d, "CYX", rowsperstrip=5, compression="zlib"),
        "CYX",
        (2, H, W),
    ),
    "header naming X before Y": (lambda p, d: write_data(p, d, "CXY"), "CXY", (2, W, H)),
    "plain stack confirmed TYX": (
        lambda p, d: write_plain(p, d),
        AxisMapping("IYX", "TYX"),
        (3, H, W),
    ),
    "RGB": (rgb, "YXS", (H, W, 3)),
    "OME-Zarr 16 x 24 chunks": (
        lambda p, d: write_ngff(p, d, "tcyx", chunks=(1, 1, 16, 24)),
        "TCYX",
        (2, 2, H, W),
    ),
}


@pytest.mark.parametrize("name", sorted(IMAGES))
def test_plane_shape_is_read_plane_s_height_and_width_without_reading_pixels(
    tmp_path, name, monkeypatch
):
    write, mapping, shape = IMAGES[name]
    if "Zarr" in name:
        pytest.importorskip("zarr")
    data = np.random.default_rng(5).integers(0, 200, shape, dtype=np.uint8)
    image = "ctrl/a.zarr" if "Zarr" in name else IMAGE
    write(tmp_path / "exp" / image, data)
    mapping = prefill_mapping(mapping) if isinstance(mapping, str) else mapping
    result = run_described(tmp_path / "exp", mapping)
    assert result.written, result.issues
    inputs = RunInputs.open(result.output_folder)
    (fov,) = inputs.fovs()
    expected = inputs.read_plane(fov).shape[-2:]
    assert expected == (H, W)

    def no_pixels(*args, **kwargs):
        raise AssertionError("plane_shape read pixels")

    monkeypatch.setattr(tifffile.TiffPage, "asarray", no_pixels)
    monkeypatch.setattr(tifffile.TiffPage, "decode", property(no_pixels))
    shape_now = inputs.plane_shape(fov)
    assert shape_now == expected and inputs.plane_shape(fov.relative_path) == expected
    assert all(type(n) is int for n in shape_now)


def test_plane_shape_refuses_an_image_changed_since_the_run(unchecked):
    output, _, root = unchecked
    inputs = RunInputs.open(output)
    path = root / IMAGE
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    with pytest.raises(RunInputsError, match="changed"):
        inputs.plane_shape(IMAGE)
