"""The masks as the run manifest records them (manifest version 2, contract §9, §15).

Every mask entry adds to what :meth:`.masks.MaskInventory.to_manifest` gives its
fingerprint (``size``, ``mtime_ns``), so a brick can tell a mask changed since the run, and
how it is read: ``roles`` (one per axis of ``axes``), ``axes_from`` (``"header"``: the
mask's metadata names them) and ``shared`` (a 2-D mask stated as shared by every plane of
its image; never without a statement).
"""

from __future__ import annotations

import os
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
        ``roles``, ``axes_from`` and ``shared``.
    """
    data = inventory.to_manifest()
    by_path = {mask.path: mask for mask in inventory.files}
    for entry in data["files"]:
        mask = by_path[entry["path"]]
        fingerprint = Fingerprint.of(_mask_path(mask, inventory.folder, Path(experiment_root)))
        entry.update(
            size=fingerprint.size,
            mtime_ns=fingerprint.mtime_ns,
            roles=mask.axes,
            axes_from="header",
            shared=False,
        )
    return data


def _mask_path(mask: MaskFile, folder: Path | None, root: Path) -> Path:
    """Return where a mask is on disk: in the masks folder, or inside its OME-Zarr image."""
    return (folder / mask.path) if mask.source == FOLDER else (root / mask.path)
