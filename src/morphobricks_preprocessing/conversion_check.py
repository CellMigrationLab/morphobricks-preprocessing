"""Check a copy tree for :class:`.run_inputs.RunInputs` (contract §9.1, §17, PP-11c).

A run on named-axis copies records its conversion record's SHA-256
(``conversion_record_sha256``). When the run is opened, the record must be the same file,
of the current record version, and each copy the run recorded must have the fingerprint
and the SHA-256 the record gives (issue #87). Each copy is read whole, once, when the run is
opened: one read of the copies the run recorded, never skipped. Reads after that check the
fingerprint, as for any image.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .conversion_record import (
    RECORD_FILENAME,
    ConversionRecordError,
    changed_copies,
    read_record,
)
from .manifest import file_sha256
from .validation import agree, plural


def conversion_problem(root: Path, expected: str, paths: Iterable[str]) -> str | None:
    """Return why a run on named-axis copies cannot be used, or None when it can.

    Parameters
    ----------
    root
        The copy tree (the run's experiment folder now).
    expected
        The record's SHA-256 the run recorded.
    paths
        The images the run recorded, relative to ``root``.

    Returns
    -------
    str or None
        The message for the user, or None.
    """
    path = root / RECORD_FILENAME
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
        return None
    them = agree(changed, "it", "them")
    return (
        f"{plural(len(changed), 'named-axis copy', 'named-axis copies')} changed since the "
        f"conversion wrote {them} (or the conversion record does not list {them}): "
        f"{', '.join(changed[:5])}{' and more' if len(changed) > 5 else ''}. Convert the "
        "originals again into a new folder and run preprocessing on that one."
    )
