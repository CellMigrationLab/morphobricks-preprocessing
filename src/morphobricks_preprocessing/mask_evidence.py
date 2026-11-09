"""What the *Your masks* screen shows beside each mask type's question (evidence, never a guess).

For one mask type: how many images have a mask of that type, the pixel type(s) the mask headers
give, and, for one representative image (the first image to analyse that has such a mask, by
sorted path), the number of different values in the mask plane shown and the pictures: the image
channel through the confirmed axis roles (:func:`.preview.channel_strip`, PR 13 rules) and the
mask reduced with the same step (:func:`.preview.labels_thumbnail`, never interpolated). The mask
plane is read with the mask step's reader (:func:`.masks.read_mask`, which also checks the
labels). Nothing here decides what the mask is: the values are evidence for the user, who answers
"separate objects" or "regions"; the overlay is drawn only as the user declared
(:func:`.preview_render.mask_overlay_png`). For a colour (RGB) folder the image is drawn in
colour (:meth:`MaskEvidence.in_colour`, :func:`.preview_render.colour_composite`), display only.

The evidence is of the files as the folder check read them: the image is read only with the
fingerprint the check took with its header, and the mask only with the fingerprint taken with
its own header (:attr:`.masks.MaskFile.bound`); a mask found without one must not change while it
is read. Another revision is refused ("check the folder again"), never shown as the one checked.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from .axes import AxisReview
from .fingerprint import Fingerprint, current_fingerprint
from .masks import MaskFile, MaskInventory, read_mask
from .preview import (
    FILE_CHANGED,
    PreviewRefused,
    StripOptions,
    channel_strip,
    check_unchanged,
    checked_fingerprint,
    labels_thumbnail,
)
from .preview_render import (
    OverlayRefused,
    Panel,
    colour_composite,
    display_panel,
    mask_overlay_png,
    png_bytes,
)

READ_ERRORS = (PreviewRefused, OSError, ValueError, IndexError)
"""Why a picture or a value count may not be available; each becomes a sentence, never a guess."""


@dataclass(frozen=True)
class MaskEvidence:
    """The evidence about one mask type.

    ``image`` is the representative image (None when no image to analyse has such a mask);
    ``values`` the number of different values (0 included) in its mask plane, None when it could
    not be read; ``channel`` the C index of the picture (None for an image without channels);
    ``plane`` which plane is shown; ``label`` the channel label the user tagged at that C index
    (None when not tagged); ``problem`` why something is missing. ``panels`` are every channel
    of the plane shown; ``colour`` is True when the picture is their colour composite, and
    ``colour_problem`` says why a colour folder is drawn on one channel instead.
    """

    source_type: str
    images_with: int
    images: int
    dtypes: tuple[str, ...]
    image: str | None = None
    values: int | None = None
    channel: int | None = None
    plane: str = ""
    picture: Panel | None = None
    labels: np.ndarray | None = None
    problem: str = ""
    label: str | None = None
    panels: tuple[Panel, ...] = ()
    colour: bool = False
    colour_problem: str = ""

    def in_colour(self) -> MaskEvidence:
        """Return the evidence drawn on the colour picture of the three channels (RGB folder).

        A plane that cannot be drawn in colour keeps its one-channel picture, and
        ``colour_problem`` says why (shown in the caption, never silently).
        """
        if self.picture is None:
            return self
        try:
            panel = colour_composite(self.panels)
        except ValueError as exc:
            return replace(self, colour_problem=str(exc))
        return replace(self, picture=panel, channel=None, colour=True)

    def image_png(self) -> bytes | None:
        """Return the image channel's picture as PNG bytes, or None."""
        return None if self.picture is None else png_bytes(self.picture.pixels)

    def mask_png(self) -> bytes | None:
        """Return the mask alone, grey with its own contrast (shown before any answer)."""
        return None if self.labels is None else png_bytes(display_panel(self.labels).pixels)

    def overlay_png(self, representation: str | None) -> bytes | None:
        """Return the mask drawn on the image as the user declared it, or None."""
        if self.picture is None or self.labels is None:
            return None
        try:
            return mask_overlay_png(self.picture, self.labels, representation)
        except OverlayRefused:
            return None


def representative(
    masks: MaskInventory, source_type: str, analysed: Sequence[str]
) -> MaskFile | None:
    """Return the mask of the first image to analyse (sorted by path) that has one, or None."""
    by_image = {f.image: f for f in masks.files if f.mask_type == source_type}
    return next((by_image[i] for i in sorted(analysed) if i in by_image), None)


def mask_evidence(
    masks: MaskInventory,
    source_type: str,
    root: Path,
    analysed: Sequence[str],
    review: AxisReview,
    channel: int | None,
    options: StripOptions,
    bound: Mapping[str, Fingerprint | None],
) -> MaskEvidence:
    """Gather the evidence about one mask type (see the module docstring).

    Parameters
    ----------
    masks
        The masks paired by the folder check.
    source_type
        The mask type.
    root
        The experiment folder.
    analysed
        The images to analyse (relative paths, excluded images left out).
    review
        The axis review of the confirmed roles: the image is read only through them.
    channel
        The C index to draw the mask on (None or 0 for an image without channels).
    options
        The size, preview cache and memory limit of the image's picture (its ``expected`` is
        set here, from ``bound``).
    bound
        The fingerprints the folder check took with each image header
        (:attr:`.run.preflight.Preflight.header_fingerprints`).
    """
    files = [f for f in masks.files if f.mask_type == source_type and f.image in set(analysed)]
    dtypes = tuple(sorted({str(f.dtype) for f in files if f.dtype is not None}))
    found = MaskEvidence(source_type, len({f.image for f in files}), len(analysed), dtypes)
    first = representative(masks, source_type, analysed)
    if first is None:
        return found
    try:
        options = replace(options, expected=checked_fingerprint(bound, first.image))
        check_unchanged(root / first.image, options.expected, first.image)
        strip = channel_strip(root / first.image, first.image, review, options)
        panel = strip.panels[channel or 0]
        z = strip.z if "Z" in first.axes else None
        t = strip.t if "T" in first.axes else None
        plane = _read_bound(first, masks.folder, root, (z, t))
        values = int(np.unique(plane).size)
        labels = labels_thumbnail(plane, panel.plane).pixels
    except READ_ERRORS as exc:
        return _missing(found, first.image, str(exc))
    where = ", ".join(
        f"{name} {value}"
        for name, value in (("depth slice", strip.z), ("time point", strip.t))
        if value is not None
    )
    return MaskEvidence(
        source_type,
        found.images_with,
        found.images,
        dtypes,
        first.image,
        values,
        (channel or 0) if len(strip.panels) > 1 else None,
        where or "the only plane",
        panel.panel,
        labels,
        panels=tuple(p.panel for p in strip.panels),
    )


def _read_bound(
    mask: MaskFile, folder: Path | None, root: Path, where: tuple[int | None, int | None]
) -> np.ndarray:
    """Read the mask plane only as the revision whose header was read; refuse another.

    A mask bound by the check (:attr:`.masks.MaskFile.bound`) must still have that fingerprint;
    one found without binding must keep one fingerprint across the read.

    Raises
    ------
    PreviewRefused
        ``file_changed_while_shown``.
    """
    path = mask.location(folder, root)
    before = mask.bound or current_fingerprint(path)
    if before is None:
        raise PreviewRefused(FILE_CHANGED, f"the mask {mask.path} is gone: check the folder again.")
    check_unchanged(path, before, f"the mask {mask.path}")
    plane = read_mask(mask, folder, root, z=where[0], t=where[1])
    check_unchanged(path, before, f"the mask {mask.path}")
    return plane


def _missing(found: MaskEvidence, image: str, why: str) -> MaskEvidence:
    """Return the evidence without pictures or values, saying why."""
    return MaskEvidence(
        found.source_type,
        found.images_with,
        found.images,
        found.dtypes,
        image,
        problem=why or "the picture cannot be shown",
    )


__all__ = ["READ_ERRORS", "MaskEvidence", "mask_evidence", "representative"]
