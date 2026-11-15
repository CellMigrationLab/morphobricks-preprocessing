"""The one labeller of a ``binary`` mask: 8-connected components (Spec ADR 0005 BM1, BM6).

:func:`label_binary` is the pure labelling function behind ``RunInputs.read_objects`` (the only
place a run converts a ``binary`` mask to objects, as the resolver recorded it), and the mask
step's piece counter uses its structure. Connectivity is 8 and fixed: two foreground pixels are
one object when they share an edge or a corner. No argument, setting or record field chooses
it; the resolver's record states it (``connectivity: 8``).
"""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage

from ..mask_rules import check_binary_values
from . import labels

__all__ = ["CONNECTIVITY", "STRUCTURE", "label_binary"]

CONNECTIVITY = 8
"""The number of neighbours of a pixel that join it to an object: edges and corners."""
STRUCTURE = np.ones((3, 3), dtype=bool)
"""The 3 x 3 structure of ones of 8-connectivity (SciPy's ``generate_binary_structure(2, 2)``)."""
STRUCTURE.flags.writeable = False


def label_binary(mask: np.ndarray, *, name: str = "the mask") -> np.ndarray:
    """Label the 8-connected components of a 2-D binary plane, 1 to n with no gap.

    The plane is first checked by :func:`.mask_rules.check_binary_values` (its dtype, then its
    values), so a direct call is as safe as a call through ``RunInputs.read_objects``. Nothing is
    cast, thresholded or squeezed, and the input is not changed.

    Parameters
    ----------
    mask
        A 2-D ``numpy.ndarray`` of signed or unsigned integers or booleans holding values in
        {0, v} for one v > 0 (all zero and all foreground included).
    name
        The plane as a refusal names it (the ``{path}`` of the message).

    Returns
    -------
    numpy.ndarray
        A new ``int32`` array of the input's shape: 0 where ``mask == 0``, else the component's
        label, numbered 1 to n in row-major order of each component's first pixel. A plane with
        no foreground gives zero objects; an all-foreground plane one object.

    Raises
    ------
    MaskContradictsRepresentationError
        ``R_MASK_CONTRADICTS_REPRESENTATION``: a float, complex or other non-integer dtype (also
        when every value is whole), a negative value, or two distinct positive values.
    ValueError
        For anything but a plain 2-D ``numpy.ndarray``, and for a plane with more components
        than :data:`.labels.LARGEST_LABEL` (the count is taken before the int32 output is
        filled; nothing is wrapped, widened or truncated).
    """
    if type(mask) is not np.ndarray:
        raise ValueError(
            f"{name} is a {type(mask).__name__}, not a NumPy array; label_binary labels a 2-D "
            "numpy.ndarray"
        )
    if mask.ndim != 2:
        raise ValueError(
            f"{name} has {mask.ndim} dimensions (shape {mask.shape}); label_binary labels one "
            "2-D plane and never squeezes"
        )
    values = np.unique(mask) if mask.dtype.kind in "biu" else ()
    check_binary_values(values, dtype=mask.dtype, path=name)
    foreground = mask != 0
    limit = labels.LARGEST_LABEL
    # At most one component per 2 x 2 block (8-connectivity): below that bound int32 cannot
    # overflow; above it the count is taken in int64 first and refused when too large.
    bound = math.ceil(mask.shape[0] / 2) * math.ceil(mask.shape[1] / 2)
    if bound <= limit:
        found = np.empty(mask.shape, dtype=np.int32)
        ndimage.label(foreground, STRUCTURE, output=found)
        return found
    wide = np.empty(mask.shape, dtype=np.int64)
    count = int(ndimage.label(foreground, STRUCTURE, output=wide))
    if count > limit:
        raise ValueError(
            f"{name} has {count} connected components; at most {limit} fit an int32 label, so "
            "it is not labelled (never wrapped, widened or truncated)"
        )
    return wide.astype(np.int32)
