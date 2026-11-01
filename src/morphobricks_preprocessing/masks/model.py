"""Masks as found: one :class:`MaskFile` per mask, the :class:`MaskInventory` of a run.

Moved from ``masks.py`` (PP-5).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import numpy as np

from ..validation import ValidationIssue, has_errors

FOLDER = "folder"
"""Source of a mask kept as a TIFF file in the masks folder."""


OME_ZARR = "ome-zarr"
"""Source of a mask kept as a label image inside an OME-Zarr image."""


PLANE_AXES = ("Z", "T")
"""The dimensions a mask may have besides Y and X: depth slices and time points."""


_YX = frozenset("YX")
"""The axes of a mask plane, kept by the header and the readers even when of length 1."""


@dataclass(frozen=True)
class MaskFile:
    """One mask of one image.

    Attributes
    ----------
    image
        The image's path relative to the experiment root.
    mask_type
        The mask type: its folder name in the masks folder, or the label image's name.
    path
        The mask's path: relative to the masks folder for a TIFF mask; for a label image of
        an OME-Zarr image, ``<image>/labels/<name>`` relative to the experiment root.
    source
        :data:`FOLDER` or :data:`OME_ZARR`.
    shape
        Shape of the mask without its length-1 axes other than Y and X, ``(Y, X)`` for a 2D
        mask (``(1, 512)`` for a mask one pixel high).
    dtype
        Pixel type, or None when the header could not be read.
    axes
        One letter per entry of ``shape``, from the mask's own metadata: ``"YX"`` for a 2D
        mask, with ``Z`` and/or ``T`` for a mask with several depth slices or time points
        (e.g. ``"TYX"``). Empty when the header could not be read.
    """

    image: str
    mask_type: str
    path: str
    source: str
    shape: tuple[int, ...] = ()
    dtype: np.dtype | None = None
    axes: str = ""

    @property
    def planes(self) -> dict[str, int]:
        """Number of depth slices (``"Z"``) and time points (``"T"``); 1 for an axis it lacks."""
        return {
            axis: self.shape[self.axes.index(axis)] if axis in self.axes else 1
            for axis in PLANE_AXES
        }


@dataclass(frozen=True)
class MaskInventory:
    """Every mask found for the images of an experiment.

    Attributes
    ----------
    folder
        The masks folder, as an absolute path; None when only OME-Zarr label images were used.
    types
        The mask types found, sorted.
    files
        One entry per mask, sorted by image then type.
    issues
        Errors and warnings about the masks.
    """

    folder: Path | None
    types: tuple[str, ...] = ()
    files: tuple[MaskFile, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        """True if no issue blocks using the masks."""
        return not has_errors(self.issues)

    @property
    def images(self) -> tuple[str, ...]:
        """Images with at least one mask, sorted."""
        return tuple(sorted({f.image for f in self.files}))

    def for_image(self, image: str) -> Mapping[str, MaskFile]:
        """Return the masks of one image, by type (empty when it has none)."""
        return MappingProxyType({f.mask_type: f for f in self.files if f.image == image})

    def to_manifest(self) -> dict:
        """Return the inventory as recorded in the run manifest (contract §9)."""
        return {
            "folder": None if self.folder is None else self.folder.as_posix(),
            "types": list(self.types),
            "files": [
                {
                    "image": f.image,
                    "type": f.mask_type,
                    "path": f.path,
                    "source": f.source,
                    "axes": f.axes,
                    "shape": list(f.shape),
                }
                for f in self.files
            ],
        }
