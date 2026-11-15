"""The shared mask refusals of Spec ADR 0005 ("one policy for binary masks"): one constructor each.

Each refusal code has exactly **one** function that builds its code and message (ADR 0005 BM9,
"construction and propagation"); every other place calls it and passes the error on unchanged:

- :func:`refuse_not_instances` builds ``A_MASK_NOT_INSTANCES``: for a mask whose declared
  representation is not ``instances`` requested as objects (the resolver turns it into a
  :class:`.run_inputs_request.Refusal`), and for an in-memory object set that is not an integer
  instance array (:func:`.masks.check_object_set` raises it). It returns the error; it never
  raises it.
- :func:`refuse_binary_values` builds ``R_MASK_CONTRADICTS_REPRESENTATION``. Only the shared
  validator :func:`check_binary_values` calls it: the mask step on the distinct values of a
  whole file, and :func:`.masks.binary.label_binary` on one plane (the read guard of
  ``RunInputs.read_objects``).

The value rule of a ``binary`` mask (BM11, experiment-v1 section 4.5): stored as signed or
unsigned integers or booleans, never floats (also when every value is whole), holding values in
{0, v} for one v > 0. An all-zero and an all-foreground file are accepted. Pure: no file is
read here.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from .run_inputs_error import MaskNotInstancesError

__all__ = [
    "MASK_CONTRADICTS_REPRESENTATION",
    "MASK_NOT_INSTANCES",
    "SHOWN_VALUES",
    "MaskContradictsRepresentationError",
    "binary_dtype",
    "binary_values_broken",
    "check_binary_values",
    "refuse_binary_values",
    "refuse_not_instances",
]

MASK_NOT_INSTANCES = "A_MASK_NOT_INSTANCES"
MASK_CONTRADICTS_REPRESENTATION = "R_MASK_CONTRADICTS_REPRESENTATION"
SHOWN_VALUES = 10
"""At most this many distinct values are named in the values message; ``, ...]`` follows."""
_INSTANCES = "instances"
_PARENT_LABELS = "parent_labels"
_POLICY = "connected_components"
_OBJECT_SET = (
    "An object set is an integer instance array (0 is background, each positive value one "
    "object). A boolean array is used only as a region. To measure the connected pieces of a "
    "binary mask as objects, describe it as binary and request "
    "binary_mask_policy='connected_components' in a run."
)


class MaskContradictsRepresentationError(ValueError):
    """A file or plane of a mask declared ``binary`` breaks the binary value rule (BM11)."""

    code = MASK_CONTRADICTS_REPRESENTATION


def refuse_not_instances(
    subject: str, *, declared: str | None = None, value: object = None
) -> MaskNotInstancesError:
    """Return the ``A_MASK_NOT_INSTANCES`` error: the only place its messages are built.

    Parameters
    ----------
    subject
        The mask ID (with ``declared``) or the object set's name (with ``value``).
    declared
        The declared representation of a mask requested as objects: ``parent_labels`` (never
        an object set, no conversion offered) or ``binary`` (the recorded conversion offered).
    value
        The in-memory object that is not an integer instance array.

    Returns
    -------
    MaskNotInstancesError
        Not raised: the resolver records it as a refusal, a checker raises it.

    Raises
    ------
    ValueError
        Unless exactly one of ``declared`` and ``value`` is given (``value=None`` counts as
        given when ``declared`` is None: ``None`` is an object set that is refused).
    """
    if declared is not None and value is not None:
        raise ValueError("refuse_not_instances takes one of declared and value, not both")
    if declared == _INSTANCES:
        raise ValueError("an instances mask is an object set: there is nothing to refuse")
    if declared is None:
        return MaskNotInstancesError(_value_message(subject, value))
    if declared == _PARENT_LABELS:
        return MaskNotInstancesError(
            f"The mask {subject!r} is declared parent_labels: each non-zero value is one "
            "group (for example the mitochondria of one cell) in any number of pieces, not one "
            "object, so it cannot be an object set. It is never split, relabelled or converted. "
            "Use it in the role 'region', or name an instances mask as the objects "
            "(A_MASK_NOT_INSTANCES)."
        )
    return MaskNotInstancesError(
        f"The mask {subject!r} is declared {declared}, so it has no object identity and cannot "
        f"be an object set. Ask for the recorded conversion binary_mask_policy={_POLICY!r} in "
        "this request, or declare the mask instances (A_MASK_NOT_INSTANCES)."
    )


def _value_message(name: str, value: object) -> str:
    """Return the in-memory message: the array branch or the non-array branch."""
    if type(value) is np.ndarray:
        kind = f"{value.dtype} array"
    else:
        kind = f"{type(value).__name__} (not a plain NumPy array)"
    return f"{MASK_NOT_INSTANCES}: {name!r} is a {kind}, not an object set. {_OBJECT_SET}"


def refuse_binary_values(
    path: str, *, values: Iterable[int] | None = None, dtype: object = None
) -> MaskContradictsRepresentationError:
    """Return the ``R_MASK_CONTRADICTS_REPRESENTATION`` error; the only builder of its messages.

    Called only by :func:`check_binary_values`.

    Parameters
    ----------
    path
        The file (mask step) or the plane's name (``label_binary``'s ``name``).
    values
        The distinct non-zero values found when the check stopped (named ascending, at most
        :data:`SHOWN_VALUES` of them).
    dtype
        The stored pixel type that is not integer or boolean.

    Raises
    ------
    ValueError
        Unless exactly one of ``values`` and ``dtype`` is given.
    """
    if (values is None) == (dtype is None):
        raise ValueError("refuse_binary_values takes exactly one of values and dtype")
    if dtype is not None:
        return MaskContradictsRepresentationError(
            f"{MASK_CONTRADICTS_REPRESENTATION}: {path} is declared binary but has pixel type "
            f"{np.dtype(dtype)}; a binary mask is stored as integers or booleans, never as "
            "floats, even when its values are whole numbers. Save it as integers or booleans."
        )
    found = sorted({int(v) for v in values})
    shown = str(found[:SHOWN_VALUES])
    if len(found) > SHOWN_VALUES:
        shown = shown[:-1] + ", ...]"
    return MaskContradictsRepresentationError(
        f"{MASK_CONTRADICTS_REPRESENTATION}: {path} is declared binary but holds the values "
        f"{shown}; a binary mask holds 0 and at most one positive value (an all-zero or "
        "all-foreground file is accepted). Declare it instances or parent_labels, or fix the "
        "file."
    )


def binary_dtype(dtype: object) -> bool:
    """Return True for a pixel type a ``binary`` mask may be stored as (integer or boolean)."""
    return np.dtype(dtype).kind in "biu"


def binary_values_broken(values: Iterable[int]) -> bool:
    """Return True once ``values`` hold a negative value or two distinct positive values.

    The mask step stops reading a file's planes as soon as this holds: no later plane can make
    the file pass.
    """
    found = {int(v) for v in values}
    return any(v < 0 for v in found) or sum(1 for v in found if v > 0) > 1


def check_binary_values(values: Iterable[int], *, dtype: object, path: str) -> None:
    """Refuse a file or plane of a ``binary`` mask that breaks the value rule (BM11).

    The dtype is checked first: signed or unsigned integers or booleans; never float, complex,
    object or any other type, also when every value is whole. Then the values: {0, v} for one
    v > 0, so all-zero ({0}) and all-foreground ({v}) pass, and a negative value or two or more
    distinct positive values do not.

    Parameters
    ----------
    values
        The distinct values of a plane, or of every plane of a file together (the mask step's
        file-wide union). Not read when the dtype is refused.
    dtype
        The stored pixel type.
    path
        The file or plane as the message names it.

    Raises
    ------
    MaskContradictsRepresentationError
        ``R_MASK_CONTRADICTS_REPRESENTATION``, built by :func:`refuse_binary_values`.
    """
    if not binary_dtype(dtype):
        raise refuse_binary_values(path, dtype=dtype)
    found = {int(v) for v in values}
    if binary_values_broken(found):
        raise refuse_binary_values(path, values=sorted(v for v in found if v != 0))
