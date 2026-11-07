"""The mask step's representation check and its recorded status (experiment-v1 section 4.5a).

A mask declared ``binary`` in the description must not hold more than two distinct values
(``R_MASK_CONTRADICTS_REPRESENTATION``); pixels cannot prove ``instances``. Publishing a
description reads no mask pixels, so a mask file is ``not yet checked`` until the mask step
has read it, and a file added later is ``not yet checked`` again. The mask step records what
it checked in ``mask_representation.json`` in the output folder: for each declared mask, the
representation and source type it checked against, and each checked file with the
fingerprint it had when its pixels were read (and unchanged to the end of the read, the
binding of :mod:`.mask_binding`). A file counts as checked only when its fingerprint, the
representation and the source type are still the ones recorded; a missing, unreadable or
foreign status file means nothing was checked. A file that contradicts its representation is
never recorded as checked.

``instances`` files are recorded as checked once the mask step has read them: the step reads
every mask of the experiment and refuses a label outside the integer range, and nothing more
can be proved about instances from pixels.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ._io import write_text_atomic
from .description import Description
from .description_run_text import representation_issue
from .fingerprint import Fingerprint
from .mask_binding import check_scanned, mask_fingerprint, scan_fingerprints
from .masks import MaskFile, MaskInventory, read_mask
from .validation import ValidationIssue

REPRESENTATION_FILENAME = "mask_representation.json"
"""The status file the mask step writes next to ``mask_relationships.json``."""
REPRESENTATION_VERSION = "1"
CHECKED = "checked"
NOT_CHECKED = "not yet checked"
BINARY = "binary"
MAX_BINARY_VALUES = 2
"""A binary mask holds at most this many distinct values."""


@dataclass(frozen=True)
class RepresentationResult:
    """What the mask step checked: the file written and the contradictions found."""

    issues: tuple[ValidationIssue, ...]
    checked: Mapping[str, tuple[str, ...]]
    """Mask ID -> the mask files recorded as checked, sorted."""


def _distinct(mask: MaskFile, folder: Path | None, root: Path) -> int:
    """Return the number of distinct values over every plane of a mask (stops past two)."""
    values: set[int] = set()
    for z in range(mask.planes["Z"]):
        for t in range(mask.planes["T"]):
            pixels = read_mask(
                mask,
                folder,
                root,
                z=z if "Z" in mask.axes else None,
                t=t if "T" in mask.axes else None,
            )
            values.update(int(v) for v in np.unique(pixels))
            if len(values) > MAX_BINARY_VALUES:
                return len(values)
    return len(values)


def check_representation(
    description: Description,
    inventory: MaskInventory,
    experiment_root: str | os.PathLike,
    output_folder: str | os.PathLike,
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

    Returns
    -------
    RepresentationResult
        One ``R_MASK_CONTRADICTS_REPRESENTATION`` error per contradicting file.

    Raises
    ------
    MaskChangedError
        A mask changed before or while it was read: nothing is written, an earlier status
        file is left as it was.
    ValueError
        For a mask whose pixels cannot be read (as :func:`.masks.read_mask`).
    """
    root = Path(experiment_root).resolve()
    issues: list[ValidationIssue] = []
    record: dict[str, dict] = {}
    for mask_id, mask in sorted(description.masks.items()):
        files = []
        for found in (f for f in inventory.files if f.mask_type == mask.source_type):
            before = scan_fingerprints([found], inventory.folder, root, False)
            values = (
                _distinct(found, inventory.folder, root) if mask.representation == BINARY else 0
            )
            check_scanned([found], inventory.folder, root, before, False)
            if values > MAX_BINARY_VALUES:
                issues.append(
                    representation_issue(mask_id, mask.representation, found.path, values)
                )
                continue
            data = mask_fingerprint(found, inventory.folder, root).to_data()
            files.append({"path": found.path, **data})
        record[mask_id] = {
            "source_type": mask.source_type,
            "representation": mask.representation,
            "files": sorted(files, key=lambda item: item["path"]),
        }
    document = {"version": REPRESENTATION_VERSION, "masks": record}
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    write_text_atomic(Path(output_folder) / REPRESENTATION_FILENAME, text)
    checked = {k: tuple(f["path"] for f in v["files"]) for k, v in record.items()}
    return RepresentationResult(tuple(issues), checked)


def read_checked(output_folder: str | os.PathLike) -> Mapping[str, Mapping]:
    """Return the status file's ``masks`` table, or empty when there is none to trust."""
    path = Path(output_folder) / REPRESENTATION_FILENAME
    try:
        if path.is_symlink():
            return {}
        document = json.loads(path.read_text(encoding="utf-8"))
        masks = document["masks"]
        if document["version"] != REPRESENTATION_VERSION or not isinstance(masks, dict):
            return {}
        return masks
    except (OSError, ValueError, KeyError, TypeError):
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
    same fingerprint, for the same representation and source type.
    """
    found = table.get(mask_id)
    if (
        not isinstance(found, Mapping)
        or found.get("source_type") != source_type
        or found.get("representation") != representation
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
