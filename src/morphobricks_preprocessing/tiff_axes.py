"""Whether a TIFF series' axes come from its metadata (contract §16, D-d)."""

from __future__ import annotations

import json

import tifffile


def series_axes_from_metadata(series: tifffile.TiffPageSeries) -> bool:
    """Return True when axis metadata names the axes of a ``tifffile`` series.

    False for a plain TIFF: a series ``tifffile`` reads as ``generic`` or ``uniform`` pages,
    or as "shaped" without axes (``{"shape": ...}`` only). True for OME, ImageJ, shaped
    metadata with ``"axes"`` (decision D-d) and the other microscope formats ``tifffile``
    reads axes from. :func:`inspect_tiff` records it for the first series as
    :attr:`TiffMetadata.axes_from_metadata`; a brick that builds a :class:`TiffMetadata`
    for another series passes it the same way.

    Parameters
    ----------
    series
        A series of an open ``tifffile.TiffFile``.

    Returns
    -------
    bool
        Whether the series' axes come from metadata rather than from where its pages are
        stored.
    """
    kind = str(series.kind).lower()
    if kind in ("generic", "uniform"):
        return False
    if kind == "shaped":
        try:
            shaped = json.loads(series.keyframe.shaped_description or "")
        except (AttributeError, TypeError, ValueError):
            return False
        return isinstance(shaped, dict) and "axes" in shaped
    return True
