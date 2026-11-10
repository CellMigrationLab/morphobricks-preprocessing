"""Read the lab-tool links file a run is given (``run --lab-links FILE``, :mod:`.lab_links`)."""

from __future__ import annotations

import os
from pathlib import Path

from .lab_links import LabLinks, LabLinksError, parse_lab_links


def read_lab_links(path: str | os.PathLike, *, confirmed: bool = False) -> LabLinks:
    """Read and check a lab-tool links JSON file.

    Parameters
    ----------
    path
        The file.
    confirmed
        Whether the user confirmed its repeat codes for this run (``--confirm-repeat-codes``).

    Returns
    -------
    LabLinks

    Raises
    ------
    LabLinksError
        If the file cannot be read or its content is refused; the message names the file.
    """
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise LabLinksError(f"The lab-tool links file {path} cannot be read: {exc}") from exc
    try:
        return parse_lab_links(data, confirmed=confirmed)
    except LabLinksError as exc:
        raise LabLinksError(f"The lab-tool links file {path} is refused: {exc}.") from exc
