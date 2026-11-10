"""The refusals of folder and mask-type names that apply with an experiment description.

Spec experiment-v1 §3.3 and §6: a folder label or a discovered mask type name read from disk
must be Unicode NFC (``R_FOLDER_NOT_NFC``), and a mask type name must pass the Label rules
(``R_MASK_TYPE_NAME_INVALID``). Neither is ever normalized, trimmed or renamed: the name is
reported as read. Every run is described (Spec ADR 0002 AR10), so they always apply.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from .validation import ValidationIssue, error, plural

if TYPE_CHECKING:
    from .discovery import LeafFolder

FOLDER_NOT_NFC = "R_FOLDER_NOT_NFC"
"""A folder label or discovered mask type name that is not Unicode NFC."""

MASK_TYPE_NAME_INVALID = "R_MASK_TYPE_NAME_INVALID"
"""A discovered mask type name that breaks the Label rules, so it cannot be a ``source_type``."""

NAME_CODES = frozenset({FOLDER_NOT_NFC, MASK_TYPE_NAME_INVALID})
"""The codes of this module."""


def is_nfc(name: str) -> bool:
    """Return True if ``name`` is Unicode NFC (checked, never converted)."""
    return unicodedata.is_normalized("NFC", name)


def non_nfc_folders_issue(leaves: Iterable[LeafFolder]) -> list[ValidationIssue]:
    """Return ``R_FOLDER_NOT_NFC`` for the label folders whose names are not NFC, or nothing.

    The label folders are the condition, biological-repeat and technical-repeat folders on
    the way to each leaf folder (:attr:`.LeafFolder.relative_path`), as read from disk; a
    folder that leads to no image is no label and is not checked. Never normalized: the
    paths are reported as read.

    Parameters
    ----------
    leaves
        The experiment's leaf folders (:attr:`.DiscoveryResult.leaves`).
    """
    found = set()
    for leaf in leaves:
        parts = () if leaf.relative_path == "." else PurePosixPath(leaf.relative_path).parts
        found.update("/".join(parts[: i + 1]) for i, part in enumerate(parts) if not is_nfc(part))
    found = sorted(found)
    if not found:
        return []
    return [
        error(
            FOLDER_NOT_NFC,
            f"{plural(len(found), 'folder')} {'has a name' if len(found) == 1 else 'have names'}"
            " that "
            f"{'is' if len(found) == 1 else 'are'} not in Unicode NFC form, for example an "
            "accented letter stored as a letter plus a separate accent. The name is never "
            "normalized or renamed for you, because the experiment description records folder "
            "names exactly as they are on disk.",
            found,
            "Rename these folders so that each accented character is a single composed "
            "character (NFC), or use names without accents.",
        )
    ]


def non_nfc_mask_types_issue(names: Iterable[str]) -> list[ValidationIssue]:
    """Return ``R_FOLDER_NOT_NFC`` for mask types whose names are not NFC, or nothing."""
    found = sorted(set(names))
    if not found:
        return []
    return [
        error(
            FOLDER_NOT_NFC,
            f"{plural(len(found), 'mask type')} {'has a name' if len(found) == 1 else 'have names'}"
            " that "
            f"{'is' if len(found) == 1 else 'are'} not in Unicode NFC form. The name is never "
            "normalized or renamed for you.",
            found,
            "Rename the mask folders (or the mask file endings) so that each accented character "
            "is a single composed character (NFC), or use names without accents.",
        )
    ]


def invalid_mask_types_issue(names: Iterable[str]) -> list[ValidationIssue]:
    """Return ``R_MASK_TYPE_NAME_INVALID`` for mask types that break the Label rules, or nothing.

    Parameters
    ----------
    names
        Mask type names that :func:`.discovery.check_identity_text` refused.
    """
    found = sorted(set(names))
    if not found:
        return []
    return [
        error(
            MASK_TYPE_NAME_INVALID,
            f"{plural(len(found), 'mask type')} "
            f"{'has a name' if len(found) == 1 else 'have names'} that cannot identify a "
            "mask type: the name starts or ends with a space, or is NA, nan, none, null, n/a "
            "or <NA> (in any case), which tables read as a missing value.",
            found,
            "Rename these mask folders (or the mask file endings), without spaces at the start "
            "or end, and not NA, nan, none, null, n/a or <NA>.",
        )
    ]
