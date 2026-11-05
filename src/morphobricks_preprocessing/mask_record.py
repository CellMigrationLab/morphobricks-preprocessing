"""The masks as the run manifest records them (manifest version 2, contract §9, §15).

Every mask entry adds to what :meth:`.masks.MaskInventory.to_manifest` gives its
fingerprint (``size``, ``mtime_ns``, and ``inventory_sha256`` for an OME-Zarr label image,
issue #86), so a brick can tell a mask changed since the run, and
how it is read: ``roles`` (one per axis of ``axes``) and ``axes_from`` (``"header"``: the
mask's metadata names them; ``"stated"``: the user stated them for its mask type). The key
``shared`` of a 2-D mask shared by every plane of its image (v0.4.0 release candidates) is
no longer written, and a manifest recording ``shared: true`` is refused
(:func:`shared_mask_text`, owner decision of 5 November 2026). A label image of an OME-Zarr
image has its own fingerprint, left out of its image's (:func:`.inventory.source_signature`).
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from .fingerprint import Fingerprint
from .masks import FOLDER, MaskFile, MaskInventory


def mask_manifest(inventory: MaskInventory, experiment_root: str | os.PathLike) -> dict:
    """Return the manifest's ``masks`` entry, with each mask's fingerprint and roles.

    Parameters
    ----------
    inventory
        The masks found (:func:`.masks.find_masks`).
    experiment_root
        The experiment folder; the label images of OME-Zarr images are inside it.

    Returns
    -------
    dict
        :meth:`.masks.MaskInventory.to_manifest` with, per file, ``size``, ``mtime_ns``,
        ``roles`` and ``axes_from``. ``axes`` are the mask's axes as stored
        (its header's letters, ``"IYX"`` for a plain stack), ``roles`` what they are read as
        (``"TYX"`` when stated), so a brick reads the mask as stored, never reordered.

    Raises
    ------
    ValueError
        For a mask without roles: its header was refused, so the run has an error and
        records no mask without axes.
    """
    unread = [mask.path for mask in inventory.files if not mask.axes]
    if unread:
        raise ValueError(f"Masks without roles cannot be recorded: {', '.join(unread)}")
    data = inventory.to_manifest()
    by_path = {mask.path: mask for mask in inventory.files}
    for entry in data["files"]:
        mask = by_path[entry["path"]]
        fingerprint = Fingerprint.of(_mask_path(mask, inventory.folder, Path(experiment_root)))
        entry.update(
            axes=mask.header_axes or mask.axes,
            **fingerprint.to_data(),
            roles=mask.axes,
            axes_from=mask.axes_from or "header",
        )
    return data


def _mask_path(mask: MaskFile, folder: Path | None, root: Path) -> Path:
    """Return where a mask is on disk: in the masks folder, or inside its OME-Zarr image."""
    return (folder / mask.path) if mask.source == FOLDER else (root / mask.path)


def shared_mask_text(records: Iterable[Mapping]) -> str | None:
    """Say why recorded masks shared by every plane of their image are refused, or None.

    The v0.4.0 release candidates recorded a 2-D mask stated ``YX`` for its type as one
    plane shared by every plane of its image (``shared: true``; main at #148 also without
    a statement). The option was removed (owner decision of 5 November 2026): every mask
    has its image's time points, so such a record is never trusted, stated or not.

    Parameters
    ----------
    records
        The manifest's mask entries (``masks["files"]``).

    Returns
    -------
    str or None
        The refusal, naming the masks, or None.
    """
    shared = [r for r in records if r.get("shared") is True]
    if not shared:
        return None
    paths = ", ".join(str(r.get("path")) for r in shared)
    return (
        "The run recorded 2-D masks as shared by every plane of their image: "
        f"{paths}. That option was removed: MorphoBricks v1 analyses 2-D planes, and a "
        "mask must have the same time points (T) as its image. To use this output folder, "
        "run preprocessing again without the statement (no --mask-axes TYPE=YX): a 2-D "
        "mask of an image with one plane needs none, and for a time-lapse save one mask "
        "plane per time point (TYX) first."
    )
