"""One image of a copy tree, planned: what :mod:`.conversion_plan` hands to the writer."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .axes import AxisMapping
from .conversion_record import CopyEntry
from .copy_axes import CopyLayout


@dataclass(frozen=True)
class PlannedCopy:
    """One image of the copy tree, before it is written.

    Attributes
    ----------
    entry
        Its conversion-record entry, without the copy's fingerprint.
    layout
        The named-axis copy of a converted image, else None.
    mapping
        The roles a converted image is read with, else None.
    nbytes
        The bytes the copy takes (pixels only for a converted or projected image).
    dtype
        The pixel type a converted or projected image's header gives (native byte order,
        the source's: a projected copy's own type is in its entry), else None.
    store
        ``"ome-tiff"`` (a named-axis OME-TIFF) or ``"ome-zarr"`` (:mod:`.ome_zarr_copy`) for
        a converted or projected image.
    """

    entry: CopyEntry
    layout: CopyLayout | None = None
    mapping: AxisMapping | None = None
    nbytes: int = 0
    dtype: np.dtype | None = None
    store: str = "ome-tiff"

    @property
    def source_path(self) -> str:
        """The source image, relative to the experiment (the copy's path unless renamed)."""
        return self.entry.source_path

    @property
    def relative_path(self) -> str:
        """The copy, relative to the copy tree (the source's path too unless renamed)."""
        return self.entry.relative_path
