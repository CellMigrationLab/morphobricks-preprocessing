"""Mask labels: whole numbers from 0 to the int32 limit (decision D-b, contract §16).

Moved from ``masks.py`` (PP-5).
"""

from __future__ import annotations

import numpy as np

LARGEST_LABEL = int(np.iinfo(np.int32).max)
"""The largest label a mask may hold, 2,147,483,647: MorphoBricks labels are int32
(decision D-b, 30 October 2026), so every brick stores a label the same way."""


def check_labels(pixels: np.ndarray, name: str = "the mask") -> None:
    """Refuse a mask whose pixels are not labels from 0 to :data:`LARGEST_LABEL`.

    Label 0 is the background and every object has a positive label (contract §15). A
    negative value is neither, so it is refused rather than read as an object (which would
    also make the background one). A label above :data:`LARGEST_LABEL` cannot be stored as
    an int32 label, the one label type of every MorphoBricks brick (decision D-b), so it is
    refused rather than wrapped or widened. Pixels that are not whole numbers (floats) are
    not labels. Boolean masks are labels 0 and 1. This is the one label check every brick
    calls (MorphoBricks D86).

    Parameters
    ----------
    pixels
        The mask, or one plane of it.
    name
        The mask as the message names it, for example ``"the mask Cells/ctrl/a.tif"``.

    Raises
    ------
    ValueError
        Naming the mask and the offending value: a pixel below 0, a pixel above
        :data:`LARGEST_LABEL`, or a pixel type other than integer or boolean.
    """
    pixels = np.asarray(pixels)
    if pixels.dtype.kind not in "biu":
        raise ValueError(
            f"{name} does not hold whole-number labels (pixel type {pixels.dtype}); save the "
            "mask as integers (labels) or true/false."
        )
    if pixels.dtype.kind == "b" or not pixels.size:
        return
    smallest = pixels.min()
    if smallest < 0:
        raise ValueError(
            f"{name} holds negative labels (smallest {smallest}); label 0 is the "
            "background and objects must have positive labels."
        )
    largest = pixels.max()
    if largest > LARGEST_LABEL:
        raise ValueError(
            f"{name} holds labels above {LARGEST_LABEL} (largest {largest}); MorphoBricks "
            "stores labels as 32-bit signed numbers (int32), so relabel the objects from 1."
        )
