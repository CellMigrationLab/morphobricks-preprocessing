"""The mask step's representation check and its recorded status (experiment-v1 section 4.5a).

Every file of a mask declared ``binary`` in the description must be stored as integers
(signed or unsigned) or booleans, never floats, and hold, over **all its planes together**,
values in {0, v} for one v > 0 (Spec ADR 0005 BM11, experiment-v1 section 4.5): an all-zero and
an all-foreground file are accepted; a float file, a negative value, or two or more distinct
positive values (also when each plane alone holds one) is ``R_MASK_CONTRADICTS_REPRESENTATION``,
built by the shared validator :func:`.mask_rules.check_binary_values` and recorded with its code
and message as they are. Pixels cannot prove ``instances``. Publishing a
description reads no mask pixels, so a mask file is ``not yet checked`` until the mask step
has read it, and a file added later is ``not yet checked`` again. The mask step records what
it checked in ``mask_representation.json`` in the output folder: for each declared mask, the
representation and source type it checked against, and each checked file with the
fingerprint it had when its pixels were read (and unchanged to the end of the read, the
binding of :mod:`.mask_binding`). A file counts as checked only when its fingerprint, the
representation and the source type are still the ones recorded; a missing, unreadable or
foreign status file means nothing was checked. A file that contradicts its representation is
never recorded as checked.

The record of a ``binary`` mask carries the value rule its files were checked by,
``"value_rule": "BM11"`` (:data:`BINARY_VALUE_RULE`). A ``binary`` record without it was
written by a mask step that predates BM11 (v0.5.6 and earlier, whose rule was "at most two
distinct values": {3, 7} split over two planes passed): its files are ``not yet checked`` until
the mask step runs again (:func:`predates_value_rule`, Spec ADR 0002 AR10: an old form is
refused with its fix named, never read leniently). ``instances`` and ``parent_labels`` records
carry no rule and are unchanged.

``instances`` files are recorded as checked once the mask step has read them: the step reads
every mask of the experiment and refuses a label outside the integer range, and nothing more
can be proved about instances from pixels.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from ._io import write_text_atomic
from .description import Description
from .fingerprint import Fingerprint
from .mask_binding import check_scanned, scan_fingerprints
from .mask_rules import (
    MaskContradictsRepresentationError,
    binary_dtype,
    binary_values_broken,
    check_binary_values,
)
from .masks import MaskFile, MaskInventory
from .masks.read import read_mask_plane
from .validation import ValidationIssue, error

REPRESENTATION_FILENAME = "mask_representation.json"
"""The status file the mask step writes next to ``mask_relationships.json``."""
REPRESENTATION_VERSION = "1"
CHECKED = "checked"
NOT_CHECKED = "not yet checked"
BINARY = "binary"
VALUE_RULE = "value_rule"
"""The key of a ``binary`` mask's record naming the value rule its files were checked by."""
BINARY_VALUE_RULE = "BM11"
"""The value rule this mask step checks ``binary`` files by (Spec ADR 0005 BM11, file-wide)."""


@dataclass(frozen=True)
class RepresentationResult:
    """What the mask step checked: the file written and the contradictions found."""

    issues: tuple[ValidationIssue, ...]
    checked: Mapping[str, tuple[str, ...]]
    """Mask ID -> the mask files recorded as checked, sorted."""


def _distinct(mask: MaskFile, folder: Path | None, root: Path) -> tuple[set[int], np.dtype]:
    """Return the distinct values over every plane of a mask file, and its stored dtype.

    The planes are read as stored (no label check: a negative value is the binary rule's to
    refuse) and their values united; reading stops once the union can no longer pass (a
    negative value or two positive ones), and no pixel of a float file is read.
    """
    values: set[int] = set()
    dtype = None if mask.dtype is None else np.dtype(mask.dtype)
    for z in range(mask.planes["Z"]):
        for t in range(mask.planes["T"]):
            if dtype is not None and not binary_dtype(dtype):
                return values, dtype
            pixels = read_mask_plane(
                mask,
                folder,
                root,
                z=z if "Z" in mask.axes else None,
                t=t if "T" in mask.axes else None,
            )
            dtype = pixels.dtype
            if not binary_dtype(dtype):
                return values, dtype
            values.update(int(v) for v in np.unique(pixels))
            if binary_values_broken(values):
                return values, dtype
    return values, np.dtype(np.uint8) if dtype is None else dtype


def binary_refusals(
    description: Description, inventory: MaskInventory, experiment_root: str | os.PathLike
) -> dict[str, ValidationIssue]:
    """Return the ``R_MASK_CONTRADICTS_REPRESENTATION`` error of every refused ``binary`` file.

    Each file of a mask declared ``binary`` is read between two fingerprints
    (:mod:`.mask_binding`) and checked **file-wide** by :func:`.mask_rules.check_binary_values`
    on the union of its planes' values. The error's code and message are recorded as they are,
    with no action added.

    Returns
    -------
    dict
        Each refused file's path (as in the inventory) to its error; files that pass are not in
        it.

    Raises
    ------
    MaskChangedError
        A mask changed before or while it was read.
    ValueError
        For a mask whose pixels cannot be read as the 2-D planes its header gave.
    """
    root = Path(experiment_root).resolve()
    refused: dict[str, ValidationIssue] = {}
    for mask in description.masks.values():
        if mask.representation != BINARY:
            continue
        for found in (f for f in inventory.files if f.mask_type == mask.source_type):
            before = scan_fingerprints([found], inventory.folder, root, False)
            values, dtype = _distinct(found, inventory.folder, root)
            check_scanned([found], inventory.folder, root, before, False)
            try:
                check_binary_values(values, dtype=dtype, path=found.path)
            except MaskContradictsRepresentationError as exc:
                refused[found.path] = error(exc.code, str(exc), (found.path,))
    return refused


def check_representation(
    description: Description,
    inventory: MaskInventory,
    experiment_root: str | os.PathLike,
    output_folder: str | os.PathLike,
    *,
    refused: Mapping[str, ValidationIssue] | None = None,
) -> RepresentationResult:
    """Check every file of every declared mask and write ``mask_representation.json``.

    Parameters
    ----------
    description
        The published description.
    inventory
        The masks found, with their headers bound to fingerprints (``find_masks(bind=True)``).
    experiment_root, output_folder
        The experiment folder and the output folder (the caller holds its lock).
    refused
        The ``binary`` files already refused by :func:`binary_refusals` on the same inventory
        (the mask step checks them before its scan); None checks them here.

    Returns
    -------
    RepresentationResult
        One ``R_MASK_CONTRADICTS_REPRESENTATION`` error per refused file (its code and message
        those of :func:`.mask_rules.check_binary_values`, no action added).

    Raises
    ------
    MaskChangedError
        A mask changed before or while it was read: nothing is written, an earlier status
        file is left as it was.
    ValueError
        For a mask whose pixels cannot be read as the 2-D planes its header gave, or found
        without its header bound to a fingerprint.

    Notes
    -----
    Each file is recorded with exactly the fingerprint verified against the one bound to its
    checked header (:func:`_verified`), the snapshot :func:`binary_refusals` read a ``binary``
    file's values between: a file replaced after that verification is recorded with the
    fingerprint it had when checked, so the replacement is ``not yet checked``.
    """
    root = Path(experiment_root).resolve()
    if refused is None:
        refused = binary_refusals(description, inventory, root)
    issues: list[ValidationIssue] = []
    record: dict[str, dict] = {}
    for mask_id, mask in sorted(description.masks.items()):
        files = []
        for found in (f for f in inventory.files if f.mask_type == mask.source_type):
            if mask.representation == BINARY and found.path in refused:
                issues.append(refused[found.path])
                continue
            files.append({"path": found.path, **_verified(found, inventory.folder, root)})
        entry: dict[str, object] = {
            "source_type": mask.source_type,
            "representation": mask.representation,
        }
        if mask.representation == BINARY:
            entry[VALUE_RULE] = BINARY_VALUE_RULE
        entry["files"] = sorted(files, key=lambda item: item["path"])
        record[mask_id] = entry
    document = {"version": REPRESENTATION_VERSION, "masks": record}
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    write_text_atomic(Path(output_folder) / REPRESENTATION_FILENAME, text)
    checked = {k: tuple(f["path"] for f in v["files"]) for k, v in record.items()}
    return RepresentationResult(tuple(issues), checked)


def _verified(found: MaskFile, folder: Path | None, root: Path) -> dict:
    """Return the fingerprint to record for a checked file: the one just verified (audit P1).

    It is :func:`.mask_binding.scan_fingerprints`' own answer, which equals the fingerprint
    bound to the checked header and so the one :func:`binary_refusals` read the values
    between; never a second fingerprint taken after the verification, which a file replaced
    meanwhile would give. A file without a bound header (found without ``bind=True``) has no
    checked header to record against and is refused.
    """
    if found.bound is None:
        raise ValueError(
            f"the mask {found.path} was found without its header bound to a fingerprint "
            "(find_masks(bind=True)); its check cannot be recorded"
        )
    fingerprint = scan_fingerprints([found], folder, root, False)[found.path][0]
    return fingerprint.to_data()


def read_checked(output_folder: str | os.PathLike) -> Mapping[str, Mapping]:
    """Return the status file's ``masks`` table, or empty when there is none to trust.

    The file is read on every call; its parse is reused for the same bytes only
    (:func:`_parsed_checked`, PP-1), so a status file the mask step rewrote is parsed again.
    The table is shared between calls on the same bytes: read it, never change it.
    """
    path = Path(output_folder) / REPRESENTATION_FILENAME
    try:
        if path.is_symlink():
            return {}
        data = path.read_bytes()
    except OSError:
        return {}
    return _parsed_checked(data)


@lru_cache(maxsize=8)
def _parsed_checked(data: bytes) -> Mapping[str, Mapping]:
    """Return the ``masks`` table of a status file's bytes (cached by content), else empty."""
    try:
        document = json.loads(data.decode("utf-8"))
        masks = document["masks"]
        if document["version"] != REPRESENTATION_VERSION or not isinstance(masks, dict):
            return {}
        return masks
    except (ValueError, KeyError, TypeError):
        return {}


def file_status(
    table: Mapping[str, Mapping],
    mask_id: str,
    source_type: str,
    representation: str,
    entry: Mapping,
) -> str:
    """Return ``checked`` or ``not yet checked`` for one recorded mask file.

    ``entry`` is the file's record (manifest ``masks.files`` entry or the like) with its
    ``path`` and fingerprint; it is checked only if the status file holds that path with the
    same fingerprint, for the same representation and source type, and, for a ``binary``
    mask, by the current value rule (:func:`predates_value_rule`).
    """
    found = table.get(mask_id)
    if (
        not isinstance(found, Mapping)
        or found.get("source_type") != source_type
        or found.get("representation") != representation
        or predates_value_rule(table, mask_id, source_type, representation)
    ):
        return NOT_CHECKED
    try:
        for item in found.get("files", ()):
            if item["path"] == entry["path"]:
                same = Fingerprint.recorded(item) == Fingerprint.recorded(entry)
                return CHECKED if same else NOT_CHECKED
    except (KeyError, TypeError, ValueError):
        return NOT_CHECKED
    return NOT_CHECKED


def predates_value_rule(
    table: Mapping[str, Mapping], mask_id: str, source_type: str, representation: str
) -> bool:
    """Return True when a ``binary`` mask's record was written before the value rule BM11.

    The record is for the representation and source type declared now, and is ``binary``, but
    does not name :data:`BINARY_VALUE_RULE`: a mask step older than BM11 wrote it, so none of
    its files counts as checked (the refusal names the fix: run the mask step again).
    ``instances`` and ``parent_labels`` records are never stale.
    """
    found = table.get(mask_id)
    return (
        representation == BINARY
        and isinstance(found, Mapping)
        and found.get("source_type") == source_type
        and found.get("representation") == BINARY
        and found.get(VALUE_RULE) != BINARY_VALUE_RULE
    )
