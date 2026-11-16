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

__all__ = ["CONNECTIVITY", "STRUCTURE", "label_binary", "labelling_bytes"]

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
    bound = _most_components(mask.shape)
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


def labelling_bytes(shape: tuple[int, int], dtype: np.dtype) -> int:
    """Return an upper bound of the memory :func:`label_binary` allocates beside its result.

    For a ``(height, width)`` plane of ``dtype``, in bytes, from the shape and type only (no
    pixel is read). The input plane and the ``int32`` result (4 bytes per pixel) are not
    counted. The two steps run one after the other, so the larger of the two is returned:

    - the value check: ``numpy.unique``'s sorted copy of the plane (its own bytes per pixel)
      and its flags, counted at 2 bytes per pixel;
    - the labelling: the boolean foreground (1 byte per pixel); SciPy's label table, 8-byte
      entries, one per provisional label and per pixel of a line, at most one provisional label
      per 2 x 2 block (8-connectivity), counted three times for its doublings while it grows
      (SciPy 1.18 ``_ni_label``); SciPy's two line buffers; and, for a plane that could hold more
      components than the ``int32`` limit, the ``int64`` labels (8 bytes per pixel).

    It is an upper bound for every plane of that shape and type, however its foreground lies:
    a plane of isolated pixels needs the most.
    """
    height, width = (int(n) for n in shape)
    pixels, line, most = height * width, max(height, width) + 2, _most_components(shape)
    check = pixels * (np.dtype(dtype).itemsize + 2)
    # The table doubles until it holds ``most + line`` entries, so it ends below twice that;
    # while it is reallocated the old and the new table are both held: three times in all.
    table = 3 * 8 * (most + line)
    wide = 8 * pixels if most > labels.LARGEST_LABEL else 0
    return max(check, pixels + table + 2 * 8 * line + wide)


def _most_components(shape: tuple[int, ...]) -> int:
    """Return the most 8-connected components a plane of ``shape`` holds: one per 2 x 2 block."""
    return math.ceil(shape[0] / 2) * math.ceil(shape[1] / 2)
