"""Write one image as an OME-Zarr copy (OME-NGFF 0.4), plane by plane (contract §17).

Owner (11 November 2026): the notebook can also save the experiment as OME-Zarr, once the axes
are confirmed, as a recorded conversion. A copy is written by
:func:`.conversion.convert_experiment` with ``store="ome-zarr"``: the same plan, lock,
``.partial`` discipline and conversion record as the named-axis OME-TIFF copies, with these
writers in place of :mod:`.ome_tiff_copy`'s.

The store is written directly (no ``zarr`` package is needed to write it; reading it needs the
``zarr`` extra, as for any OME-Zarr image): a Zarr version 2 group with one array ``0``,
uncompressed, one chunk per plane (the whole Y and X), little-endian, so every plane has a
fixed file and the same copy is the same bytes. Each plane is the plane read under the
confirmed roles, bit for bit; a projected copy's planes are computed as in
:func:`.ome_tiff_copy.write_projected_copy`. The metadata follow the copy's layout
(:class:`.copy_axes.CopyLayout`): the axes by role with their units, the level-0 ``scale`` from
the pixel sizes (an unknown size is the placeholder 1, named under ``metadata`` /
``unknown_scale`` so it is read as unknown, :mod:`.physical_sizes`), the channel names under
``omero``, and a projected copy's description under ``morphobricks``. Significant bits cannot
be stated in OME-NGFF 0.4 and are not written (the record says so).

A copy's name is the image's with ``.ome.zarr`` in place of its TIFF extension
(:func:`zarr_copy_name`), so its position ID is unchanged; the record keeps the source's own
path (:attr:`.conversion_record.CopyEntry.source_relative_path`).
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import numpy as np

from .axes import AxisMapping
from .copy_axes import CopyLayout
from .image_names import position_id
from .ome_tiff_copy import CopyWriteError
from .physical_sizes import UNKNOWN_SCALE_KEY
from .planes import DEFAULT_MEMORY_LIMIT, iter_planes, read_plane
from .planned_copy import PlannedCopy
from .projection import PlaneAccumulator
from .projection_record import ProjectionEntry, projection_description

OME_TIFF = "ome-tiff"
"""The store of a named-axis copy written as OME-TIFF (the default)."""
OME_ZARR = "ome-zarr"
"""The store of a copy written as OME-Zarr."""
STORES = (OME_TIFF, OME_ZARR)
ZARR_COPY_SUFFIX = ".ome.zarr"
NGFF_VERSION = "0.4"
_AXIS_TYPES = {"T": "time", "C": "channel", "Z": "space", "Y": "space", "X": "space"}
BITS_NOT_WRITTEN = "OME-NGFF 0.4 has no field for significant bits"


def zarr_copy_name(relative_path: str) -> str:
    """Return the OME-Zarr copy's path for an image: the same folder and position ID."""
    folder, _, _name = relative_path.rpartition("/")
    name = position_id(relative_path) + ZARR_COPY_SUFFIX
    return f"{folder}/{name}" if folder else name


def as_zarr_copy(planned: PlannedCopy) -> PlannedCopy:
    """Return a planned OME-TIFF copy as the same copy written as OME-Zarr.

    A byte-for-byte copy (an excluded image, an OME-Zarr image) is left as it is. The entry
    takes the copy's name and keeps the source's path; significant bits are not carried.
    """
    if planned.layout is None:
        return planned
    source = planned.entry.relative_path
    entry = replace(
        planned.entry,
        relative_path=zarr_copy_name(source),
        source_relative_path=source,
        significant_bits=None,
    )
    layout = replace(planned.layout, significant_bits=None)
    return replace(planned, entry=entry, layout=layout, store=OME_ZARR)


def _metadata(layout: CopyLayout, description: str | None) -> dict:
    """Return the group's attributes: OME-NGFF 0.4 multiscales, channel names, description."""
    sizes = {size.role: size for size in layout.sizes}
    axes, scale, unknown = [], [], []
    for role in layout.axes:
        axis = {"name": role.lower(), "type": _AXIS_TYPES[role]}
        size = sizes.get(role)
        if role != "C" and (size is None or size.value is None):
            unknown.append(role.lower())
        if size is not None and size.value is not None:
            axis["unit"] = size.unit
        axes.append(axis)
        scale.append(float(size.value) if size is not None and size.value is not None else 1.0)
    multiscale: dict = {
        "version": NGFF_VERSION,
        "axes": axes,
        "datasets": [
            {"path": "0", "coordinateTransformations": [{"type": "scale", "scale": scale}]}
        ],
    }
    if unknown:
        multiscale["metadata"] = {UNKNOWN_SCALE_KEY: unknown}
    attrs: dict = {"multiscales": [multiscale]}
    if layout.channel_names:
        attrs["omero"] = {"channels": [{"label": name} for name in layout.channel_names]}
    if description:
        attrs["morphobricks"] = {"description": description}
    return attrs


def _json(path: Path, data: dict) -> None:
    """Write metadata as sorted JSON with LF line endings (the same data, the same bytes)."""
    text = json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_bytes(text.encode("utf-8"))


def _lay_out(target: Path, layout: CopyLayout, dtype: np.dtype, description: str | None) -> Path:
    """Write the store's metadata and return the folder of the array's chunks."""
    target.mkdir(parents=True)
    array = target / "0"
    array.mkdir()
    _json(target / ".zgroup", {"zarr_format": 2})
    _json(target / ".zattrs", _metadata(layout, description))
    rows, cols = layout.plane_shape
    stored = dtype.newbyteorder("<") if dtype.itemsize > 1 else dtype
    _json(
        array / ".zarray",
        {
            "zarr_format": 2,
            "shape": list(layout.shape),
            "chunks": [*([1] * (len(layout.shape) - 2)), rows, cols],
            "dtype": stored.str,
            "compressor": None,
            "filters": None,
            "fill_value": 0,
            "order": "C",
            "dimension_separator": "/",
        },
    )
    return array


def _chunk_path(array: Path, position: tuple[int, ...]) -> Path:
    """Return the file of the plane at ``position`` (its leading indices), making its folder."""
    path = array.joinpath(*(str(i) for i in position), "0", "0")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _little(pixels: np.ndarray, dtype: np.dtype) -> np.ndarray:
    """Return pixels of exactly ``dtype`` as contiguous little-endian values; never a cast."""
    if pixels.dtype != dtype:
        raise CopyWriteError(f"a plane is {pixels.dtype}, not {dtype} as its header said")
    stored = dtype.newbyteorder("<") if dtype.itemsize > 1 else dtype
    return np.ascontiguousarray(pixels).astype(stored, copy=False)


def write_named_zarr(
    source: str | os.PathLike,
    target: str | os.PathLike,
    layout: CopyLayout,
    mapping: AxisMapping,
    *,
    dtype: np.dtype,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
) -> None:
    """Write ``source``, read under ``mapping``, to ``target`` as an OME-Zarr image.

    Parameters
    ----------
    source
        The TIFF image.
    target
        The folder to write (the caller's ``.partial`` folder); it must not exist.
    layout
        The copy's axes, shape and metadata (:func:`.copy_axes.copy_layout`).
    mapping
        The image's own confirmed mapping.
    dtype
        The pixel type the inspected header gives; a plane of another type is refused.
    memory_limit
        Working-memory limit per plane, as the run's.

    Raises
    ------
    PlaneReadError
        If the source cannot be read under ``mapping`` within the memory limit.
    CopyWriteError
        If a plane is not of ``dtype``.
    OSError
        If the store cannot be written.
    """
    dtype = np.dtype(dtype)
    array = _lay_out(Path(target), layout, dtype, layout.description)
    rows, cols = layout.plane_shape
    roles = [role.lower() for role in layout.axes[:-2]]
    row_nbytes = cols * dtype.itemsize
    for plane in iter_planes(source, mapping, memory_limit=memory_limit):
        position = tuple(int(plane.coordinates[role]) for role in roles)
        with open(_chunk_path(array, position), "wb") as stream:
            stream.truncate(rows * row_nbytes)
            for block in plane.blocks():
                pixels = _little(block.pixels, dtype)
                for r in range(pixels.shape[0]):
                    stream.seek((block.y + r) * row_nbytes + block.x * dtype.itemsize)
                    stream.write(memoryview(pixels[r]).cast("B"))


def write_projected_zarr(
    source: str | os.PathLike,
    target: str | os.PathLike,
    layout: CopyLayout,
    mapping: AxisMapping,
    details: ProjectionEntry,
    *,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
) -> None:
    """Write the Z projection of ``source`` to ``target`` as a 2-D OME-Zarr image.

    The planes are computed exactly as :func:`.ome_tiff_copy.write_projected_copy` computes
    them (the slices ``details.z_range`` of each (t, c), ascending, into one running plane);
    the store's ``morphobricks`` attribute says how the copy was made.

    Raises
    ------
    PlaneReadError, CopyWriteError, ProjectionRefused, OSError
        As :func:`.ome_tiff_copy.write_projected_copy`.
    """
    start, stop = details.z_range
    out = np.dtype(details.output_dtype)
    array = _lay_out(Path(target), layout, out, projection_description(details))
    roles = [role.lower() for role in layout.axes[:-2]]
    has_z = mapping.index_of("Z") is not None
    for position in np.ndindex(*layout.shape[:-2]):
        where = dict(zip(roles, position, strict=True))
        running = PlaneAccumulator(
            layout.plane_shape, details.source_dtype, details.method, stop - start
        )
        for z in range(start, stop):
            plane = read_plane(
                source,
                mapping,
                z=z if has_z else None,
                c=where.get("c"),
                t=where.get("t"),
                memory_limit=memory_limit,
            )
            if plane.pixels is None:
                raise CopyWriteError(f"a plane of {source} does not fit the memory limit")
            running.add(plane.pixels)
        pixels = _little(running.result(), out)
        _chunk_path(array, tuple(int(i) for i in position)).write_bytes(pixels.tobytes())


__all__ = [
    "BITS_NOT_WRITTEN",
    "NGFF_VERSION",
    "OME_TIFF",
    "OME_ZARR",
    "STORES",
    "ZARR_COPY_SUFFIX",
    "as_zarr_copy",
    "write_named_zarr",
    "write_projected_zarr",
    "zarr_copy_name",
]
