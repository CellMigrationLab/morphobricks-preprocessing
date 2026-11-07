"""Z projections as pure arithmetic: the method, the output type and the running plane.

A recorded projection copy (issue #176) turns a Z-stack into a 2-D image, one (t, c) at a
time, with the maximum, the mean or the sum of its slices. This module decides what that
arithmetic is and never reads or writes a file:

- :class:`Projection` is the request: a method and an optional slice range.
- :func:`projection_dtype` gives the output pixel type, decided only by the input type, the
  method and the number of slices (never by the data), with the arithmetic that makes the
  result exact. A projection that could not be exact is refused here, before any pixel is
  read: nothing wraps, nothing clips.
- :class:`PlaneAccumulator` adds slices, in ascending order, into one running plane. The
  order is fixed, so the bytes are deterministic.

The output type (``Z`` slices of the input type in the column, summed or averaged exactly):

========================  =======  =======================  =========
Input                     max      sum                      mean
========================  =======  =======================  =========
uint8, uint16             same     uint32 (exact integer)   float64
int8, int16               same     int32 (exact integer)    float64
uint32, int32             same     float64 (exact integer)  float64
float32, float64          same     float64                  float64
int64, uint64             refused  refused                  refused
========================  =======  =======================  =========

A NaN in a slice makes that pixel NaN; infinities are kept. A finite input that sums to
infinity, or an infinity added to its opposite, is refused (``projection_overflow`` and
``projection_undefined``), never written.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

METHODS = ("max", "mean", "sum")
"""The projection methods, in the order they are listed to the user."""

EXACT_FLOAT_INTEGER = 2**53
"""The largest integer a float64 holds exactly together with every integer below it."""

_SPECIAL_NAN = 1
_SPECIAL_INF = 2

_INTEGER_SUM_OUTPUT = {
    "uint8": "uint32",
    "uint16": "uint32",
    "int8": "int32",
    "int16": "int32",
    "uint32": "float64",
    "int32": "float64",
}
"""The type an exact integer sum is written in, by input type."""

_INTEGER_ACCUMULATOR = {"u": "uint64", "i": "int64"}
_FLOAT_INPUTS = frozenset({"float32", "float64"})
_INTEGER_INPUTS = frozenset(_INTEGER_SUM_OUTPUT)


class ProjectionRefused(ValueError):  # noqa: N818 (a refusal, named for what it says)
    """Raised when a projection cannot be made exactly; ``code`` is the refusal's code."""

    def __init__(self, code: str, message: str) -> None:
        """Keep the code with the plain-language message."""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Projection:
    """What the user asked for: a method and, optionally, the slices to use.

    Attributes
    ----------
    method
        ``"max"``, ``"mean"`` or ``"sum"``. There is no default.
    z_range
        ``(start, stop)``, zero-based and half-open, the same for every image; None for
        every slice.
    """

    method: Literal["max", "mean", "sum"]
    z_range: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        """Refuse a method or range that makes no sense, before anything is read."""
        if self.method not in METHODS:
            raise ValueError(
                "--project needs a method: max, mean or sum. There is no default: choose "
                f"the one that fits your analysis (got {self.method!r})."
            )
        if self.z_range is None:
            return
        bounds = tuple(self.z_range)
        valid = len(bounds) == 2 and all(type(b) is int for b in bounds)
        if not valid or not 0 <= bounds[0] < bounds[1]:
            raise ValueError(
                f"The slice range must be two whole numbers START:STOP with 0 <= START < STOP "
                f"(zero-based, STOP excluded), not {self.z_range!r}."
            )
        object.__setattr__(self, "z_range", bounds)

    def resolve(self, size: int) -> tuple[int, int]:
        """Return the slices ``(start, stop)`` used for an image with ``size`` slices.

        Raises
        ------
        ValueError
            If the range reaches past the image (a range is never shortened to fit).
        """
        if self.z_range is None:
            return 0, size
        start, stop = self.z_range
        if stop > size:
            raise ValueError(f"the slice range {start}:{stop} does not fit {size} slices")
        return start, stop


@dataclass(frozen=True)
class ProjectionDtype:
    """How one (input type, method, slice count) is computed and written.

    Attributes
    ----------
    method
        The projection method.
    source_dtype
        The input type's name.
    output_dtype
        The type of the written plane.
    accumulator_dtype
        The type the running plane is held in.
    slices
        The slice count this was decided for.
    max_slices
        The most slices the type holds exactly, or None for a maximum.
    dtype_rule
        One line saying how the type was decided, recorded with the copy.
    rounding
        ``"none"``, ``"float64, one division"`` or ``"float64 addition, z ascending"``.
    significant_bits_dropped
        Why the source's significant bits are not kept, or None when they are.
    """

    method: str
    source_dtype: str
    output_dtype: np.dtype
    accumulator_dtype: np.dtype
    slices: int
    max_slices: int | None
    dtype_rule: str
    rounding: str
    significant_bits_dropped: str | None

    def working_nbytes(self, plane_shape: tuple[int, int]) -> int:
        """Return the bytes one projection holds at its peak.

        That is one input plane, the running plane, the output plane when it is another
        type than the running one, and a one-byte flag per pixel for floats added up.
        """
        pixels = int(plane_shape[0]) * int(plane_shape[1])
        total = np.dtype(self.source_dtype).itemsize + self.accumulator_dtype.itemsize
        if self.output_dtype != self.accumulator_dtype:
            total += self.output_dtype.itemsize
        if self.source_dtype in _FLOAT_INPUTS and self.method != "max":
            total += 1
        return pixels * total


def _slice_bound(dtype: np.dtype, low: int, high: int) -> int:
    """Return the most slices of ``dtype`` whose sum stays inside ``low``..``high``."""
    info = np.iinfo(dtype)
    bound = high // int(info.max)
    if info.min < 0:
        bound = min(bound, low // int(info.min))
    return bound


def _refuse_pixel_type(dtype: np.dtype) -> ProjectionRefused:
    """Return the refusal of a pixel type no copy can hold."""
    return ProjectionRefused(
        "cannot_convert_pixel_type",
        f"The pixel type {dtype} cannot be projected: an OME-TIFF copy cannot hold 64-bit "
        "integers, and pixel values are never changed to fit.",
    )


def _refuse_inexact(method: str, dtype: str, slices: int, bound: int) -> ProjectionRefused:
    """Return the refusal of a sum or mean that could not be exact."""
    return ProjectionRefused(
        "projection_cannot_be_exact",
        f"{method.capitalize()} of {slices} slices of {dtype} images can exceed what the copy "
        f"holds exactly (at most {bound} slices); nothing was written.",
    )


def projection_dtype(dtype: np.dtype | str, method: str, slices: int) -> ProjectionDtype:
    """Decide the output type of a projection, and refuse one that could not be exact.

    Parameters
    ----------
    dtype
        The input pixel type.
    method
        ``"max"``, ``"mean"`` or ``"sum"``.
    slices
        How many slices are projected (at least 1).

    Returns
    -------
    ProjectionDtype

    Raises
    ------
    ProjectionRefused
        ``cannot_convert_pixel_type`` for a type outside the table (64-bit integers among
        them), ``projection_cannot_be_exact`` when the worst case (the type's extreme times
        ``slices``) could not be held exactly.
    ValueError
        For an unknown method or fewer than one slice.
    """
    dtype = np.dtype(dtype).newbyteorder("=")
    name = dtype.name
    if method not in METHODS:
        raise ValueError(f"unknown projection method {method!r}")
    if type(slices) is not int or slices < 1:
        raise ValueError(f"a projection needs at least one slice, not {slices!r}")
    if name not in _INTEGER_INPUTS | _FLOAT_INPUTS:
        raise _refuse_pixel_type(dtype)
    float64 = np.dtype("float64")
    if method == "max":
        return ProjectionDtype(
            method, name, dtype, dtype, slices, None, f"max {name}->{name}", "none", None
        )
    reason = (
        "a sum can exceed the range the source's significant bits describe"
        if method == "sum"
        else "a mean is not an integer of the range the source's significant bits describe"
    )
    if name in _FLOAT_INPUTS:
        return ProjectionDtype(
            method,
            name,
            float64,
            float64,
            slices,
            None,
            f"{method} {name}->float64",
            "float64 addition, z ascending",
            reason,
        )
    return _integer_dtype(dtype, method, slices, reason)


def _integer_dtype(dtype: np.dtype, method: str, slices: int, reason: str) -> ProjectionDtype:
    """Decide the type of an integer sum or mean (exact, or refused)."""
    name = dtype.name
    float64 = np.dtype("float64")
    held = np.dtype(_INTEGER_ACCUMULATOR[dtype.kind])
    if method == "sum":
        output = np.dtype(_INTEGER_SUM_OUTPUT[name])
        if output.kind == "f":
            low, high = -EXACT_FLOAT_INTEGER, EXACT_FLOAT_INTEGER
            what = f"sum {name}->float64 exact integer within 2**53"
        else:
            info = np.iinfo(output)
            low, high = int(info.min), int(info.max)
            held, what = output, f"sum {name}->{output.name} exact"
        rounding = "none"
    else:
        output = float64
        low, high = -EXACT_FLOAT_INTEGER, EXACT_FLOAT_INTEGER
        what = f"mean {name}->float64 exact integer sum within 2**53, divided once"
        rounding = "float64, one division"
    bound = _slice_bound(dtype, low, high)
    if slices > bound:
        raise _refuse_inexact(method, name, slices, bound)
    return ProjectionDtype(
        method, name, output, held, slices, bound, f"{what}, bound Z<={bound}", rounding, reason
    )


class PlaneAccumulator:
    """Add the slices of one (t, c) into a running plane and give the projection.

    Parameters
    ----------
    plane_shape
        ``(rows, columns)`` of every slice.
    dtype
        The input pixel type; a slice of another type is refused, never cast.
    method
        ``"max"``, ``"mean"`` or ``"sum"``.
    slices
        How many slices will be added.

    Raises
    ------
    ProjectionRefused
        As :func:`projection_dtype`.
    """

    def __init__(
        self, plane_shape: tuple[int, int], dtype: np.dtype | str, method: str, slices: int
    ) -> None:
        """Decide the arithmetic and start the running plane."""
        self.rule = projection_dtype(dtype, method, slices)
        self._shape = (int(plane_shape[0]), int(plane_shape[1]))
        self._source = np.dtype(self.rule.source_dtype)
        self._count = 0
        self._running: np.ndarray | None = None
        self._special: np.ndarray | None = None
        if self.rule.source_dtype in _FLOAT_INPUTS and method != "max":
            self._special = np.zeros(self._shape, dtype=np.uint8)

    def add(self, plane: np.ndarray) -> None:
        """Add the next slice (ascending z).

        Raises
        ------
        ValueError
            If the slice has another shape or type than declared, or more slices than
            declared are added.
        """
        plane = np.asarray(plane)
        if plane.dtype != self._source or plane.shape != self._shape:
            raise ValueError(
                f"a slice is {plane.shape} {plane.dtype}, not {self._shape} {self._source} "
                "as declared"
            )
        if self._count >= self.rule.slices:
            raise ValueError(f"more than the declared {self.rule.slices} slices were added")
        if self._special is not None:
            self._special |= np.isnan(plane).astype(np.uint8) * _SPECIAL_NAN
            self._special |= np.isinf(plane).astype(np.uint8) * _SPECIAL_INF
        if self._running is None:
            self._running = np.zeros(self._shape, dtype=self.rule.accumulator_dtype)
            if self.rule.method == "max":
                self._running[...] = plane
                self._count = 1
                return
        if self.rule.method == "max":
            np.maximum(self._running, plane, out=self._running)
        else:
            # An infinity from finite numbers is found and refused in result(), not warned.
            with np.errstate(over="ignore", invalid="ignore"):
                np.add(self._running, plane, out=self._running, casting="safe")
        self._count += 1

    def result(self) -> np.ndarray:
        """Return the projected plane, in the output type.

        Raises
        ------
        ValueError
            If fewer slices than declared were added.
        ProjectionRefused
            ``projection_overflow`` or ``projection_undefined`` for a float sum the
            arithmetic made infinite or undefined from finite numbers.
        """
        if self._running is None or self._count != self.rule.slices:
            raise ValueError(f"{self._count} of {self.rule.slices} declared slices were added")
        running = self._running
        if self._special is not None:
            self._check_floats(running)
        if self.rule.method == "mean":
            if running.dtype != self.rule.output_dtype:
                running = running.astype(self.rule.output_dtype)
            running /= self.rule.slices
        elif running.dtype != self.rule.output_dtype:
            running = running.astype(self.rule.output_dtype)
        return running

    def _check_floats(self, running: np.ndarray) -> None:
        """Refuse a float sum that is infinite or undefined without an input to explain it."""
        special = self._special
        overflow = int(np.count_nonzero(np.isinf(running) & ((special & _SPECIAL_INF) == 0)))
        if overflow:
            raise ProjectionRefused(
                "projection_overflow",
                f"The sum overflows to infinity (float64) at {overflow} pixels.",
            )
        undefined = int(np.count_nonzero(np.isnan(running) & ((special & _SPECIAL_NAN) == 0)))
        if undefined:
            raise ProjectionRefused(
                "projection_undefined",
                f"The sum adds an infinity to its opposite at {undefined} pixels, "
                "which is undefined.",
            )
