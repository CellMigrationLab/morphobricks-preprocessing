"""What the conversion record says about one projected copy (issue #176, record version 3).

A projected copy is a 2-D image made from a Z-stack by :mod:`.projection`. Its record entry
carries a :class:`ProjectionEntry`: the source, the Z axis it had, the slices used, the
method, the types and the arithmetic, the Z size that was dropped and why the significant
bits were. Everything here is pure data with strict reading: a field that disagrees with the
others, or with the dtype table of :func:`.projection.projection_dtype`, is refused, so a
record edited by hand is never trusted.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

import numpy as np

from .projection import METHODS, Projection, projection_dtype
from .recorded_sizes import SOURCES, RecordedSize

NAN_POLICY = "propagate"
"""The only NaN rule: a NaN in a slice makes that pixel NaN (no ``nan*`` function)."""

ROUNDINGS = ("none", "float64, one division", "float64 addition, z ascending")
"""The roundings a projection can record (``none`` for a maximum and an exact integer sum)."""

_ENTRY_KEYS = frozenset(
    {
        "source_relative_path",
        "source_content_sha256",
        "source_z_axis",
        "source_z_size",
        "z_range",
        "method",
        "source_dtype",
        "output_dtype",
        "dtype_rule",
        "rounding",
        "nan_policy",
        "dropped_sizes",
        "significant_bits_dropped",
    }
)


def projection_to_data(projection: Projection) -> dict:
    """Return a requested projection as the record-level JSON data."""
    z_range = None if projection.z_range is None else list(projection.z_range)
    return {"method": projection.method, "z_range": z_range}


def projection_from_data(data: object) -> Projection:
    """Read the record-level ``projection`` object.

    Raises
    ------
    ValueError
        If it is not ``{"method", "z_range"}`` with a valid method and range.
    """
    if not isinstance(data, Mapping) or set(data) != {"method", "z_range"}:
        raise ValueError(f"the projection {data!r} is not {{method, z_range}}")
    z_range = data["z_range"]
    if z_range is not None:
        if not isinstance(z_range, list):
            raise ValueError(f"the slice range {z_range!r} is not a list of two numbers")
        z_range = tuple(z_range)
    return Projection(data["method"], z_range)


@dataclass(frozen=True)
class ProjectionEntry:
    """How one image was projected, as the record lists it.

    Attributes
    ----------
    source_relative_path
        The source image, relative to the experiment root (an OME-Zarr source keeps its
        ``.zarr`` name; the copy is ``.ome.tif``).
    source_content_sha256
        The SHA-256 of the source's content as read while writing (None before it is
        written).
    source_z_axis
        ``(letter, index)`` of the source header's Z axis, or None for an image without
        one (a single slice).
    source_z_size
        The number of slices the source has.
    z_range
        ``(start, stop)``, the slices actually used (zero-based, stop excluded).
    method
        ``"max"``, ``"mean"`` or ``"sum"``.
    source_dtype, output_dtype
        The pixel types (numpy names) read and written.
    dtype_rule
        One line saying how the output type was decided.
    rounding
        One of :data:`ROUNDINGS`.
    nan_policy
        :data:`NAN_POLICY`.
    dropped_sizes
        ``{"Z": size}``: the Z size the copy does not carry (value, unit and source), or
        None when the source gave none.
    significant_bits_dropped
        Why the source's significant bits are not kept, or None when they are.
    """

    source_relative_path: str
    source_content_sha256: str | None
    source_z_axis: tuple[str, int] | None
    source_z_size: int
    z_range: tuple[int, int]
    method: str
    source_dtype: str
    output_dtype: str
    dtype_rule: str
    rounding: str
    nan_policy: str = NAN_POLICY
    dropped_sizes: Mapping[str, RecordedSize | None] = field(default_factory=dict)
    significant_bits_dropped: str | None = None

    def __post_init__(self) -> None:
        """Refuse an entry that contradicts itself or the dtype table."""
        _check_shape(self)
        slices = self.z_range[1] - self.z_range[0]
        rule = projection_dtype(self.source_dtype, self.method, slices)
        if self.output_dtype != rule.output_dtype.name or self.rounding != rule.rounding:
            raise ValueError(
                f"{self.source_relative_path!r}: a {self.method} of {slices} {self.source_dtype} "
                f"slices is {rule.output_dtype.name} with rounding {rule.rounding!r}, not "
                f"{self.output_dtype} with {self.rounding!r}"
            )

    def with_content(self, sha256: str) -> ProjectionEntry:
        """Return the entry with the source content SHA-256 computed while writing."""
        return replace(self, source_content_sha256=sha256)

    def plan_key(self) -> tuple:
        """Return what decides how the copy is made (the content SHA-256 is a result)."""
        return (
            self.source_relative_path,
            self.source_z_axis,
            self.source_z_size,
            self.z_range,
            self.method,
            self.source_dtype,
            self.output_dtype,
            self.dtype_rule,
            self.rounding,
            self.nan_policy,
            tuple(
                sorted(
                    (r, _size_data(s) and tuple(_size_data(s).values()))
                    for r, s in self.dropped_sizes.items()
                )
            ),
            self.significant_bits_dropped,
        )

    def to_dict(self) -> dict:
        """Return the entry as JSON-compatible data."""
        axis = self.source_z_axis
        axis_data = None if axis is None else {"letter": axis[0], "index": axis[1]}
        return {
            "source_relative_path": self.source_relative_path,
            "source_content_sha256": self.source_content_sha256,
            "source_z_axis": axis_data,
            "source_z_size": self.source_z_size,
            "z_range": list(self.z_range),
            "method": self.method,
            "source_dtype": self.source_dtype,
            "output_dtype": self.output_dtype,
            "dtype_rule": self.dtype_rule,
            "rounding": self.rounding,
            "nan_policy": self.nan_policy,
            "dropped_sizes": {r: _size_data(s) for r, s in sorted(self.dropped_sizes.items())},
            "significant_bits_dropped": self.significant_bits_dropped,
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> ProjectionEntry:
        """Read an entry written by :meth:`to_dict`; it must name its source SHA-256.

        Raises
        ------
        ValueError
            For missing or extra keys, wrong types, or values that disagree.
        """
        if not isinstance(data, Mapping) or set(data) != _ENTRY_KEYS:
            raise ValueError(f"a projection entry needs exactly the keys {sorted(_ENTRY_KEYS)}")
        if not isinstance(data["source_content_sha256"], str):
            raise ValueError(f"{data['source_relative_path']!r} has no source SHA-256")
        z_range = data["z_range"]
        if not isinstance(z_range, list):
            raise ValueError(f"the slice range {z_range!r} is not a list of two numbers")
        dropped = data["dropped_sizes"]
        if not isinstance(dropped, Mapping):
            raise ValueError(f"dropped_sizes {dropped!r} is not an object")
        return cls(
            source_relative_path=_text(data["source_relative_path"]),
            source_content_sha256=data["source_content_sha256"],
            source_z_axis=_axis(data["source_z_axis"]),
            source_z_size=data["source_z_size"],
            z_range=tuple(z_range),
            method=_text(data["method"]),
            source_dtype=_text(data["source_dtype"]),
            output_dtype=_text(data["output_dtype"]),
            dtype_rule=_text(data["dtype_rule"]),
            rounding=_text(data["rounding"]),
            nan_policy=_text(data["nan_policy"]),
            dropped_sizes={_text(r): _size(r, s) for r, s in dropped.items()},
            significant_bits_dropped=(
                None
                if data["significant_bits_dropped"] is None
                else _text(data["significant_bits_dropped"])
            ),
        )


MARKER = "MorphoBricks Z projection:"
"""How the ``Image/Description`` of every projected copy begins (read by :mod:`.projection_marker`)."""


def projection_description(entry: ProjectionEntry) -> str:
    """Return the text written as the copy's OME-XML ``Image/Description``.

    It says how the copy was made, so the file describes itself: the method, the slices
    used, the source, its content SHA-256 and the types. The conversion record holds the
    same facts.
    """
    start, stop = entry.z_range
    return (
        f"{MARKER} {entry.method} of slices {start}:{stop} "
        f"(of {entry.source_z_size}) of {entry.source_relative_path}; "
        f"source content SHA-256 {entry.source_content_sha256}; "
        f"{entry.source_dtype} to {entry.output_dtype} ({entry.dtype_rule}; "
        f"rounding: {entry.rounding}; NaN: {entry.nan_policy}); Z size dropped."
    )


def _check_shape(entry: ProjectionEntry) -> None:
    """Refuse an entry whose own fields do not fit each other."""
    name = entry.source_relative_path
    size, bounds = entry.source_z_size, entry.z_range
    if entry.method not in METHODS or entry.rounding not in ROUNDINGS:
        raise ValueError(
            f"{name!r}: unknown method {entry.method!r} or rounding {entry.rounding!r}"
        )
    if entry.nan_policy != NAN_POLICY:
        raise ValueError(f"{name!r}: the NaN policy is {NAN_POLICY!r}, not {entry.nan_policy!r}")
    if type(size) is not int or size < 1:
        raise ValueError(f"{name!r}: the Z size {size!r} is not a whole number of at least 1")
    ints = len(bounds) == 2 and all(type(b) is int for b in bounds)
    if not ints or not 0 <= bounds[0] < bounds[1] <= size:
        raise ValueError(f"{name!r}: the slice range {bounds!r} does not fit {size} slices")
    if entry.source_z_axis is None and size != 1:
        raise ValueError(f"{name!r}: {size} slices but no Z axis")
    if set(entry.dropped_sizes) - {"Z"}:
        raise ValueError(f"{name!r}: only the Z size is dropped, not {sorted(entry.dropped_sizes)}")
    try:
        np.dtype(entry.source_dtype)
        np.dtype(entry.output_dtype)
    except TypeError as exc:
        raise ValueError(f"{name!r}: {exc}") from exc


def _size_data(size: RecordedSize | None) -> dict | None:
    """Return a dropped size as ``{value, unit, source}``, or None for no size."""
    return None if size is None else {"value": size.value, "unit": size.unit, "source": size.source}


def _size(role: object, data: object) -> RecordedSize | None:
    """Read a dropped size written by :func:`_size_data`."""
    if data is None:
        return None
    if not isinstance(data, Mapping) or set(data) != {"value", "unit", "source"}:
        raise ValueError(f"the dropped size {data!r} is not {{value, unit, source}}")
    if data["source"] not in SOURCES or data["value"] is None:
        raise ValueError(f"the dropped size {data!r} needs a value and a source")
    return RecordedSize.from_dict({"role": role, **data})


def _axis(data: object) -> tuple[str, int] | None:
    """Read the source's Z axis ``{letter, index}``, or None."""
    if data is None:
        return None
    ok = isinstance(data, Mapping) and set(data) == {"letter", "index"}
    if not ok or not isinstance(data["letter"], str) or type(data["index"]) is not int:
        raise ValueError(f"the source Z axis {data!r} is not {{letter, index}}")
    return data["letter"], data["index"]


def _text(value: object) -> str:
    """Return a JSON string; raise for anything else."""
    if not isinstance(value, str):
        raise ValueError(f"{value!r} is not text")
    return value
