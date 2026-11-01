"""Helpers that write small OME-Zarr images for tests, independently of the brick's writer."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

AXIS_TYPES = {"t": "time", "c": "channel", "z": "space", "y": "space", "x": "space"}


def zarr_major() -> int:
    zarr = pytest.importorskip("zarr")
    return int(zarr.__version__.split(".")[0])


def write_ngff(  # noqa: PLR0913
    path,
    data,
    axes="czyx",
    *,
    version="0.4",
    chunks=None,
    channel_names=None,
    axis_types=None,
    extra_multiscales=0,
    scale=None,
    units=None,
    dataset_transforms=None,
    multiscale_transforms=None,
):
    """Write ``data`` as a one-level OME-Zarr image (OME-NGFF 0.4 or 0.5) at ``path``.

    ``axes`` gives one OME-NGFF axis name per dimension; ``axis_types`` overrides
    their types. ``scale`` is the level-0 scale (1 per axis by default),
    ``dataset_transforms`` replaces the level-0 ``coordinateTransformations``
    whole (``[]`` leaves them out), ``units`` maps axis names to their ``unit``,
    and ``multiscale_transforms`` adds ``coordinateTransformations`` to the
    multiscales entry. Returns the path.
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
    units = units or {}
    if dataset_transforms is None:
        dataset_transforms = [{"type": "scale", "scale": scale or [1.0] * data.ndim}]
    dataset = {"path": "0"}
    if dataset_transforms:
        dataset["coordinateTransformations"] = dataset_transforms
    multiscale = {
        "axes": [
            {"name": a, "type": t, **({"unit": units[a]} if a in units else {})}
            for a, t in zip(axes, types, strict=True)
        ],
        "datasets": [dataset],
    }
    if multiscale_transforms is not None:
        multiscale["coordinateTransformations"] = multiscale_transforms
    attrs: dict = {"multiscales": [multiscale] * (1 + extra_multiscales)}
    if channel_names:
        attrs["omero"] = {"channels": [{"label": name} for name in channel_names]}
    if version == "0.5":
        group.attrs.update({"ome": {"version": "0.5", **attrs}})
    else:
        multiscale["version"] = "0.4"
        group.attrs.update(attrs)
    return path


def write_labels(path, labels, axes="zyx", *, version="0.4"):
    """Add label images to the OME-Zarr image at ``path``: ``labels`` maps names to arrays.

    Each label image is written like an image (one level, ``image-label``
    metadata) under ``<path>/labels/<name>``, and listed in ``<path>/labels``.
    """
    zarr = pytest.importorskip("zarr")
    path = Path(path)
    zarr_format = 3 if version == "0.5" else 2
    for name, data in labels.items():
        write_ngff(path / "labels" / name, data, axes, version=version)
        label_group = zarr.open_group(str(path / "labels" / name), mode="a")
        image_label = {"image-label": {"version": version}}
        if version == "0.5":
            label_group.attrs.update({"ome": {**dict(label_group.attrs)["ome"], **image_label}})
        else:
            label_group.attrs.update(image_label)
    group = (
        zarr.open_group(str(path / "labels"), mode="a", zarr_format=zarr_format)
        if zarr_major() >= 3
        else zarr.open_group(str(path / "labels"), mode="a")
    )
    listing = {"labels": list(labels)}
    group.attrs.update({"ome": {"version": "0.5", **listing}} if version == "0.5" else listing)
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
