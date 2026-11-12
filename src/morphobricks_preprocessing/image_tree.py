"""Which image a viewer shows: condition, biological repeat, technical repeat, image (pure).

The shipped notebook shows images through one viewer (:mod:`.notebook_viewer`) whose four
dropdowns narrow each other: the biological repeats offered are those of the chosen condition,
the technical repeats those of that repeat, the images those of that folder (owner, 11 November
2026: dynamic dropdowns instead of one long list). The levels are the folder labels of
:mod:`.discovery`; nothing is decided here.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

LEVELS = ("Condition", "Biological repeat", "Technical repeat", "Image")
"""The four dropdowns, the widest first."""


@dataclass(frozen=True)
class ImageEntry:
    """One image with the labels of its folder.

    Attributes
    ----------
    condition, biological_repeat, technical_repeat
        The folder's labels.
    image
        The image, relative to the experiment folder.
    """

    condition: str
    biological_repeat: str
    technical_repeat: str
    image: str

    @property
    def path(self) -> tuple[str, str, str, str]:
        """The four values the dropdowns choose, in :data:`LEVELS` order."""
        return (self.condition, self.biological_repeat, self.technical_repeat, self.image)

    @property
    def name(self) -> str:
        """The image's file name, as the last dropdown lists it."""
        return self.image.rpartition("/")[2]


def entries_of(leaves: Iterable, excluded: Iterable[str] = ()) -> tuple[ImageEntry, ...]:
    """Return the images of the leaf folders (:class:`.discovery.LeafFolder`), sorted by path."""
    left_out = set(excluded)
    found = [
        ImageEntry(*leaf.labels, image)
        for leaf in leaves
        for image in leaf.images
        if image not in left_out
    ]
    return tuple(sorted(found, key=lambda entry: entry.image))


def choices(entries: Sequence[ImageEntry], chosen: Sequence[str | None]) -> list[list[str]]:
    """Return what each level offers, given the values chosen at the levels before it.

    Values keep the order of the entries (sorted paths); a level is narrowed by every level
    above it, so no combination without an image can be chosen.
    """
    offered: list[list[str]] = []
    left = list(entries)
    for level in range(len(LEVELS)):
        values = list(dict.fromkeys(entry.path[level] for entry in left))
        offered.append(values)
        value = chosen[level] if level < len(chosen) else None
        if value not in values:
            value = values[0] if values else None
        left = [entry for entry in left if entry.path[level] == value]
    return offered


def resolve(entries: Sequence[ImageEntry], wanted: Sequence[str | None]) -> ImageEntry | None:
    """Return the entry the wanted values lead to: each kept while offered, else the first."""
    left = list(entries)
    for level in range(len(LEVELS)):
        values = list(dict.fromkeys(entry.path[level] for entry in left))
        if not values:
            return None
        value = wanted[level] if level < len(wanted) and wanted[level] in values else values[0]
        left = [entry for entry in left if entry.path[level] == value]
    return left[0] if left else None


def entry_of(entries: Sequence[ImageEntry], image: str) -> ImageEntry | None:
    """Return the entry of an image by its relative path, None when it is not listed."""
    return next((entry for entry in entries if entry.image == image), None)


__all__ = ["LEVELS", "ImageEntry", "choices", "entries_of", "entry_of", "resolve"]
