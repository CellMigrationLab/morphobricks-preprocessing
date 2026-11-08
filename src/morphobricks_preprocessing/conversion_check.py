"""Check a copy tree for :class:`.run_inputs.RunInputs` (contract §9.1, §17, PP-11c).

A run on named-axis copies records its conversion record's SHA-256
(``conversion_record_sha256``). When the run is opened, the record must be the same file,
of the current record version, and each copy the run recorded must have the fingerprint
and the SHA-256 the record gives (issue #87). Each copy is read whole, once, when the run is
opened: one read of the copies the run recorded, never skipped. Reads after that check the
fingerprint, as for any image.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from .conversion_record import (
    RECORD_FILENAME,
    ConversionRecord,
    ConversionRecordError,
    changed_copies,
    read_record,
)
from .manifest import file_sha256
from .projection_run import run_projection
from .validation import agree, plural


def conversion_problem(
    root: Path,
    expected: str | None,
    paths: Iterable[str],
    projection: Mapping | None = None,
    masks: Iterable[tuple[str, str]] = (),
) -> str | None:
    """Return why a run on named-axis copies cannot be used, or None when it can.

    Parameters
    ----------
    root
        The copy tree (the run's experiment folder now).
    expected
        The record's SHA-256 the run recorded.
    paths
        The images the run recorded, relative to ``root``.
    projection
        The manifest's ``projection`` entry, or None. It must be exactly what the record
        gives for ``paths`` and ``masks``: projected copies are never read as plain images.
    masks
        ``(image, mask path)`` of the masks the run recorded.

    Returns
    -------
    str or None
        The message for the user, or None.
    """
    path = root / RECORD_FILENAME
    if expected is None:
        return None if projection is None else _no_record(path, root)
    if not path.exists() or file_sha256(path) != expected:
        return (
            f"The conversion record {path.name} in {root} is not the one the run read "
            "(missing, or changed since), so it is not known how these named-axis copies "
            "were made. Run preprocessing again on the copies (or, if the copies were only "
            "moved, give experiment_root= where they are now)."
        )
    try:
        found = read_record(root)
    except ConversionRecordError as exc:
        return (
            f"The conversion record {path.name} in {root} cannot be used ({exc}). Convert the "
            "originals again into a new folder and run preprocessing on that one."
        )
    changed = changed_copies(root, found[0], paths) if found is not None else []
    if not changed:
        return _projection_problem(found[0], expected, list(paths), projection, masks)
    them = agree(changed, "it", "them")
    return (
        f"{plural(len(changed), 'named-axis copy', 'named-axis copies')} changed since the "
        f"conversion wrote {them} (or the conversion record does not list {them}): "
        f"{', '.join(changed[:5])}{' and more' if len(changed) > 5 else ''}. Convert the "
        "originals again into a new folder and run preprocessing on that one."
    )


def _no_record(path: Path, root: Path) -> str:
    """Say that a run recorded a projection but no conversion record to read it from."""
    return (
        f"The run recorded Z-projected images, but no conversion record {path.name} (a run "
        f"records the record's SHA-256), so it is not known how the images in {root} were "
        "made. Run preprocessing again on the projected copies."
    )


def _projection_problem(
    record: ConversionRecord,
    sha256: str,
    paths: list[str],
    recorded: Mapping | None,
    masks: Iterable[tuple[str, str]],
) -> str | None:
    """Return why the manifest's projection is not what the record says, or None.

    Projected copies recorded without their projection (or the reverse, or with other
    slices, method, types or source SHA-256) are refused, never read as plain images.
    """
    expected = run_projection(record, sha256, paths, masks)
    if (None if expected is None else expected.to_dict()) == recorded:
        return None
    what = "no projection" if recorded is None else "a projection that differs from the record"
    return (
        f"The run recorded {what}, but its conversion record says how these images were "
        "made (Z-projected copies, or not): the manifest and the record disagree, so these "
        "images are not read as plain images. Run preprocessing again on the projected copies."
    )
