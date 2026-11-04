"""The masks as the run manifest records them (manifest version 2, contract §9, §15).

Every mask entry adds to what :meth:`.masks.MaskInventory.to_manifest` gives its
fingerprint (``size``, ``mtime_ns``, and ``inventory_sha256`` for an OME-Zarr label image,
issue #86), so a brick can tell a mask changed since the run, and
how it is read: ``roles`` (one per axis of ``axes``), ``axes_from`` (``"header"``: the
mask's metadata names them; ``"stated"``: the user stated them for its mask type) and
``shared`` (a 2-D mask stated as shared by every plane of its image; never without a
statement, :func:`unstated_shared_text`). A label image of an OME-Zarr image has its own
fingerprint, left out of its image's (:func:`.inventory.source_signature`).
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
        ``roles``, ``axes_from`` and ``shared``. ``axes`` are the mask's axes as stored
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
            shared=mask.shared,
        )
    return data


def _mask_path(mask: MaskFile, folder: Path | None, root: Path) -> Path:
    """Return where a mask is on disk: in the masks folder, or inside its OME-Zarr image."""
    return (folder / mask.path) if mask.source == FOLDER else (root / mask.path)


def unstated_shared_text(records: Iterable[Mapping]) -> str | None:
    """Say why recorded masks shared without a statement are refused, or return None.

    Only a 2-D mask whose type the user stated ``YX`` is shared by every plane of its
    image (owner decision of 4 November 2026). Main between v0.4.0rc5 and that decision
    (#148, F10) also recorded a 2-D mask whose own metadata names ``YX`` as shared
    (``shared: true`` with ``axes_from: "header"``); such a record is never trusted.

    Parameters
    ----------
    records
        The manifest's mask entries (``masks["files"]``).

    Returns
    -------
    str or None
        The refusal, naming the masks and the statement to add per mask type, or None.
    """
    unstated = [r for r in records if r.get("shared") is True and r.get("axes_from") != "stated"]
    if not unstated:
        return None
    paths = ", ".join(str(r.get("path")) for r in unstated)
    types = sorted({str(r.get("type")) for r in unstated})
    options = " ".join(f"--mask-axes {name}=YX" for name in types)
    return (
        "The run recorded 2-D masks as shared by every plane of their image without a "
        f"statement, because their metadata names YX: {paths}. A mask is shared only when "
        "its type is stated YX. Run preprocessing again, stating it if one 2-D mask is "
        f"meant for every plane ({options}; in the notebook, YX under Mask axes), or save "
        "one mask plane per depth slice and time point."
    )
