"""Write OME-Zarr copies of the source images (OME-NGFF 0.4).

An optional part of a run: each analysed TIFF is copied to
``<output>/ome_zarr/<relative path>.ome.zarr`` so that later bricks and viewers
can read it lazily, chunk by chunk. The source images are never changed.

The copy is written plane by plane while the histograms are computed, so the
image is read only once and never held in memory as a whole. Axes follow the
OME-NGFF order ``t, c, z, y, x`` with the roles of the confirmed axis mapping
(axes without a role are dropped; they have length 1). Pixel values are copied
unchanged at level 0; smaller levels for viewers are 2 x 2 averages, rounded to
the pixel type. Physical pixel sizes and channel names are taken from OME or
ImageJ metadata when present.

Needs the ``zarr`` extra (``pip install "morphobricks-preprocessing[zarr]"``).
Zarr 2.18 and 3.x are supported; the files are always written in Zarr format 2,
which OME-NGFF 0.4 uses.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

import numpy as np
import tifffile

from .axes import AxisMapping
from .dtypes import is_integer
from .planes import Plane, PlaneLayout

logger = logging.getLogger(__name__)

NGFF_VERSION = "0.4"
OME_ZARR_FOLDER = "ome_zarr"
OME_ZARR_SUFFIX = ".ome.zarr"

# OME-NGFF 0.4 axis order, and the axis type of each role.
AXIS_ORDER = ("T", "C", "Z", "Y", "X")
_AXIS_TYPES = {"T": "time", "C": "channel", "Z": "space", "Y": "space", "X": "space"}

# Levels are added for viewers until the plane fits in this many pixels per side.
PYRAMID_TARGET = 512
MAX_LEVELS = 8
CHUNK_SIDE = 1024

# Units in OME and ImageJ metadata, and their OME-NGFF (UDUNITS-2) names.
_SPACE_UNITS = {
    "pm": "picometer",
    "nm": "nanometer",
    "µm": "micrometer",
    "μm": "micrometer",
    "um": "micrometer",
    "micron": "micrometer",
    "microns": "micrometer",
    "micrometer": "micrometer",
    "mm": "millimeter",
    "cm": "centimeter",
    "m": "meter",
}
_TIME_UNITS = {
    "ns": "nanosecond",
    "µs": "microsecond",
    "μs": "microsecond",
    "us": "microsecond",
    "ms": "millisecond",
    "s": "second",
    "sec": "second",
    "min": "minute",
    "h": "hour",
}


class ZarrUnavailableError(ImportError):
    """Raised when OME-Zarr copies are requested but ``zarr`` is not installed."""


def zarr_available() -> bool:
    """Return True if the ``zarr`` package can be imported."""
    try:
        _zarr()
    except ZarrUnavailableError:
        return False
    return True


def ome_zarr_relative_path(source_relative_path: str) -> str:
    """Return where the copy of an image goes, relative to the output folder.

    ``"ctrl/rep1/img_01.tif"`` becomes ``"ome_zarr/ctrl/rep1/img_01.ome.zarr"``;
    an ``.ome.tif`` or ``.ome.tiff`` file loses its whole suffix.
    """
    path = PurePosixPath(source_relative_path)
    name = path.name
    for suffix in (".ome.tiff", ".ome.tif", ".tiff", ".tif"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    return (PurePosixPath(OME_ZARR_FOLDER) / path.parent / (name + OME_ZARR_SUFFIX)).as_posix()


@dataclass(frozen=True)
class PhysicalSizes:
    """Pixel sizes from metadata; None where unknown.

    Attributes
    ----------
    x, y, z
        Pixel size and z-step in ``space_unit``.
    t
        Time between frames in ``time_unit``.
    space_unit, time_unit
        OME-NGFF unit names, e.g. ``"micrometer"`` and ``"second"``.
    """

    x: float | None = None
    y: float | None = None
    z: float | None = None
    t: float | None = None
    space_unit: str | None = None
    time_unit: str | None = None

    def scale(self, role: str) -> float:
        """Return the size of one step along ``role`` (1 when unknown)."""
        value = {"X": self.x, "Y": self.y, "Z": self.z, "T": self.t}.get(role)
        return 1.0 if value is None or not value > 0 else float(value)

    def unit(self, role: str) -> str | None:
        """Return the unit of ``role``, or None when unknown or for channels."""
        if role == "T":
            return self.time_unit if self.t is not None else None
        if role == "C":
            return None
        sizes = {"X": self.x, "Y": self.y, "Z": self.z}
        return self.space_unit if sizes[role] is not None else None


def read_physical_sizes(path: str | os.PathLike) -> PhysicalSizes:
    """Read pixel sizes from OME or ImageJ metadata, without reading pixels.

    Returns all-None sizes when the file has no such metadata or it cannot be
    parsed; sizes are never guessed.
    """
    try:
        with tifffile.TiffFile(path) as tif:
            if tif.is_ome and tif.ome_metadata:
                return _ome_sizes(tif.ome_metadata)
            if tif.is_imagej:
                return _imagej_sizes(tif)
    except Exception:  # metadata is optional; a damaged header is reported elsewhere
        logger.debug("Cannot read physical sizes of %s", path, exc_info=True)
    return PhysicalSizes()


def canonical_dtype(dtype: np.dtype) -> np.dtype:
    """Return the standard NumPy dtype with the same name, e.g. ``int64`` for ``longlong``.

    On Linux and macOS, NumPy has two 64-bit integer types (``long`` and ``long
    long``) that compare equal but are different classes; tifffile may return
    either, and zarr 3 only recognises the standard one.
    """
    return np.dtype(np.dtype(dtype).name)


def pyramid_shapes(plane_shape: tuple[int, int]) -> list[tuple[int, int]]:
    """Return (Y, X) of every level: level 0, then halved until it fits ``PYRAMID_TARGET``."""
    shapes = [tuple(plane_shape)]
    while max(shapes[-1]) > PYRAMID_TARGET and len(shapes) < MAX_LEVELS:
        y, x = shapes[-1]
        shapes.append((math.ceil(y / 2), math.ceil(x / 2)))
    return shapes


def downsample(pixels: np.ndarray) -> np.ndarray:
    """Average 2 x 2 blocks (edge pixels repeated for odd sizes), keeping the dtype.

    Integer results are rounded and stay within the type's range; NaN and
    infinity propagate as in a plain average.
    """
    y, x = pixels.shape
    padded = np.pad(pixels, ((0, y % 2), (0, x % 2)), mode="edge").astype(np.float64)
    means = padded.reshape(padded.shape[0] // 2, 2, padded.shape[1] // 2, 2).mean(axis=(1, 3))
    if is_integer(pixels.dtype):
        info = np.iinfo(pixels.dtype)
        return np.clip(np.rint(means), info.min, info.max).astype(pixels.dtype)
    return means.astype(pixels.dtype)


@dataclass
class OmeZarrWriter:
    """Writes one image as OME-Zarr, one plane at a time.

    Use :meth:`create`, then :meth:`write` every plane, then :meth:`finish`.

    Attributes
    ----------
    path
        The ``.ome.zarr`` folder being written.
    roles
        Roles of the array axes, in OME-NGFF order (e.g. ``("C", "Z", "Y", "X")``).
    shape
        Shape of level 0.
    """

    path: Path
    roles: tuple[str, ...]
    shape: tuple[int, ...]
    dtype: np.dtype
    sizes: PhysicalSizes
    channel_names: tuple[str | None, ...] = ()
    name: str = ""
    _levels: list = field(default_factory=list, repr=False)
    _channel_ranges: dict = field(default_factory=dict, repr=False)

    @classmethod
    def create(
        cls,
        path: str | os.PathLike,
        mapping: AxisMapping,
        layout: PlaneLayout,
        *,
        sizes: PhysicalSizes | None = None,
        channel_names: Sequence[str | None] = (),
        name: str = "",
    ) -> OmeZarrWriter:
        """Create the empty arrays of an image with ``mapping`` and ``layout``."""
        roles = tuple(r for r in AXIS_ORDER if r in ("Y", "X") or r in mapping.plane_roles)
        lengths = {"Y": layout.plane_shape[0], "X": layout.plane_shape[1]}
        for role in mapping.plane_roles:
            dim = mapping.index_of(role)
            lengths[role] = 1 if dim is None else layout.shape[dim]
        writer = cls(
            path=Path(path),
            roles=roles,
            shape=tuple(lengths[r] for r in roles),
            dtype=canonical_dtype(layout.dtype),
            sizes=sizes or PhysicalSizes(),
            channel_names=tuple(channel_names),
            name=name,
        )
        writer._open()
        return writer

    def write(self, plane: Plane) -> None:
        """Store one plane at its coordinates, at every level."""
        index = tuple(
            (plane.coordinates[r.lower()] or 0) for r in self.roles if r not in ("Y", "X")
        )
        pixels = plane.pixels.view(self.dtype)
        for level, array in enumerate(self._levels):
            if level:
                pixels = downsample(pixels)
            array[index] = pixels
        if "C" in self.roles:
            self._track_channel(plane.c or 0, plane.pixels)

    def finish(self) -> Path:
        """Write the OME-NGFF metadata; return the image folder."""
        self._group.attrs.update(self.attributes())
        return self.path

    def attributes(self) -> dict:
        """Return the OME-NGFF 0.4 ``multiscales`` (and ``omero`` for channels) metadata."""
        axes = []
        for role in self.roles:
            axis = {"name": role.lower(), "type": _AXIS_TYPES[role]}
            unit = self.sizes.unit(role)
            if unit:
                axis["unit"] = unit
            axes.append(axis)
        datasets = []
        for level in range(len(self._levels)):
            factor = 2**level
            scale = [
                self.sizes.scale(r) * (factor if r in ("Y", "X") else 1) if r != "C" else 1.0
                for r in self.roles
            ]
            datasets.append(
                {
                    "path": str(level),
                    "coordinateTransformations": [{"type": "scale", "scale": scale}],
                }
            )
        multiscale = {
            "version": NGFF_VERSION,
            "axes": axes,
            "datasets": datasets,
            "type": "mean",
            "metadata": {"description": "2 x 2 block mean; level 0 holds the source values"},
        }
        if self.name:
            multiscale["name"] = self.name
        attrs = {"multiscales": [multiscale]}
        if "C" in self.roles:
            attrs["omero"] = self._omero()
        return attrs

    def _open(self) -> None:
        self._group = _create_group(self.path)
        self._levels = []
        for level, (y, x) in enumerate(pyramid_shapes(self.shape[-2:])):
            shape = (*self.shape[:-2], y, x)
            chunks = (*(1,) * (len(shape) - 2), min(y, CHUNK_SIDE), min(x, CHUNK_SIDE))
            self._levels.append(_create_array(self._group, str(level), shape, chunks, self.dtype))

    def _track_channel(self, channel: int, pixels: np.ndarray) -> None:
        finite = pixels[np.isfinite(pixels)] if pixels.dtype.kind == "f" else pixels
        if finite.size == 0:
            return
        low, high = float(finite.min()), float(finite.max())
        old = self._channel_ranges.get(channel)
        self._channel_ranges[channel] = (
            (low, high)
            if old is None
            else (
                min(old[0], low),
                max(old[1], high),
            )
        )

    def _omero(self) -> dict:
        channels = []
        for c in range(self.shape[self.roles.index("C")]):
            low, high = self._channel_ranges.get(c, (0.0, 1.0))
            label = self.channel_names[c] if c < len(self.channel_names) else None
            channels.append(
                {
                    "label": label or f"Channel {c}",
                    "active": True,
                    "color": "FFFFFF",
                    "window": {"min": low, "max": high, "start": low, "end": high},
                }
            )
        return {"version": NGFF_VERSION, "channels": channels}


def is_copy_path(output_folder: str | os.PathLike, relative_path: str) -> bool:
    """Return True if ``relative_path`` names an OME-Zarr copy inside ``<output>/ome_zarr``.

    Used before moving or removing a copy recorded in a manifest, so that a damaged
    manifest cannot point outside this brick's own copies.
    """
    output = Path(output_folder)
    base = (output / OME_ZARR_FOLDER).resolve()
    target = (output / relative_path).resolve()
    return base in target.parents and target.name.endswith(OME_ZARR_SUFFIX)


# zarr 2 / 3 ---------------------------------------------------------------------------


def _zarr():
    try:
        import zarr
    except ImportError as exc:
        raise ZarrUnavailableError(
            "Writing OME-Zarr copies needs the zarr package. Install it with: "
            'pip install "morphobricks-preprocessing[zarr]"'
        ) from exc
    return zarr


def _zarr_major() -> int:
    return int(_zarr().__version__.split(".")[0])


def _create_group(path: Path):
    zarr = _zarr()
    if _zarr_major() >= 3:
        return zarr.open_group(str(path), mode="w", zarr_format=2)
    return zarr.open_group(str(path), mode="w")


def _create_array(group, name: str, shape, chunks, dtype):
    if _zarr_major() >= 3:
        return group.create_array(
            name,
            shape=shape,
            chunks=chunks,
            dtype=dtype,
            fill_value=0,
            chunk_key_encoding={"name": "v2", "separator": "/"},
        )
    return group.create_dataset(
        name, shape=shape, chunks=chunks, dtype=dtype, fill_value=0, dimension_separator="/"
    )


# Metadata -----------------------------------------------------------------------------


def _ome_sizes(xml: str) -> PhysicalSizes:
    root = ElementTree.fromstring(xml)
    pixels = next((e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "Pixels"), None)
    if pixels is None:
        return PhysicalSizes()
    unit = _SPACE_UNITS.get(pixels.get("PhysicalSizeXUnit", "µm"))
    time_unit = _TIME_UNITS.get(pixels.get("TimeIncrementUnit", "s"))
    return PhysicalSizes(
        x=_number(pixels.get("PhysicalSizeX")),
        y=_number(pixels.get("PhysicalSizeY")),
        z=_number(pixels.get("PhysicalSizeZ")),
        t=_number(pixels.get("TimeIncrement")) if time_unit else None,
        space_unit=unit,
        time_unit=time_unit,
    )


def _imagej_sizes(tif: tifffile.TiffFile) -> PhysicalSizes:
    meta = tif.imagej_metadata or {}
    unit = _SPACE_UNITS.get(str(meta.get("unit", "")).strip().lower())
    page = tif.pages.first
    x = _resolution(page.tags.get("XResolution"))
    y = _resolution(page.tags.get("YResolution"))
    time_unit = _TIME_UNITS.get(str(meta.get("tunit", "sec")).strip().lower())
    if unit is None:
        # Without a unit the resolution tags are a default (often 1 pixel per inch).
        x = y = None
    return PhysicalSizes(
        x=x,
        y=y,
        z=_number(meta.get("spacing")) if unit else None,
        t=_number(meta.get("finterval")) if time_unit else None,
        space_unit=unit,
        time_unit=time_unit,
    )


def _resolution(tag) -> float | None:
    if tag is None:
        return None
    try:
        numerator, denominator = tag.value
        return denominator / numerator if numerator else None
    except (TypeError, ValueError):
        return None


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None
