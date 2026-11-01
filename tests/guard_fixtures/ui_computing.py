"""A module declared ui that computes with scipy."""

from __future__ import annotations

from scipy import ndimage


def label(mask: object) -> object:
    """Label a mask."""
    return ndimage.label(mask)
