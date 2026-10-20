"""Helpers that write small OME-Zarr images for tests, independently of the brick's writer."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

AXIS_TYPES = {"t": "time", "c": "channel", "z": "space", "y": "space", "x": "space"}


def zarr_major() -> int:
    zarr = pytest.importorskip("zarr")
    return int(zarr.__version__.split(".")[0])


def write_ngff(
    path,
    data,
    axes="czyx",
    *,
    version="0.4",
    chunks=None,
    channel_names=None,
    axis_types=None,
    extra_multiscales=0,
):
    """Write ``data`` as a one-level OME-Zarr image (OME-NGFF 0.4 or 0.5) at ``path``.

    ``axes`` gives one OME-NGFF axis name per dimension; ``axis_types`` overrides
    their types. Returns the path.
    """
    zarr = pytest.importorskip("zarr")
    if version == "0.5" and zarr_major() < 3:
        pytest.skip("OME-NGFF 0.5 needs zarr 3")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.asarray(data)
    group = _group(zarr, path, 3 if version == "0.5" else 2)
    chunks = chunks or (*(1,) * (data.ndim - 2), *data.shape[-2:])
    _array(group, "0", data, chunks)
    types = axis_types or [AXIS_TYPES.get(a, "space") for a in axes]
    multiscale = {
        "axes": [{"name": a, "type": t} for a, t in zip(axes, types, strict=True)],
        "datasets": [
            {
                "path": "0",
                "coordinateTransformations": [{"type": "scale", "scale": [1.0] * data.ndim}],
            }
        ],
    }
    attrs: dict = {"multiscales": [multiscale] * (1 + extra_multiscales)}
    if channel_names:
        attrs["omero"] = {"channels": [{"label": name} for name in channel_names]}
    if version == "0.5":
        group.attrs.update({"ome": {"version": "0.5", **attrs}})
    else:
        multiscale["version"] = "0.4"
        group.attrs.update(attrs)
    return path


def write_bioformats2raw(path, images, axes="zyx"):
    """Write a bioformats2raw folder holding one OME-NGFF 0.4 image per entry of ``images``."""
    zarr = pytest.importorskip("zarr")
    path = Path(path)
    group = _group(zarr, path, 2)
    group.attrs.update({"bioformats2raw.layout": 3})
    for i, data in enumerate(images):
        write_ngff(path / str(i), data, axes)
    return path


def write_plate(path, data, axes="zyx"):
    """Write an OME-NGFF 0.4 plate with two wells of one field each."""
    zarr = pytest.importorskip("zarr")
    path = Path(path)
    group = _group(zarr, path, 2)
    wells = ["A/1", "A/2"]
    group.attrs.update({"plate": {"wells": [{"path": w} for w in wells], "version": "0.4"}})
    for well in wells:
        well_group = _group(zarr, path / well, 2)
        well_group.attrs.update({"well": {"images": [{"path": "0"}], "version": "0.4"}})
        write_ngff(path / well / "0", data, axes)
    return path


def _group(zarr, path: Path, zarr_format: int):
    if zarr_major() >= 3:
        return zarr.open_group(str(path), mode="w", zarr_format=zarr_format)
    return zarr.open_group(str(path), mode="w")


def _array(group, name, data, chunks):
    if zarr_major() >= 3:
        array = group.create_array(name, shape=data.shape, chunks=chunks, dtype=data.dtype)
    else:
        array = group.create_dataset(name, shape=data.shape, chunks=chunks, dtype=data.dtype)
    array[...] = data
    return array
