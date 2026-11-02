"""The axes, shape and metadata of an image's named-axis copy (design v0.4 §5).

A named-axis copy is an OME-TIFF holding the planes of its source read under the roles the
user confirmed (:func:`.planes.iter_planes`), with those roles written as its axes, so the
copy's own header names every axis. :func:`copy_layout` works it out from the header alone:

- **Axes.** Every axis with a role, in the source's order, then Y and X (rows, then
  columns): ``ZCYX`` for a stack confirmed ``Z C Y X``, ``ZYX`` for a ``ZYX`` header whose
  Y and X are confirmed the other way round. An axis without a role (length 1) is left out,
  and so is an axis the image lacks (``missing_as_singleton``): nothing is added.
- **Pixel sizes follow the roles; a relabelled axis gets an unknown scale, never a guessed
  one.** A size the header gives is carried only to the role the header names it for, and
  only when the user confirmed that role for that same axis. An axis the user gives
  another role than the header's letter is relabelled: its size is not written, and the
  reason is kept for the conversion record. So a Y/X swap never exchanges the sizes of Y
  and X (both become unknown), and a z-step is never written as a time step. A plain
  stack's positional Y and X keep the header's Y and X sizes when confirmed Y before X.
- **Channel names** are carried when the channel axis keeps its role and every channel is
  named; the significant bits are carried as they are.

Nothing here reads pixels or files.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from .recorded_sizes import SIZE_ROLES, RecordedSize, header_sizes

if TYPE_CHECKING:
    from .axes import AxisMapping
    from .metadata import TiffMetadata

OME_UNITS = MappingProxyType(
    {
        "angstrom": "Å",
        "picometer": "pm",
        "nanometer": "nm",
        "micrometer": "µm",
        "millimeter": "mm",
        "centimeter": "cm",
        "meter": "m",
        "nanosecond": "ns",
        "microsecond": "µs",
        "millisecond": "ms",
        "second": "s",
        "minute": "min",
        "hour": "h",
    }
)
"""OME-XML unit symbols of the OME-NGFF unit names a size is recorded with."""

_OME_SIZE_KEYS = {
    "X": ("PhysicalSizeX", "PhysicalSizeXUnit"),
    "Y": ("PhysicalSizeY", "PhysicalSizeYUnit"),
    "Z": ("PhysicalSizeZ", "PhysicalSizeZUnit"),
    "T": ("TimeIncrement", "TimeIncrementUnit"),
}


@dataclass(frozen=True)
class CopyLayout:
    """How one image is written as a named-axis copy.

    Attributes
    ----------
    axes
        The copy's axes, one role letter each, Y and X last (``"ZCYX"``).
    shape
        The copy's shape, one length per axis.
    sizes
        The copy's pixel sizes by role (:data:`.recorded_sizes.SIZE_ROLES`), source
        ``"header"``; unknown ones have no value.
    unknown
        ``{role: reason}`` for each size the source header gives that is not written.
    channel_names
        The channel names written, or ``()`` for none.
    significant_bits
        The significant bits written, or None.
    """

    axes: str
    shape: tuple[int, ...]
    sizes: tuple[RecordedSize, ...] = ()
    unknown: Mapping[str, str] = field(default_factory=dict)
    channel_names: tuple[str, ...] = ()
    significant_bits: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "unknown", MappingProxyType(dict(self.unknown)))

    @property
    def plane_shape(self) -> tuple[int, int]:
        """The (Y, X) shape of one plane."""
        return self.shape[-2], self.shape[-1]

    @property
    def plane_count(self) -> int:
        """The number of 2D planes (TIFF pages) of the copy."""
        count = 1
        for length in self.shape[:-2]:
            count *= length
        return count

    def plane_index(self, coordinates: Mapping[str, int | None]) -> int:
        """Return the page number of the plane at these coordinates in the copy.

        Parameters
        ----------
        coordinates
            ``{"z": ..., "c": ..., "t": ...}``, as :attr:`.planes.Plane.coordinates`.

        Returns
        -------
        int
            The zero-based page of the copy, in its axes' order.
        """
        index = 0
        for role, length in zip(self.axes[:-2], self.shape[:-2], strict=True):
            index = index * length + int(coordinates[role.lower()])
        return index

    def ome_metadata(self) -> dict:
        """Return the metadata ``tifffile`` writes into the copy's OME-XML."""
        metadata: dict = {"axes": self.axes}
        for size in self.sizes:
            if size.value is not None:
                value_key, unit_key = _OME_SIZE_KEYS[size.role]
                metadata[value_key] = size.value
                metadata[unit_key] = OME_UNITS[size.unit]
        if self.channel_names:
            metadata["Channel"] = {"Name": list(self.channel_names)}
        if self.significant_bits is not None:
            metadata["SignificantBits"] = self.significant_bits
        return metadata


def copy_layout(meta: TiffMetadata, mapping: AxisMapping) -> CopyLayout:
    """Work out the named-axis copy of an image read under its confirmed roles.

    Parameters
    ----------
    meta
        The source image's header.
    mapping
        The image's own confirmed mapping (:attr:`.axes.AxisReview.file_mappings`).

    Returns
    -------
    CopyLayout

    Raises
    ------
    ValueError
        If the mapping does not fit the header or has no Y or X.
    """
    if len(mapping.roles) != len(meta.shape) or mapping.axes != meta.axes:
        raise ValueError(f"the axis mapping {mapping.axes} does not fit axes {meta.axes}")
    y, x = mapping.index_of("Y"), mapping.index_of("X")
    if y is None or x is None:
        raise ValueError("the axis mapping needs one Y and one X axis")
    leading = [d for d, role in enumerate(mapping.roles) if role is not None and role not in "YX"]
    dims = [*leading, y, x]
    axes = "".join(mapping.roles[d] for d in dims)
    sizes, unknown = _carried_sizes(meta, mapping, axes)
    return CopyLayout(
        axes=axes,
        shape=tuple(meta.shape[d] for d in dims),
        sizes=sizes,
        unknown=unknown,
        channel_names=_channel_names(meta, mapping),
        significant_bits=meta.significant_bits,
    )


def _carried_sizes(
    meta: TiffMetadata, mapping: AxisMapping, axes: str
) -> tuple[tuple[RecordedSize, ...], dict[str, str]]:
    """Return the sizes written for each role and why the header's others are not."""
    header = {size.role: size for size in header_sizes(meta.physical_sizes)}
    sizes, unknown = [], {}
    for role in SIZE_ROLES:
        found = header[role]
        reason = _not_carried(meta, mapping, axes, role, found)
        if found.value is None or reason is not None:
            sizes.append(RecordedSize(role))
            if found.value is not None:
                unknown[role] = reason
            continue
        sizes.append(found)
    return tuple(sizes), unknown


def _not_carried(
    meta: TiffMetadata, mapping: AxisMapping, axes: str, role: str, found: RecordedSize
) -> str | None:
    """Return why the header's size for ``role`` is not written, or None to write it."""
    if role not in axes:
        return "the copy has no such axis"
    letter = meta.axes[mapping.index_of(role)]
    if letter != role:
        return (
            f"relabelled: the header names this axis {letter}, so its size is not taken for "
            f"{role} (unknown scale)"
        )
    if found.unit not in OME_UNITS:
        return f"its unit {found.unit!r} cannot be written in OME-XML"
    return None


def _channel_names(meta: TiffMetadata, mapping: AxisMapping) -> tuple[str, ...]:
    """Return the channel names when the C axis keeps its role and every channel is named."""
    c = mapping.index_of("C")
    names = meta.channel_names
    if c is None or len(names) != meta.shape[c]:
        return ()
    if meta.axes[c] != "C" and not (meta.axes[c] == "S" and "C" not in meta.axes):
        return ()  # relabelled: the names are of another axis
    if any(name is None for name in names):
        return ()
    return tuple(names)
