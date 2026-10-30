"""Write OME-Zarr copies of the source images (OME-NGFF 0.4).

An optional part of a run: each analysed TIFF is copied to
``<output>/ome_zarr/<relative path>.ome.zarr`` so that later bricks and viewers
can read it lazily, chunk by chunk. The source images are never changed.

The copy is written plane by plane, so the image is never held in memory as a
whole. A plane too large for the memory limit is written block by block; its
smaller levels are then built from the stored level above, a region at a time,
so they equal those of a plane written whole. Axes follow the
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

import json
import logging
import math
import os
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import numpy as np
from morphobricks_normalization.dtypes import is_integer

from .axes import AxisMapping
from .physical_sizes import (  # noqa: F401 - moved there, still importable from here
    UNKNOWN_SCALE_KEY,
    PhysicalSizes,
    read_physical_sizes,
)
from .planes import DEFAULT_MEMORY_LIMIT, Plane, PlaneLayout
from .zarr_input import (  # noqa: F401 - re-exported for callers of this module
    ZarrUnavailableError,
    _zarr,
    _zarr_major,
    canonical_dtype,
    zarr_available,
)

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


COMPRESSIONS = {
    "lz4": "Blosc LZ4 (fast)",
    "zstd": "Blosc Zstandard (smaller, slower)",
    "none": "no compression",
}
"""Lossless compressions offered for converted images; pixel values never change."""

MIN_CHUNK_SIZE = 16
MAX_CHUNK_SIZE = 8192


@dataclass(frozen=True)
class ConversionSettings:
    """How TIFF images are written when they are converted to OME-Zarr.

    Attributes
    ----------
    chunk_size
        Side of the stored tiles in X and Y, in pixels (16 to 8,192). Along Z, C
        and T every chunk holds one plane.
    compression
        ``"lz4"`` (Blosc LZ4, default), ``"zstd"`` (Blosc Zstandard) or ``"none"``.
        All are lossless.
    compression_level
        1 (fastest) to 9 (smallest); ignored without compression.
    pyramid_levels
        Smaller levels added for viewers: None to halve until a plane fits 512
        pixels (default), 0 for none, or a number of levels (1 to 7).

    Raises
    ------
    ValueError
        If a setting is outside its allowed range.
    """

    chunk_size: int = CHUNK_SIDE
    compression: str = "lz4"
    compression_level: int = 5
    pyramid_levels: int | None = None

    def __post_init__(self) -> None:
        if not MIN_CHUNK_SIZE <= int(self.chunk_size) <= MAX_CHUNK_SIZE:
            raise ValueError(
                f"The chunk size must be between {MIN_CHUNK_SIZE} and {MAX_CHUNK_SIZE:,} "
                f"pixels; got {self.chunk_size}."
            )
        if self.compression not in COMPRESSIONS:
            raise ValueError(
                f"Unknown compression {self.compression!r}; choose one of {sorted(COMPRESSIONS)}."
            )
        if not 1 <= int(self.compression_level) <= 9:
            raise ValueError(
                f"The compression level must be between 1 and 9; got {self.compression_level}."
            )
        if self.pyramid_levels is not None and not 0 <= int(self.pyramid_levels) < MAX_LEVELS:
            raise ValueError(
                f"The number of pyramid levels must be between 0 and {MAX_LEVELS - 1}; "
                f"got {self.pyramid_levels}."
            )

    def describe(self) -> str:
        """Return a plain-language summary, e.g. for the run summary."""
        compression = COMPRESSIONS[self.compression].split(" (")[0]
        if self.compression != "none":
            compression += f" level {self.compression_level}"
        if self.pyramid_levels is None:
            pyramid = "smaller levels until a plane fits 512 px"
        elif self.pyramid_levels == 0:
            pyramid = "no smaller levels"
        else:
            pyramid = f"{self.pyramid_levels} smaller level(s)"
        side = self.chunk_size
        return f"chunks of {side} x {side} px, {compression}, {pyramid}"

    def to_dict(self) -> dict:
        """Return the settings as JSON-compatible data."""
        return {
            "chunk_size": self.chunk_size,
            "compression": self.compression,
            "compression_level": self.compression_level,
            "pyramid_levels": self.pyramid_levels,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> ConversionSettings:
        """Build settings from :meth:`to_dict` data; missing keys get the defaults."""
        data = data or {}
        levels = data.get("pyramid_levels")
        return cls(
            chunk_size=int(data.get("chunk_size", CHUNK_SIDE)),
            compression=str(data.get("compression", "lz4")),
            compression_level=int(data.get("compression_level", 5)),
            pyramid_levels=None if levels is None else int(levels),
        )


@dataclass(frozen=True)
class ConversionEstimate:
    """How much disk space converting images to OME-Zarr may take.

    Attributes
    ----------
    image_count
        TIFF images to convert.
    source_bytes
        Size of these TIFF files on disk.
    copy_bytes
        Uncompressed size of the copies, including the smaller levels. It is an
        upper estimate: compression usually makes the copies smaller.
    """

    image_count: int
    source_bytes: int
    copy_bytes: int


def estimate_conversion(
    experiment_root: str | os.PathLike,
    files: Iterable,
    settings: ConversionSettings | None = None,
) -> ConversionEstimate:
    """Estimate the disk space for converting the TIFF images among ``files``.

    Parameters
    ----------
    experiment_root
        The experiment folder.
    files
        Image headers (:class:`~morphobricks_preprocessing.metadata.TiffMetadata`);
        OME-Zarr images and unreadable files are left out, as they are not converted.
    settings
        The conversion settings; they decide the smaller levels.
    """
    settings = settings or ConversionSettings()
    root = Path(experiment_root)
    count = source = copy = 0
    for f in files:
        if not f.readable or f.is_ome_zarr or f.dtype is None or len(f.shape) < 2:
            continue
        count += 1
        try:
            source += (root / f.relative_path).stat().st_size
        except OSError:
            pass
        y, x = f.shape[-2], f.shape[-1]
        levels = pyramid_shapes((y, x), settings.pyramid_levels)
        factor = sum(ly * lx for ly, lx in levels) / max(y * x, 1)
        copy += int(math.prod(f.shape) * np.dtype(f.dtype).itemsize * factor)
    return ConversionEstimate(count, source, copy)


def free_space(path: str | os.PathLike) -> int | None:
    """Return the free space on the drive holding ``path`` (or its nearest existing parent).

    Returns None when it cannot be determined.
    """
    path = Path(path).resolve()
    for candidate in (path, *path.parents):
        if candidate.exists():
            try:
                return shutil.disk_usage(candidate).free
            except OSError:
                return None
    return None


def pyramid_shapes(
    plane_shape: tuple[int, int], levels: int | None = None
) -> list[tuple[int, int]]:
    """Return (Y, X) of every level: level 0, then halved planes.

    With ``levels`` None, planes are halved until they fit ``PYRAMID_TARGET``
    (at most ``MAX_LEVELS`` levels in all); otherwise ``levels`` smaller levels
    are added, stopping early once a plane is a single pixel.
    """
    shapes = [tuple(plane_shape)]
    while len(shapes) < MAX_LEVELS:
        if levels is None and max(shapes[-1]) <= PYRAMID_TARGET:
            break
        if levels is not None and (len(shapes) > levels or max(shapes[-1]) <= 1):
            break
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
    Planes read in blocks are written block by block.

    Attributes
    ----------
    path
        The ``.ome.zarr`` folder being written.
    roles
        Roles of the array axes, in OME-NGFF order (e.g. ``("C", "Z", "Y", "X")``).
    shape
        Shape of level 0.
    memory_limit
        Working memory allowed while the smaller levels of a plane read in
        blocks are built, in bytes.
    """

    path: Path
    roles: tuple[str, ...]
    shape: tuple[int, ...]
    dtype: np.dtype
    sizes: PhysicalSizes
    channel_names: tuple[str | None, ...] = ()
    name: str = ""
    settings: ConversionSettings = field(default_factory=ConversionSettings)
    memory_limit: int = DEFAULT_MEMORY_LIMIT
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
        settings: ConversionSettings | None = None,
        memory_limit: int = DEFAULT_MEMORY_LIMIT,
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
            settings=settings or ConversionSettings(),
            memory_limit=memory_limit,
        )
        writer._open()
        return writer

    @property
    def mapping(self) -> AxisMapping:
        """Axis mapping of the copy itself, for reading it back plane by plane."""
        return AxisMapping("".join(self.roles), self.roles)

    def write(self, plane: Plane) -> None:
        """Store one plane at its coordinates, at every level."""
        index = tuple(
            (plane.coordinates[r.lower()] or 0) for r in self.roles if r not in ("Y", "X")
        )
        if plane.in_blocks:
            self._write_blocks(index, plane)
            return
        pixels = plane.pixels.view(self.dtype)
        for level, array in enumerate(self._levels):
            if level:
                pixels = downsample(pixels)
            array[index] = pixels
        if "C" in self.roles:
            self._track_channel(plane.c or 0, plane.pixels)

    def _write_blocks(self, index: tuple[int, ...], plane: Plane) -> None:
        """Write a plane read in blocks, then build its smaller levels from the stored ones."""
        level0 = self._levels[0]
        for block in plane.blocks():
            rows, cols = block.pixels.shape
            region = (slice(block.y, block.y + rows), slice(block.x, block.x + cols))
            level0[(*index, *region)] = block.pixels.view(self.dtype)
            if "C" in self.roles:
                self._track_channel(plane.c or 0, block.pixels)
        for above, array in zip(self._levels, self._levels[1:], strict=False):
            height, width = above.shape[-2:]
            # Regions of even size at even positions halve exactly like the whole plane;
            # only the last row and column of the plane are repeated for odd sizes.
            side = self._region_side()
            for top in range(0, height, side):
                for left in range(0, width, side):
                    pixels = np.asarray(
                        above[(*index, slice(top, top + side), slice(left, left + side))]
                    )
                    smaller = downsample(pixels)
                    rows, cols = smaller.shape
                    target = (slice(top // 2, top // 2 + rows), slice(left // 2, left // 2 + cols))
                    array[(*index, *target)] = smaller

    def _region_side(self) -> int:
        """Side of the regions read to build a smaller level: even, within the memory limit."""
        side = 2 * self.settings.chunk_size
        # The region, its float64 copy and means, and the chunks read and written.
        chunk = self.settings.chunk_size**2 * self.dtype.itemsize
        while (
            side > 2
            and side * side * (2 * self.dtype.itemsize + 12) + 5 * chunk > self.memory_limit
        ):
            side = max(2, side // 4 * 2)
        return side

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
        # OME-NGFF 0.4 requires a scale for every axis, so an unknown size is written as 1;
        # say which, so that 1 is never read as a real size (issue #39).
        unknown = _unknown_scale(self.roles, self.sizes)
        if unknown:
            multiscale["metadata"][UNKNOWN_SCALE_KEY] = unknown
        if self.name:
            multiscale["name"] = self.name
        attrs = {"multiscales": [multiscale]}
        if "C" in self.roles:
            attrs["omero"] = self._omero()
        return attrs

    def _open(self) -> None:
        self._group = _create_group(self.path)
        self._levels = []
        side = self.settings.chunk_size
        compressor = _compressor(self.settings)
        for level, (y, x) in enumerate(
            pyramid_shapes(self.shape[-2:], self.settings.pyramid_levels)
        ):
            shape = (*self.shape[:-2], y, x)
            chunks = (*(1,) * (len(shape) - 2), min(y, side), min(x, side))
            self._levels.append(
                _create_array(self._group, str(level), shape, chunks, self.dtype, compressor)
            )

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
            channel = {
                "active": True,
                "color": "FFFFFF",
                "window": {"min": low, "max": high, "start": low, "end": high},
            }
            # Only names from the source's metadata are written. An unnamed channel gets
            # no label rather than a made-up one, so the copy reads back unnamed, like its
            # source, and never fails the exact channel-name check (issue #37).
            label = _channel_label(self.channel_names, c)
            if label is not None:
                channel["label"] = label
            channels.append(channel)
        return {"version": NGFF_VERSION, "channels": channels}


def _channel_label(names: Sequence[str | None], channel: int) -> str | None:
    """Return the name of ``channel`` in ``names``; None when it is unnamed."""
    return (names[channel] if channel < len(names) else None) or None


def is_copy_path(output_folder: str | os.PathLike, relative_path: str) -> bool:
    """Return True if ``relative_path`` names an OME-Zarr copy inside ``<output>/ome_zarr``.

    Used before moving or removing a copy recorded in a manifest, so that a damaged
    manifest cannot point outside this brick's own copies.
    """
    output = Path(output_folder)
    base = (output / OME_ZARR_FOLDER).resolve()
    target = (output / relative_path).resolve()
    return base in target.parents and target.name.endswith(OME_ZARR_SUFFIX)


def _unknown_scale(roles: Iterable[str], sizes: PhysicalSizes) -> list[str]:
    """Return the axes (``"x"``, ...) whose size is not known, so their scale 1 is a placeholder."""
    return [role.lower() for role in roles if role in "XYZT" and not sizes.known(role)]


def copy_units_current(copy: str | os.PathLike, sizes: PhysicalSizes) -> bool:
    """Return True if a copy's axis units are those its source's sizes give now.

    Copies written before each axis had its own unit (plan §12 question 24) may
    carry X's unit on Y or Z, and copies written before the unknown sizes were marked
    (``unknown_scale``, issue #39) read as size 1; such copies are written again on the next
    run. Only
    the copy's metadata file is read. A copy whose metadata cannot be read counts
    as out of date, so it is written again rather than trusted.

    Parameters
    ----------
    copy
        The copy's folder (``<output>/ome_zarr/....ome.zarr``).
    sizes
        The source image's physical sizes, as :func:`read_physical_sizes` gives them.
    """
    try:
        attrs = json.loads((Path(copy) / ".zattrs").read_text(encoding="utf-8"))
        axes = attrs["multiscales"][0]["axes"]
        roles = [str(axis["name"]).upper() for axis in axes]
        marked = attrs["multiscales"][0].get("metadata", {}).get(UNKNOWN_SCALE_KEY, [])
        return marked == _unknown_scale(roles, sizes) and all(
            axis.get("unit") == sizes.unit(role) for axis, role in zip(axes, roles, strict=True)
        )
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        logger.debug("Cannot read the axis units of the copy %s", copy, exc_info=True)
        return False


def copy_channels_current(copy: str | os.PathLike, channel_names: Sequence[str | None]) -> bool:
    """Return True if a copy's channel labels are exactly the names its source gives now.

    Copies written before unnamed channels were left without a label carry the
    placeholder ``Channel <i>``; such copies are written again on the next run, so the
    copy never names a channel its source does not. Only the copy's metadata file is
    read. A copy whose metadata cannot be read counts as out of date.

    Parameters
    ----------
    copy
        The copy's folder (``<output>/ome_zarr/....ome.zarr``).
    channel_names
        The source image's channel names, as its header gives them (None when unnamed).
    """
    try:
        attrs = json.loads((Path(copy) / ".zattrs").read_text(encoding="utf-8"))
        if "omero" not in attrs:
            return True
        channels = attrs["omero"]["channels"]
        return all(
            channel.get("label") == _channel_label(channel_names, c)
            for c, channel in enumerate(channels)
        )
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        logger.debug("Cannot read the channel labels of the copy %s", copy, exc_info=True)
        return False


# zarr 2 / 3 ---------------------------------------------------------------------------


def _create_group(path: Path):
    zarr = _zarr()
    if _zarr_major() >= 3:
        return zarr.open_group(str(path), mode="w", zarr_format=2)
    return zarr.open_group(str(path), mode="w")


def _compressor(settings: ConversionSettings):
    """Return the numcodecs compressor for ``settings``; None means no compression."""
    if settings.compression == "none":
        return None
    import numcodecs  # installed with zarr

    return numcodecs.Blosc(
        cname=settings.compression,
        clevel=settings.compression_level,
        shuffle=numcodecs.Blosc.SHUFFLE,
    )


def _create_array(group, name: str, shape, chunks, dtype, compressor):
    if _zarr_major() >= 3:
        return group.create_array(
            name,
            shape=shape,
            chunks=chunks,
            dtype=dtype,
            fill_value=0,
            compressors=compressor,
            chunk_key_encoding={"name": "v2", "separator": "/"},
        )
    return group.create_dataset(
        name,
        shape=shape,
        chunks=chunks,
        dtype=dtype,
        fill_value=0,
        compressor=compressor,
        dimension_separator="/",
    )
