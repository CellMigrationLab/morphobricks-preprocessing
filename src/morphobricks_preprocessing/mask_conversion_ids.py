"""Mask IDs and representations a mask conversion accepts (moved from ``mask_conversion``).

The helpers' names and the checks of an output mask ID (experiment-v1 section 3.2) and of a
source's declared representation, before anything is read (Spec ADR 0005 BM12).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from .description_identity import Identities, nfc
from .description_report import Issues

if TYPE_CHECKING:
    from .description import Mask

BINARY_TO_INSTANCES = "binary_to_instances"
CLASSES_TO_BINARY = "classes_to_binary"


def _check_representation(operation: str, mask_id: str, path: str, declared: Mask) -> None:
    """Refuse a declared representation the helper does not convert."""
    representation = declared.representation
    if operation == BINARY_TO_INSTANCES and representation != "binary":
        raise ValueError(
            f"{mask_id!r} ({path}) is declared {representation}; binary_to_instances converts "
            "a mask declared binary (an instances mask holds objects already, and a "
            "parent_labels mask is never split into objects)"
        )
    if operation == CLASSES_TO_BINARY and representation == "binary":
        raise ValueError(
            f"{mask_id!r} ({path}) is declared binary: it has one class already; declare or "
            "use it as it is"
        )


def _check_output_id(value: object, what: str) -> None:
    """Refuse an output mask ID (or prefix) the mask ID rules of experiment-v1 3.2 refuse."""
    if not isinstance(value, str):
        raise ValueError(f"The {what} must be text, not {type(value).__name__}")
    issues = Issues()
    ids = Identities(issues)
    ids.token(value, what)
    ids.nfc(value, what)
    ids.single(value, what)
    ids.reserved(value, what, object_set=True)
    if issues:
        codes = ", ".join(dict.fromkeys(str(item.code.value) for item in issues.items))
        raise ValueError(
            f"The {what} {value!r} breaks the mask ID rules of experiment-v1 section 3.2 "
            f"({codes}); choose another one"
        )


def _check_case_collision(output_id: str, declared: Mapping[str, Mask]) -> None:
    """Refuse an output mask ID spelled like a declared mask ID or source type but for case.

    experiment-v1 section 3.2 makes two mask IDs, or two mask source types, of one description
    that differ only in case ``E_ID_CASE_COLLISION``; the output is both, so it could never be
    declared next to them.
    """
    folded = nfc(output_id).casefold()
    names = [("mask ID", mask_id) for mask_id in sorted(declared)]
    names += [("source type", t) for t in sorted({m.source_type for m in declared.values()})]
    for kind, name in names:
        if nfc(name) != nfc(output_id) and nfc(name).casefold() == folded:
            raise ValueError(
                f"The output mask ID {output_id!r} differs only in case from the declared "
                f"{kind} {name!r}; experiment-v1 section 3.2 refuses such a pair "
                "(E_ID_CASE_COLLISION), so the output could never be declared: choose another ID"
            )
