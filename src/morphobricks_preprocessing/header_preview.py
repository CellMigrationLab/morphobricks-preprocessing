"""A first look at each folder from its image header, before the axes are confirmed (PR 17c).

*Images found* shows, beside each folder's labels, a picture of the folder's first image by
path, so a user sees at once that the folder is the wrong one. It is **display only**: nothing
read here feeds an answer, an axis role, the draft or the run. The rules:

- **Only when the header names Y and X** (:func:`.metadata.named_axes`). A plain stack whose
  rows and columns are not named gets no picture, only "preview after Axes (step 2)": no axis
  is guessed.
- **Every other axis is max-projected and named** in the caption, with the header's letters. A
  header that says colour (RGB samples) is drawn in colour
  (:func:`.preview_render.colour_composite`); its other axes are projected per colour.
- **Bounded reads:** planes of at most :data:`PIXEL_CAP` pixels, read whole within
  :data:`.preview.READ_BUDGET` (a plane that would need reading in blocks is not read here), and
  at most :data:`PLANE_CAP` planes and :data:`PIXEL_CAP` pixels per picture, evenly spread over
  the planes (the caption says so). OME-Zarr images are read from level 0 by the same rule.
- **A failure is a sentence in the cell** (:class:`HeaderPreview.problem`), never an exception
  that stops the folder check.
- **Only the image as the check read it:** the picture is drawn only when the file still has the
  fingerprint the folder check took with its header; another revision (or one not bound) says
  "check the folder again" and is not drawn.

The projection reads the planes through :func:`.planes.read_plane` with a reading layout made
from the header's own letters. Its role slots only say which dimension to step through: every
dimension other than Y and X (and the colour samples) is projected, so no slot is ever shown or
used as an axis role.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .axes import AxisMapping
from .fingerprint import Fingerprint
from .metadata import TiffMetadata, named_axes
from .planes import PlaneMemoryError, PlaneReadError, read_plane
from .preview import (
    READ_BUDGET,
    THUMBNAIL_SIDE,
    PreviewRefused,
    check_unchanged,
    checked_fingerprint,
)
from .preview_crop import TilePictures, tile_pictures

PLANE_CAP = 8
"""Planes read for one picture, at most."""
PIXEL_CAP = 4096 * 4096
"""Pixels read for one picture (all its planes and colours), and the largest plane, at most."""
NO_YX = "preview after Axes (step 2)"
HEADER_NOTE = "from the file header, axes not confirmed yet"
_SLOTS = ("Z", "T", "C")
READ_FAILURES = (PlaneReadError, OSError, ValueError, IndexError, MemoryError)


class PreviewUnavailable(Exception):
    """Raised when a header picture is not drawn; the message is the cell's sentence."""


@dataclass(frozen=True)
class HeaderPlanes:
    """The projected native planes of one image: one, or three for colour, and how."""

    planes: tuple[np.ndarray, ...]
    colour: bool
    text: str


@dataclass(frozen=True)
class HeaderPreview:
    """A folder's picture from its first image's header, or why there is none."""

    image: str | None
    pictures: TilePictures | None = None
    colour: bool = False
    text: str = ""
    problem: str = ""


def _colour_axis(meta: TiffMetadata) -> int | None:
    """Return the dimension of the RGB samples when the header says colour, else None."""
    if not meta.rgb_samples or "S" not in meta.axes:
        return None
    s = meta.axes.index("S")
    return s if meta.shape[s] == 3 else None


def _reading_layout(meta: TiffMetadata) -> tuple[AxisMapping, list[int], int | None]:
    """Return the reading layout, the dimensions to project and the colour dimension.

    Raises
    ------
    PreviewUnavailable
        When the header does not name Y and X, or names more axes than can be stepped through.
    """
    named = named_axes(meta)
    if "Y" not in named or "X" not in named:
        raise PreviewUnavailable(NO_YX)
    y, x, s = named.index("Y"), named.index("X"), _colour_axis(meta)
    roles: list[str | None] = [None] * len(meta.axes)
    roles[y], roles[x] = "Y", "X"
    slots = [r for r in _SLOTS if s is None or r != "C"]
    if s is not None:
        roles[s] = "C"
    projected = [d for d, n in enumerate(meta.shape) if d not in (y, x, s) and n > 1]
    if len(projected) > len(slots):
        raise PreviewUnavailable(
            f"the header has {len(projected)} axes besides Y and X; a preview here projects at "
            f"most {len(slots)}: {NO_YX}"
        )
    for d, role in zip(projected, slots, strict=False):
        roles[d] = role
    return AxisMapping(meta.axes, tuple(roles)), projected, s


def _spread(total: int, count: int) -> list[int]:
    """Return ``count`` plane numbers evenly spread over ``total`` (the middle one alone)."""
    if count >= total:
        return list(range(total))
    if count == 1:
        return [total // 2]
    return sorted({round(k * (total - 1) / (count - 1)) for k in range(count)})


def _caption(meta: TiffMetadata, projected: list[int], read: int, total: int) -> str:
    """Say which axes were projected, and how many planes when the cap applied."""
    if not projected:
        return "the only plane"
    named = named_axes(meta)
    axes = ", ".join(
        f"{meta.axes[d] if named[d] != '?' else 'an unnamed axis'} ({meta.shape[d]})"
        for d in projected
    )
    if read == total:
        return f"max projection over {axes}: all {total} planes"
    return (
        f"max projection over {axes}: {read} of {total} planes, evenly spread (a preview "
        f"here reads at most {PLANE_CAP} planes and {PIXEL_CAP:,} pixels)"
    )


def _read(path: str | os.PathLike, mapping: AxisMapping, coords: dict[str, int]) -> np.ndarray:
    """Read one plane whole within :data:`.preview.READ_BUDGET`; never in blocks here."""
    try:
        return read_plane(path, mapping, memory_limit=READ_BUDGET, **coords).pixels
    except PlaneMemoryError as exc:
        raise PreviewUnavailable(
            f"a plane of this image is too large to read whole for a preview here: {NO_YX}"
        ) from exc


def header_planes(path: str | os.PathLike, meta: TiffMetadata) -> HeaderPlanes:
    """Read the max projection of an image by its header's axes, within the caps.

    Raises
    ------
    PreviewUnavailable
        Without Y and X in the header, or over the caps.
    PlaneReadError, OSError, ValueError, IndexError, MemoryError
        When the file cannot be read.
    """
    if not meta.readable:
        raise PreviewUnavailable(f"the header cannot be read: {meta.error or 'unknown reason'}")
    mapping, projected, s = _reading_layout(meta)
    plane = (meta.shape[mapping.index_of("Y")], meta.shape[mapping.index_of("X")])
    if plane[0] * plane[1] > PIXEL_CAP:
        raise PreviewUnavailable(
            f"planes of {plane[0]} x {plane[1]} pixels are over the {PIXEL_CAP:,}-pixel cap "
            f"of a preview here: {NO_YX}"
        )
    colours = 3 if s is not None else 1
    lengths = [meta.shape[d] for d in projected]
    total = math.prod(lengths)
    wanted = max(1, min(PLANE_CAP, PIXEL_CAP // (plane[0] * plane[1] * colours)))
    chosen = _spread(total, wanted)
    stacks: list[np.ndarray | None] = [None] * colours
    for number in chosen:
        where = dict(zip(projected, np.unravel_index(number, lengths), strict=True))
        coords = {mapping.roles[d].lower(): int(i) for d, i in where.items()}
        for c in range(colours):
            if s is not None:
                coords["c"] = c
            pixels = _read(path, mapping, coords)
            # np.maximum keeps a non-finite pixel non-finite: it is shown magenta, as elsewhere.
            stacks[c] = pixels if stacks[c] is None else np.maximum(stacks[c], pixels)
    text = _caption(meta, projected, len(chosen), total)
    return HeaderPlanes(tuple(stacks), s is not None, text)


def header_preview(
    root: Path,
    meta: TiffMetadata | None,
    side: int = THUMBNAIL_SIDE,
    *,
    bound: Mapping[str, Fingerprint | None],
) -> HeaderPreview:
    """Return a folder's picture from its first image's header, or the reason there is none.

    Parameters
    ----------
    root
        The experiment folder.
    meta
        The header of the folder's first image by path; None when it has no image to analyse.
    side
        The thumbnail's largest side in pixels.
    bound
        The fingerprints the folder check took with each header
        (:attr:`.run.preflight.Preflight.header_fingerprints`): the image is drawn only as that
        revision.
    """
    if meta is None:
        return HeaderPreview(None, problem="no image to analyse in this folder")
    try:
        path, expected = root / meta.relative_path, checked_fingerprint(bound, meta.relative_path)
        check_unchanged(path, expected, meta.relative_path)
        found = header_planes(path, meta)
        check_unchanged(path, expected, meta.relative_path)  # a rewrite during the read
        pictures = tile_pictures(found.planes, found.colour, side)
    except (PreviewUnavailable, PreviewRefused) as exc:
        return HeaderPreview(meta.relative_path, problem=str(exc))
    except READ_FAILURES as exc:
        return HeaderPreview(meta.relative_path, problem=f"cannot be read for a preview: {exc}")
    return HeaderPreview(meta.relative_path, pictures, found.colour, found.text)


__all__ = [
    "HEADER_NOTE",
    "NO_YX",
    "PIXEL_CAP",
    "PLANE_CAP",
    "HeaderPlanes",
    "HeaderPreview",
    "PreviewUnavailable",
    "header_planes",
    "header_preview",
]
