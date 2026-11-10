"""Check that images are still the ones a preprocessing run read (contract §16).

:func:`check_sources` is the per-image check every brick makes before using a run's table.
Moved from ``manifest.py`` (PP-4).
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from .image_names import position_id
from .inventory import source_signature
from .manifest import RunManifest
from .validation import ValidationIssue, error, plural


def check_sources(
    manifest: RunManifest,
    experiment_root: str | os.PathLike,
    relative_paths: Iterable[str],
    *,
    image_axes: Mapping[str, str] | None = None,
    identities: Mapping[str, Sequence[str]] | None = None,
) -> list[ValidationIssue]:
    """Check that images are still the ones a preprocessing run read, before using its table.

    The per-image checks every brick that uses ``plane_histograms.csv`` makes (MorphoBricks
    D86; formerly CellPatcher ``statistics.check_images`` and MorphoFeature
    ``histogram_fit``): each image is in the manifest; it is unchanged, by the size and
    modification time :func:`.inventory.source_signature` gives (an OME-Zarr folder: its
    files' total size, newest time and inventory digest, issue #86); its recorded
    ``position_id`` is the one its name gives; with
    ``identities``, its labels and ``position_id`` are the caller's; with ``image_axes``,
    the caller's axes give every axis the role the run confirmed for that image. A manifest of
    an older version is not read at all (:mod:`.manifest_versions`).

    Parameters
    ----------
    manifest
        The run's manifest (:func:`read_manifest`).
    experiment_root
        The experiment folder the paths are relative to (the run's, possibly moved).
    relative_paths
        The images to check, relative to the experiment root, with ``/``.
    image_axes
        The axes the caller reads each image with, one letter per dimension (``"ZYX"``),
        by path; every path must have one. A dimension the run gave no role (length 1)
        fits any letter; the others must have the run's role. The roles are the file's
        own as recorded (:attr:`FileEntry.roles`); none recorded fits no axes.
    identities
        The caller's ``(condition, biological_repeat, technical_repeat, position_id)`` of
        each image (:data:`.discovery.FOV_KEY` order), by path; every path must have one.

    Returns
    -------
    list of ValidationIssue
        Errors, each naming its images: ``images_not_in_manifest``, ``images_missing``
        (no longer on disk), ``images_changed``, ``identity_differs`` and
        ``axis_mapping_mismatch``. Empty when all images pass.

    Raises
    ------
    ValueError
        If ``image_axes`` or ``identities`` is given without an entry for every path.
    """
    root = Path(experiment_root)
    paths = list(dict.fromkeys(relative_paths))
    for name, given in (("image_axes", image_axes), ("identities", identities)):
        absent = [path for path in paths if given is not None and path not in given]
        if absent:
            raise ValueError(f"{name} has no entry for {', '.join(absent)}")
    found: dict[str, list[tuple[str, str]]] = {}

    def report(code: str, path: str, detail: str = "") -> None:
        found.setdefault(code, []).append((path, detail))

    for path in paths:
        entry = manifest.file(path)
        if entry is None:
            report("images_not_in_manifest", path)
            continue
        try:
            fingerprint = source_signature(root / path)
        except FileNotFoundError:
            report("images_missing", path)
            continue
        if fingerprint != (entry.size, entry.mtime_ns, entry.inventory_sha256):
            report("images_changed", path)
        recorded = (*entry.labels, entry.position_id)
        if entry.position_id != position_id(path):
            report(
                "identity_differs",
                path,
                f"recorded position_id {entry.position_id!r}, its name gives {position_id(path)!r}",
            )
        elif identities is not None and tuple(identities[path]) != recorded:
            report(
                "identity_differs", path, f"given {tuple(identities[path])}, recorded {recorded}"
            )
        if image_axes is not None:
            roles = entry.roles
            axes = str(image_axes[path]).upper()
            if not _roles_agree(axes, roles):
                described = "none fit" if roles is None else "".join(r or "-" for r in roles)
                report("axis_mapping_mismatch", path, f"given {axes}, preprocessing {described}")
    return [_issue(code, items) for code, items in found.items()]


def _issue(code: str, items: list[tuple[str, str]]) -> ValidationIssue:
    """Build the error of one code, naming its images and their details."""
    message, action = _SOURCE_TEXTS[code]
    message = message.format(n=plural(len(items), "image"))
    details = [f"{path}: {detail}" for path, detail in items if detail]
    if details:
        message += " " + "; ".join(details) + "."
    return error(code, message, sorted(path for path, _ in items), action)


def _roles_agree(axes: str, roles: Sequence[str | None] | None) -> bool:
    """Return True when every axis has the run's role; one without a role (length 1) fits any."""
    if roles is None or len(roles) != len(axes):
        return False
    return all(role is None or role == axis for role, axis in zip(roles, axes, strict=True))


_RERUN = "Run the preprocessing brick again on this experiment, or use the table and manifest of "


_SOURCE_TEXTS = {
    "images_not_in_manifest": (
        "{n} not analysed by the preprocessing run of this manifest: added after it ran, "
        "excluded, or from another experiment.",
        _RERUN + "the run that analysed these images.",
    ),
    "images_missing": (
        "{n} recorded in the manifest no longer exist in the experiment folder (moved, "
        "renamed or deleted, or the experiment folder given is not the run's).",
        "Give the experiment folder the preprocessing run read, or run it again.",
    ),
    "images_changed": (
        "{n} changed after the preprocessing brick read them: the size or modification "
        "time differs from the manifest, so their rows may not describe them.",
        _RERUN + "the images as they were.",
    ),
    "identity_differs": (
        "The labels or position of {n} differ from those the preprocessing run recorded.",
        "Use the labels the preprocessing run confirmed, or run it again with the folders as "
        "they are now.",
    ),
    "axis_mapping_mismatch": (
        "The axes given for {n} give some axes another role than the axis mapping confirmed "
        "in the preprocessing brick.",
        "Use the axis roles confirmed in the preprocessing brick.",
    ),
}
