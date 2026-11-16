"""Object sets: the in-memory check (ADR 0005 BM4) and the memory of an objects read (#316).

An in-memory object set is an integer instance array, nothing else. A boolean array is
accepted only as a region (normalization's ``check_region``); no in-memory API converts, labels
or carries ``binary_mask_policy``. The refusal is built by the one constructor,
:func:`.mask_rules.refuse_not_instances`, and raised here unchanged.

:func:`objects_read_plan` says, from a mask's header, what ``RunInputs.read_objects`` costs:
its result is ``int32`` whatever the stored pixel type.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..mask_requests import CONNECTED_COMPONENTS
from ..mask_rules import refuse_not_instances
from .binary import labelling_bytes

__all__ = ["ObjectsReadPlan", "check_object_set", "objects_read_plan"]

OBJECT_LABELS = np.dtype(np.int32)
"""The pixel type of every object set ``read_objects`` returns."""


@dataclass(frozen=True)
class ObjectsReadPlan:
    """What one ``RunInputs.read_objects`` plane costs, from the mask's header (issue #316).

    Attributes
    ----------
    shape
        ``(height, width)`` of the plane and of the result.
    stored_dtype
        The mask's stored pixel type (what ``mask_window_plan`` and a region read count).
    dtype
        The result's pixel type: always ``int32``.
    conversion
        The selection's recorded conversion: None, or ``"connected_components"``.
    stored_bytes
        The stored plane, ``shape`` times the stored bytes per pixel: the read holds it while
        it makes the result.
    result_bytes
        The ``int32`` result, 4 bytes per pixel (4 times ``stored_bytes`` for a ``uint8`` mask).
    conversion_bytes
        For ``connected_components``: :func:`.binary.labelling_bytes`, an upper bound of what
        the labeller allocates beside its input and its result; 0 without a conversion.
    memory_estimate
        ``stored_bytes + result_bytes + conversion_bytes``: the peak working memory of an
        objects read (a ``uint8`` instances mask: 5 times its stored size; an ``int32`` one 8
        bytes per pixel, its stored plane and the copy ``read_objects`` returns). It counts
        the decoded plane: a compressed file's encoded pieces, which the reader holds while it
        decodes them (up to the plane's compressed size: small for a label mask, up to
        ``stored_bytes`` for one that does not compress), an OME-Zarr chunk that also holds
        other planes, and a few kilobytes of header are not counted.
    """

    shape: tuple[int, int]
    stored_dtype: np.dtype
    dtype: np.dtype
    conversion: str | None
    stored_bytes: int
    result_bytes: int
    conversion_bytes: int
    memory_estimate: int


def objects_read_plan(
    shape: tuple[int, int], stored_dtype: np.dtype, conversion: str | None
) -> ObjectsReadPlan:
    """Return the :class:`ObjectsReadPlan` of a ``(height, width)`` plane of ``stored_dtype``.

    Raises
    ------
    ValueError
        For a conversion other than None or ``"connected_components"``.
    """
    if conversion not in (None, CONNECTED_COMPONENTS):
        raise ValueError(f"an objects read has no conversion {conversion!r}")
    shape = (int(shape[0]), int(shape[1]))
    stored_dtype = np.dtype(stored_dtype)
    pixels = shape[0] * shape[1]
    stored, result = pixels * stored_dtype.itemsize, pixels * OBJECT_LABELS.itemsize
    work = labelling_bytes(shape, stored_dtype) if conversion else 0
    return ObjectsReadPlan(
        shape=shape,
        stored_dtype=stored_dtype,
        dtype=OBJECT_LABELS,
        conversion=conversion,
        stored_bytes=stored,
        result_bytes=result,
        conversion_bytes=work,
        memory_estimate=stored + result + work,
    )


def check_object_set(value: object, *, name: str) -> None:
    """Refuse an in-memory object set that is not a plain integer ``numpy.ndarray``.

    Parameters
    ----------
    value
        The object set: a plain ``numpy.ndarray`` (``type(value) is numpy.ndarray``) of a
        signed or unsigned integer dtype passes. Its labels are not checked here.
    name
        The object set as the message names it.

    Raises
    ------
    MaskNotInstancesError
        ``A_MASK_NOT_INSTANCES``: a boolean, float or other non-integer array, or anything
        that is not a plain NumPy array (a list, None, a tensor, a ``numpy.ma.MaskedArray``).
    """
    if type(value) is np.ndarray and value.dtype.kind in "iu":
        return
    raise refuse_not_instances(name, value=value)
