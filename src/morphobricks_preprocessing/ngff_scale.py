"""The ``scale`` of an OME-NGFF ``coordinateTransformations`` list, checked (issue #94).

A calibration that is not given (no transformations, or none of type ``scale``) is valid:
the image is uncalibrated. A calibration that is given but cannot be used is never dropped
silently: :func:`read_scale` returns what is wrong with it, in plain words, so that
:func:`.physical_sizes.ngff_physical_sizes` records it (:attr:`.PhysicalSizes.invalid`) and
the folder check names the image (``invalid_physical_calibration``, contract §11). Such
sizes are unknown (None); no value is ever substituted.

Nothing here reads files.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

TRANSFORM_TYPES = ("scale", "translation", "identity")
"""The transformation types a level or image may have; only ``scale`` changes the sizes."""

CALIBRATED_AXES = "XYZT"
"""The axes whose scale is a pixel size, z-step or time step: it must be a positive number."""


def read_scale(entry: object, axes: str, where: str) -> tuple[list[float | None] | None, list[str]]:
    """Return the ``scale`` of an entry's ``coordinateTransformations`` and its problems.

    Parameters
    ----------
    entry
        A dataset (a level) or a multiscales entry.
    axes
        One letter per dimension (:func:`.zarr_input.ngff_axes`).
    where
        What the entry is, for the problems, e.g. ``"level 0"``.

    Returns
    -------
    tuple
        ``(scale, problems)``. ``scale`` is None when the entry has no scale, or when its
        transformations cannot be used at all (then ``problems`` says why); otherwise one
        value per axis, None for a value that is not a usable number (``problems`` names
        it). ``problems`` is empty for a usable or absent scale.
    """
    transforms = entry.get("coordinateTransformations") if isinstance(entry, dict) else None
    if transforms is None:
        return None, []
    if not isinstance(transforms, list):
        return None, [f"the coordinateTransformations of {where} are not a list"]
    scales = []
    for transform in transforms:
        kind = transform.get("type") if isinstance(transform, dict) else None
        if kind not in TRANSFORM_TYPES:
            return None, [
                f"{where} has a coordinate transformation of type {kind!r}, not one of "
                f"{', '.join(TRANSFORM_TYPES)}"
            ]
        if kind == "scale":
            scales.append(transform)
    if not scales:
        return None, []
    if len(scales) > 1:
        return None, [f"{where} has {len(scales)} scale transformations, not one"]
    return _scale_values(scales[0], axes, where)


def _scale_values(
    transform: dict, axes: str, where: str
) -> tuple[list[float | None] | None, list[str]]:
    """Return the values of one ``scale`` transformation and what is wrong with them."""
    values = transform.get("scale")
    if values is None and "path" in transform:
        return None, [
            f"the scale of {where} is stored in a separate file ({transform['path']!r}), "
            "which is not read"
        ]
    if not isinstance(values, Sequence) or isinstance(values, str):
        return None, [f"the scale of {where} is not a list of numbers"]
    if len(values) != len(axes):
        return None, [
            f"the scale of {where} has {len(values)} values for {len(axes)} axes, not one "
            "number per axis"
        ]
    scale: list[float | None] = []
    problems = []
    for letter, value in zip(axes, values, strict=True):
        number = _scale_value(value)
        usable = number is not None and (
            letter not in CALIBRATED_AXES or (math.isfinite(number) and number > 0)
        )
        scale.append(number if usable else None)
        if not usable:
            problems.append(
                f"the scale of {where} gives {_shown(value)} for axis {letter}, not a "
                "positive number"
            )
    return scale, problems


def _scale_value(value: object) -> float | None:
    """Return one scale value as a float; None when it is not a number a float can hold."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return float(value)
    except OverflowError:  # an integer too large for a float is not a pixel size
        return None


def _shown(value: object) -> str:
    """Return a scale value as shown in a problem, shortened when it is long."""
    text = repr(value)
    return text if len(text) <= 24 else f"{text[:20]}..."
