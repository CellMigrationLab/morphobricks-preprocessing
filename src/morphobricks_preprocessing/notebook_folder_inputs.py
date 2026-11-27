"""What a folder check or scan is of, and which of its failures are expected (bc B5).

Moved out of :mod:`.notebook_folder` (v0.9.1) to keep it under the 600-line rule.
"""

from __future__ import annotations

from . import notebook_style as ui


def expected_check_failure(exc: BaseException) -> bool:
    """Return True for a failure the folder check reports and survives (issue #101).

    A storage error (``OSError``: a Google Drive that stopped answering, a folder gone) or
    one of this package's coded refusals (an ``Exception`` with a ``code``); anything else is
    a programming error or an interrupt, raised after the earlier check is cleared.
    ``SystemExit("message")`` has a str ``code`` too, but it is not an ``Exception``: it
    propagates.
    """
    return isinstance(exc, OSError) or (
        isinstance(exc, Exception) and isinstance(getattr(exc, "code", None), str)
    )


class FoldersEditedError(Exception):
    """The folder boxes were edited while a check read them (review bc B5): not used."""

    code = "folders_edited_during_check"


def check_inputs(app: object) -> tuple:
    """Return the folders and exclusions as typed now: what a scan or a folder check is of.

    Captured before the folders are read and compared after: a result of other folders (the
    boxes edited meanwhile) is not used (review bc B5).
    """
    return (
        ui.clean_path(app.folder.value),
        app._output_folder(),
        ui.clean_path(app.masks_folder.value),
        tuple(app._excluded()),
    )


__all__ = ["FoldersEditedError", "check_inputs", "expected_check_failure"]
