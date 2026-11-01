"""A module declared pure that writes a file."""

from __future__ import annotations

import os


def save(text: str) -> None:
    """Write text."""
    with open(os.devnull, "w", encoding="utf-8") as stream:
        stream.write(text)
