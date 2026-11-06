"""Masks bound to their fingerprints for a whole run and for the mask step's scan.

Images are bound to their fingerprints from the moment their headers are read (issue #90,
:mod:`.run.resume`); masks are bound the same way (issue #160). In a run
(:func:`.run.run_experiment`) each mask is fingerprinted before and after its header is read
(:attr:`.masks.MaskFile.bound`; ``mask_changed_during_check`` when the two differ). In a
``verify_content`` run each mask's content SHA-256 is also taken once the header checks pass
(:func:`mask_contents`). Before the commit every mask must still have that fingerprint
(:func:`changed_masks`), and in a ``verify_content`` run that content
(:func:`content_changed_masks`); otherwise the run stops with ``mask_changed_during_run``
and nothing is published. The manifest records the fingerprint bound to the header.

The mask step (:func:`.mask_scan.scan_masks`, issue #165) binds them too: each image's masks
are fingerprinted (and, with ``verify_content``, hashed) before their pixels are read and
again after; a mask whose fingerprint is not the one its header was bound to, or that changes
during its scan, raises :class:`MaskChangedError` (``mask_changed_during_scan``), and no
report is written.

The identity is the metadata fingerprint (size, modification time, and for an OME-Zarr
label image its inventory digest); the content SHA-256 is opt-in (``verify_content``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from .fingerprint import Fingerprint
from .masks import MaskFile, MaskInventory
from .source_content import current_content
from .validation import ValidationIssue, error, plural

MASK_CHANGED_DURING_RUN = "mask_changed_during_run"
"""The code of a run refused because a mask changed after its header was read."""
MASK_CHANGED_DURING_SCAN = "mask_changed_during_scan"
"""The code of a mask step refused because a mask changed before or while it was read."""

CHANGE_TEXTS = {
    "fingerprint": (
        "their size or modification time, or for an OME-Zarr label image its files, changed"
    ),
    "fingerprint or content": "their size, modification time, files or content (SHA-256) changed",
    "content": "their content (SHA-256) changed, even if their size and time did not",
}
"""What changed, by what was compared."""


def mask_fingerprint(mask: MaskFile, folder: Path | None, root: Path) -> Fingerprint | None:
    """Return a mask's fingerprint now, or None when it is gone or cannot be read.

    Parameters
    ----------
    mask
        The mask.
    folder, root
        The masks folder and the experiment folder (:meth:`.masks.MaskFile.location`).

    Returns
    -------
    Fingerprint or None
    """
    try:
        return Fingerprint.of(mask.location(folder, root))
    except OSError:
        return None


def changed_masks(inventory: MaskInventory, root: Path) -> list[str]:
    """Return the masks whose fingerprint now is not the one bound to their header.

    Parameters
    ----------
    inventory
        The masks, found with ``bind=True`` (:func:`.masks.find_masks`).
    root
        The experiment folder.

    Returns
    -------
    list of str
        The changed, missing or unbound masks (a mask never bound is never trusted), sorted.
    """
    return sorted(
        mask.path
        for mask in inventory.files
        if mask.bound is None or mask_fingerprint(mask, inventory.folder, root) != mask.bound
    )


def mask_contents(inventory: MaskInventory, root: Path) -> dict[str, str | None]:
    """Return each mask's content SHA-256 (``verify_content``), None for one not readable.

    Parameters
    ----------
    inventory
        The masks.
    root
        The experiment folder.

    Returns
    -------
    dict
        ``{mask path: SHA-256 or None}``, as :func:`.source_content.source_content_sha256`
        gives it (a TIFF's bytes; a label image's files).
    """
    return {
        mask.path: current_content(mask.location(inventory.folder, root))
        for mask in inventory.files
    }


def content_changed_masks(
    inventory: MaskInventory, root: Path, contents: Mapping[str, str | None]
) -> list[str]:
    """Return the masks whose content SHA-256 now is not the one in ``contents``.

    Parameters
    ----------
    inventory
        The masks.
    root
        The experiment folder.
    contents
        Their SHA-256 as taken earlier (:func:`mask_contents`).

    Returns
    -------
    list of str
        The changed, unreadable or unhashed masks, sorted.
    """
    return sorted(
        mask.path
        for mask in inventory.files
        if contents.get(mask.path) is None
        or current_content(mask.location(inventory.folder, root)) != contents[mask.path]
    )


def mask_changed_issue(
    paths: Iterable[str], compared: str = "fingerprint", *, scan: bool = False
) -> ValidationIssue:
    """Return the error naming the masks that changed while a run or the mask step used them.

    Parameters
    ----------
    paths
        The masks, sorted.
    compared
        What was compared, a key of :data:`CHANGE_TEXTS`.
    scan
        The mask step's scan (``mask_changed_during_scan``) rather than a run
        (``mask_changed_during_run``).

    Returns
    -------
    ValidationIssue
    """
    paths = sorted(paths)
    count = plural(len(paths), "mask")
    if scan:
        return error(
            MASK_CHANGED_DURING_SCAN,
            f"{count} changed or went missing before or while the mask step read them "
            f"({CHANGE_TEXTS[compared]}), so a relationship report could mix versions of "
            "them. No report was written; an earlier mask_relationships.json is left as it "
            "was and describes the masks as they were when it was written.",
            paths,
            "Wait until nothing is writing to these masks (a segmentation or a copy still "
            "running), then describe the masks again.",
        )
    return error(
        MASK_CHANGED_DURING_RUN,
        f"{count} changed or went missing while the run was using them "
        f"({CHANGE_TEXTS[compared]}), so the run's record of them would not describe them. "
        "Nothing was published.",
        paths,
        "Wait until nothing is writing to these masks (a segmentation or a copy still "
        "running), then run again: the images already read resume from their staged rows.",
    )


def scan_fingerprints(
    masks: Iterable[MaskFile], folder: Path | None, root: Path, verify_content: bool
) -> dict[str, tuple]:
    """Return each mask's fingerprint (and content) before the scan reads it (issue #165).

    A mask found with ``bind=True`` must still have the fingerprint taken with its header:
    its header (planes, axes) describes it only as it was then.

    Parameters
    ----------
    masks
        The masks of one image.
    folder, root
        The masks folder and the experiment folder.
    verify_content
        Also take each mask's content SHA-256.

    Returns
    -------
    dict
        ``{mask path: (fingerprint, SHA-256 or None)}``, for :func:`check_scanned`.

    Raises
    ------
    MaskChangedError
        For masks gone, unreadable, or no longer with the fingerprint bound to their header.
    """
    masks = list(masks)
    found = {
        mask.path: (
            mask_fingerprint(mask, folder, root),
            current_content(mask.location(folder, root)) if verify_content else None,
        )
        for mask in masks
    }
    changed = [
        mask.path
        for mask in masks
        if found[mask.path][0] is None
        or (mask.bound is not None and found[mask.path][0] != mask.bound)
        or (verify_content and found[mask.path][1] is None)
    ]
    if changed:
        raise MaskChangedError(
            changed, "fingerprint or content" if verify_content else "fingerprint"
        )
    return found


def check_scanned(
    masks: Iterable[MaskFile],
    folder: Path | None,
    root: Path,
    before: Mapping[str, tuple],
    hashed: bool,
) -> None:
    """Raise :class:`MaskChangedError` for the masks that changed while the scan read them.

    Parameters
    ----------
    masks
        The masks of one image, as given to :func:`scan_fingerprints`.
    folder, root
        The masks folder and the experiment folder.
    before
        :func:`scan_fingerprints`'s answer.
    hashed
        Compare the content SHA-256 too (``verify_content``).

    Raises
    ------
    MaskChangedError
        For the masks whose fingerprint (or content) is not the one taken before.
    """
    changed = [
        mask.path
        for mask in masks
        if mask_fingerprint(mask, folder, root) != before[mask.path][0]
        or (hashed and current_content(mask.location(folder, root)) != before[mask.path][1])
    ]
    if changed:
        raise MaskChangedError(changed, "fingerprint or content" if hashed else "fingerprint")


class MaskChangedError(ValueError):
    """Raised when masks changed before or while the mask step read them (issue #165).

    A ``ValueError``, as every other mask that cannot be used in the scan.

    Attributes
    ----------
    paths
        The masks, sorted.
    compared
        What was compared, a key of :data:`CHANGE_TEXTS`.
    issue
        The error ``mask_changed_during_scan``.
    """

    def __init__(self, paths: Iterable[str], compared: str = "fingerprint") -> None:
        self.paths = sorted(paths)
        self.compared = compared
        self.issue = mask_changed_issue(self.paths, compared, scan=True)
        super().__init__(
            f"{self.issue.message} ({MASK_CHANGED_DURING_SCAN}: {', '.join(self.paths)})"
        )
