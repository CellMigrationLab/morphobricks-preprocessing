"""The layout of a projected copy: the converted image's axes without Z (issue #176).

A projected copy keeps every role of the confirmed mapping but Z (``ZCYX`` becomes ``CYX``,
``TZCYX`` becomes ``TCYX``), so the copy's own header names its axes and Z is absent. The
Z size is never written to the copy: it is handed back for the record's ``dropped_sizes``.
Everything else (X, Y and T sizes, channel names) follows :func:`.copy_axes.copy_layout`.
Nothing here reads pixels or files.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from .copy_axes import CopyLayout, copy_layout
from .recorded_sizes import RecordedSize

if TYPE_CHECKING:
    from .axes import AxisMapping
    from .metadata import TiffMetadata


@dataclass(frozen=True)
class ProjectedLayout:
    """How one image is written as a projected copy.

    Attributes
    ----------
    layout
        The copy's axes (no Z), shape, sizes and metadata.
    z_axis
        ``(letter, index)`` of the source header's axis the Z role is confirmed for, or None
        when the image has no Z role (it is one slice).
    z_size
        The number of slices of the source along Z (1 without a Z role).
    dropped_z
        The Z size the copy does not carry (value, unit, source ``header``), or None when
        the header gave none that applies to the confirmed Z role.
    """

    layout: CopyLayout
    z_axis: tuple[str, int] | None
    z_size: int
    dropped_z: RecordedSize | None


def projected_layout(meta: TiffMetadata, mapping: AxisMapping) -> ProjectedLayout:
    """Work out the projected copy of an image read under its confirmed roles.

    Parameters
    ----------
    meta
        The source image's header.
    mapping
        The image's own confirmed mapping.

    Returns
    -------
    ProjectedLayout

    Raises
    ------
    ValueError
        As :func:`.copy_axes.copy_layout`.
    """
    full = copy_layout(meta, mapping)
    z_dim = mapping.index_of("Z")
    z_axis = None if z_dim is None else (meta.axes[z_dim], z_dim)
    z_size = 1 if z_dim is None else int(meta.shape[z_dim])
    dropped = next((s for s in full.sizes if s.role == "Z" and s.value is not None), None)
    # The Z size is dropped on purpose, so it is not an "unknown size" of the copy.
    unknown = MappingProxyType({r: why for r, why in full.unknown.items() if r != "Z"})
    layout = replace(full, unknown=unknown)
    if "Z" in full.axes:
        k = full.axes.index("Z")
        layout = replace(
            layout,
            axes=full.axes[:k] + full.axes[k + 1 :],
            shape=full.shape[:k] + full.shape[k + 1 :],
            sizes=tuple(RecordedSize("Z") if s.role == "Z" else s for s in full.sizes),
        )
    return ProjectedLayout(layout, z_axis, z_size, dropped)
