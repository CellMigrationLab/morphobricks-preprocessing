"""Find projected copies by their OME header, for a tree whose record does not vouch for them.

The writer (:func:`.ome_tiff_copy.write_projection`) gives every projected copy an OME
``Image/Description`` that begins with :data:`.projection_record.MARKER`. An image with that
description in a tree with no conversion record, or whose record does not list it as
projected, is refused (``projection_record_missing``): without the record it would pass as
a plain 2-D image and nothing would say it is a projection.

Cost: one TIFF header read per image the record does not already list as projected (the
first page's description tag; no pixel is read). Nothing else is inferred from the header,
and an image that is not an OME-TIFF, or whose header cannot be read, is not marked.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from xml.etree import ElementTree

import tifffile

from .projection_record import MARKER
from .validation import ValidationIssue, error, plural

logger = logging.getLogger(__name__)

_SUFFIXES = (".tif", ".tiff")


def is_projected_copy(path: Path) -> bool:
    """Return True if the OME ``Image/Description`` of the TIFF at ``path`` is the marker's."""
    if path.suffix.lower() not in _SUFFIXES:
        return False
    try:
        with tifffile.TiffFile(path) as tif:
            text = tif.pages.first.description
        root = ElementTree.fromstring(text)
    except (OSError, ValueError, ElementTree.ParseError, tifffile.TiffFileError, AttributeError):
        logger.debug("Cannot read the description of %s", path, exc_info=True)
        return False
    for image in root:
        if image.tag.rsplit("}", 1)[-1] != "Image":
            continue
        for child in image:
            if child.tag.rsplit("}", 1)[-1] == "Description":
                return (child.text or "").startswith(MARKER)
    return False


def marked_images(root: str | Path, images: Iterable[str]) -> list[str]:
    """Return the images (relative to ``root``) whose header says they are projected copies."""
    return sorted(name for name in images if is_projected_copy(Path(root) / name))


def marker_issue(images: list[str]) -> ValidationIssue:
    """Refuse projected copies that no conversion record lists as projected."""
    first = images[0]
    return error(
        "projection_record_missing",
        f"{plural(len(images), 'image')} (first: {first}) say in their OME description that "
        "they are Z-projected copies, but the folder has no conversion record that lists "
        f"{'it' if len(images) == 1 else 'them'} as projected, so it is not known how they were "
        "made and they are not read as plain images.",
        images,
        "Run `convert --project` again from the original images into a new folder, or restore "
        "the conversion record (.morphobricks-conversion.json) that was written with these copies.",
    )
