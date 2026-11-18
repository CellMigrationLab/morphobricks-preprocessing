"""The errors of the plane and window readers (:mod:`.planes`, :mod:`.windows`).

They live on their own so that the TIFF piece reader (:mod:`.tiff_pieces`), which
:mod:`.planes` uses, can raise them; :mod:`.planes` still exports them.
"""

from __future__ import annotations


class PlaneReadError(Exception):
    """Raised when a file's planes cannot be read under the given axis mapping."""


class PlaneMemoryError(PlaneReadError):
    """Raised when reading a plane would need more memory than the limit allows."""


class CompressionNotBoundedError(PlaneReadError):
    """Raised, before any pixel is read, for a TIFF codec whose read memory is not measured.

    The bounded readers (windows, every channel of a plane at once, blocks of planes over the
    memory limit) read only the codecs of :data:`.codec_bounds.TIFF_BOUNDED` (issue #356).
    """

    code = "compression_not_bounded"
