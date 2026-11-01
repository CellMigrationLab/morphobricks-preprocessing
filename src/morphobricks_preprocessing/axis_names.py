"""Which axes a file's metadata names, for images and masks alike (contract §16, D-d).

One rule (plan-structure audit D3): a dimension is named when ``tifffile`` (or OME-NGFF)
gives it a letter from the file's metadata. Left open (:data:`OPEN_AXIS`) are placeholder
axes (:data:`PLACEHOLDER_AXES`), and, in a plain stack of pages without axis metadata, the
``Y`` and ``X`` too: they only say where each page's rows and columns are stored.
:func:`.metadata.named_axes` applies it to images, :mod:`.masks` to masks.
"""

from __future__ import annotations

PLACEHOLDER_AXES = frozenset("IQ")
"""``tifffile``'s letters for a dimension whose meaning the file does not give: ``I`` (a
plain stack of pages) and ``Q`` (a "shaped" file without axes, or an OME-NGFF axis of
another type). They are never metadata: the user gives such a dimension its role."""

OPEN_AXIS = "?"
"""Marks a dimension the metadata does not name in :func:`named_letters`."""


def named_letters(axes: str, from_metadata: bool) -> str:
    """Return ``axes`` with every dimension the metadata does not name as ``?``.

    Parameters
    ----------
    axes
        The axes ``tifffile`` (or OME-NGFF) reports, one letter per dimension.
    from_metadata
        Whether the axes come from axis metadata (OME, ImageJ, shaped with axes, OME-NGFF),
        :func:`.metadata.series_axes_from_metadata`; False for a plain stack of pages.

    Returns
    -------
    str
        One character per dimension, e.g. ``"CYX"``, ``"?YX"`` or ``"???"``.
    """
    plain_stack = not from_metadata and bool(PLACEHOLDER_AXES & set(axes))
    return "".join(
        OPEN_AXIS if axis in PLACEHOLDER_AXES or (plain_stack and axis in "YX") else axis
        for axis in axes
    )
