"""An in-memory object set (Python API): an integer instance array, nothing else (ADR 0005 BM4).

A boolean array is accepted only as a region (normalization's ``check_region``); no in-memory
API converts, labels or carries ``binary_mask_policy``. The refusal is built by the one
constructor, :func:`.mask_rules.refuse_not_instances`, and raised here unchanged.
"""

from __future__ import annotations

import numpy as np

from ..mask_rules import refuse_not_instances

__all__ = ["check_object_set"]


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
