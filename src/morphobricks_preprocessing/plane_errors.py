"""The errors of the plane and window readers (:mod:`.planes`, :mod:`.windows`).

They live on their own so that the TIFF piece reader (:mod:`.tiff_pieces`), which
:mod:`.planes` uses, can raise them; :mod:`.planes` still exports them.
"""

from __future__ import annotations


class PlaneReadError(Exception):
    """Raised when a file's planes cannot be read under the given axis mapping."""


class PlaneMemoryError(PlaneReadError):
    """Raised when reading a plane would need more memory than the limit allows."""
