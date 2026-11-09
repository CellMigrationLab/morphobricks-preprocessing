"""Reopening a published description: the file, how the folders changed, what must be answered.

Reads the output folder's ``morphobricks.toml`` with the strict reader (a symbolic link is
refused) and compares it with the folders found now (:func:`.description_reconcile.reconcile`,
units matched by their exact folder labels only). A removed folder is never dropped silently: it
comes back as a grouped question per condition (:func:`.description_removal.removal_questions`),
answered by the user's "Yes, remove". Nothing is written here.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .description import Description, DescriptionError, description_from_bytes
from .description_file import DESCRIPTION_FILENAME, read_file_bytes, sha256_hex
from .description_reconcile import Reconciliation, reconcile
from .description_removal import RemovalQuestion, removal_questions
from .discovery import DiscoveryResult
from .metadata import TiffMetadata
from .reconcile_observe import observe_folders


@dataclass(frozen=True)
class PublishedState:
    """The published file as read, and how the folders now differ from it.

    ``saved_on`` is the calendar day the file was last written (from the file system, shown to
    the user only: it is never written into a description or a record).
    """

    description: Description
    sha256: str
    saved_on: str
    reconciliation: Reconciliation
    removals: tuple[RemovalQuestion, ...]

    @property
    def changed(self) -> bool:
        """True when the folders or headers differ from the saved file.

        A folder is new or removed, a member's image count changed, or a unit's headers no
        longer fit its saved channels (any reason that reopens a repeat).
        """
        rec = self.reconciliation
        return bool(rec.new or rec.removed or rec.counts or rec.reopened)


def published_state(
    output: Path,
    discovery: DiscoveryResult,
    excluded: Collection[str] = (),
    metadata: Iterable[TiffMetadata] | None = None,
) -> tuple[PublishedState | None, str]:
    """Return the published file compared with the folders, or None and why it cannot be used.

    ``metadata`` are the image headers already read (the preflight's): with them each unit's
    channel count and header names are compared with its saved channels, so a header changed
    under the same files is a change, never "nothing changed".

    Returns
    -------
    tuple
        ``(None, "")`` without a published file; ``(None, reason)`` when it cannot be read
        (the reader's codes are named); else the state and ``""``.
    """
    path = output / DESCRIPTION_FILENAME
    try:
        data = read_file_bytes(path)
        if data is None:
            return None, ""
        published = description_from_bytes(data)
    except DescriptionError as exc:
        codes = ", ".join(sorted({i.code.value for i in exc.issues}))
        return None, f"{DESCRIPTION_FILENAME} cannot be used ({codes})"
    except OSError as exc:
        return None, f"{DESCRIPTION_FILENAME} cannot be read: {exc}"
    rec = reconcile(published, observe_folders(discovery, excluded, metadata))
    day = date.fromtimestamp(path.stat().st_mtime).isoformat()
    return PublishedState(
        published, sha256_hex(data), day, rec, removal_questions(published, rec)
    ), ""


__all__ = ["PublishedState", "published_state"]
