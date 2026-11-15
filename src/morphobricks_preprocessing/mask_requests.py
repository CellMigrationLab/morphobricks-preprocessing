"""The mask uses of a request, resolver contract version 3 (Spec ADR 0005; pure).

Moved out of :mod:`.run_inputs_request`, which imports and re-exports every name here. A mask
request is identified by ``(mask_id, role)``: a consumer sends one entry per use, entries of one
use collapse (:func:`mask_uses`), the ``objects`` use of one mask ID carries one conversion at
most (:func:`objects_conflict`), and each converted mask ID gets one :class:`MaskConversion`
record in ``Provenance.mask_conversions``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol, TypeVar

OBJECTS = "objects"
"""The role of an object set (the only role a conversion may apply to)."""
CONNECTED_COMPONENTS = "connected_components"
"""The recorded conversion of a ``binary`` mask to objects (experiment-v1 section 4.5c)."""
LABELLER = "morphobricks_preprocessing.RunInputs.read_objects"
"""The labeller a ``connected_components`` record names: the one helper that applies it."""
LABELLER_CONNECTIVITY = 8
"""The connectivity a ``connected_components`` record states (neighbours, not SciPy's rank);
fixed, never a parameter (Spec ADR 0005 BM1). :data:`.masks.binary.CONNECTIVITY` is the
labeller's own constant; a test keeps the two equal (the resolver imports no labelling code)."""


class _Use(Protocol):
    """What these rules read of a :class:`.run_inputs_request.MaskRequest`."""

    mask_id: str
    role: str
    binary_mask_policy: str | None


Use = TypeVar("Use", bound=_Use)


@dataclass(frozen=True)
class MaskConversion:
    """One conversion record of ``Provenance.mask_conversions`` (Spec ADR 0005 BM2, BM10).

    The fields are the record's four keys, in its order; :meth:`as_record` gives the record a
    consumer writes under ``mask_conversions`` in its run record, as is.

    Attributes
    ----------
    mask_id
        The description's mask ID (never a source type).
    conversion
        ``connected_components`` or ``parent_labels_as_area``.
    connectivity
        8 for ``connected_components``; None for ``parent_labels_as_area``.
    labeller
        :data:`LABELLER` for ``connected_components``; None for ``parent_labels_as_area``.
    """

    mask_id: str
    conversion: str
    connectivity: int | None
    labeller: str | None

    def as_record(self) -> dict[str, object]:
        """Return the record: ``mask_id``, ``conversion``, ``connectivity``, ``labeller``."""
        return {
            "mask_id": self.mask_id,
            "conversion": self.conversion,
            "connectivity": self.connectivity,
            "labeller": self.labeller,
        }


def conversion_record(mask_id: str, conversion: str) -> MaskConversion:
    """Return the record of a selection's conversion (connectivity and labeller by its kind)."""
    if conversion == CONNECTED_COMPONENTS:
        return MaskConversion(mask_id, conversion, LABELLER_CONNECTIVITY, LABELLER)
    return MaskConversion(mask_id, conversion, None, None)


def objects_conflict(masks: Iterable[_Use]) -> None:
    """Refuse one mask ID requested as ``objects`` with two different conversions.

    One run converts a mask at most one way (Spec ADR 0005 BM2, BM5): every ``objects`` entry
    of one mask ID carries the same ``binary_mask_policy`` (all the policy, or all none). The
    message names the first two that differ, in request order.

    Raises
    ------
    ValueError
        ``{mask_id!r} is requested as objects with two different conversions ({a!r} and
        {b!r}); one run converts a mask at most one way``.
    """
    first: dict[str, str | None] = {}
    for mask in masks:
        if mask.role != OBJECTS:
            continue
        policy = first.setdefault(mask.mask_id, mask.binary_mask_policy)
        if policy != mask.binary_mask_policy:
            raise ValueError(
                f"{mask.mask_id!r} is requested as objects with two different conversions "
                f"({policy!r} and {mask.binary_mask_policy!r}); one run converts a mask at most "
                "one way"
            )


def mask_uses(masks: Iterable[Use]) -> tuple[Use, ...]:
    """Return the request's distinct ``(mask_id, role)`` uses, each at its first entry.

    Entries of one ``(mask_id, role)`` with the same conversion collapse into one (the request
    was checked by :func:`check_request`, so they all have the same conversion).
    """
    seen: dict[tuple[str, str], Use] = {}
    for mask in masks:
        seen.setdefault((mask.mask_id, mask.role), mask)
    return tuple(seen.values())


def mask_ids(masks: Iterable[_Use]) -> tuple[str, ...]:
    """Return the request's distinct mask IDs, in the order of their first entry."""
    return tuple(dict.fromkeys(mask.mask_id for mask in masks))
