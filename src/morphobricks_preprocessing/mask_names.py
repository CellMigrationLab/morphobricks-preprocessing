"""Files in the experiment named like masks: screened, and blocked as images (contract §1).

Moved from ``discovery.py`` (PP-5).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from .image_names import position_id
from .validation import ValidationIssue, agree, error, plural, warning

if TYPE_CHECKING:
    from .discovery import LeafFolder

MASK_NAME_SUFFIXES = (
    "_mask",
    "_masks",
    "_cp_masks",
    "_labels",
    "_label",
    "_seg",
    "_segmentation",
)
"""Image name endings that read as a mask of another image (``f1_mask.tif`` beside ``f1.tif``),
compared in any case: CellPatcher's former default mask suffixes (MorphoBricks decision D-i
moved this screen here from MorphoRelate), and Cellpose's ``_cp_masks`` and ``_masks``
(design v0.4 §2)."""


MASK_FOLDER_WORDS = frozenset(
    {"mask", "masks", "label", "labels", "seg", "segmentation", "segmentations"}
)
"""Words that make a folder name read as a masks folder (``masks``, ``ctrl_masks``,
``CellLabels``), compared in any case after splitting the name into words."""


MASK_NAME_CODES = frozenset({"image_named_as_mask", "mask_folder_in_experiment"})
"""The codes of :func:`mask_name_issues`."""

_WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def mask_name_issues(
    leaves: Iterable[LeafFolder], mask_names: Sequence[str] = (), excluded: Iterable[str] = ()
) -> list[ValidationIssue]:
    """Refuse images named as masks, and warn about masks folders inside the experiment.

    A mask is never a position, and masks belong in a masks folder outside the experiment
    (contract §15). Two screens over every image of the experiment (all leaf folders
    together), run by :func:`discover_experiment` (MorphoBricks decision D-i, moved from
    MorphoRelate's ``project_image_names``):

    - ``image_named_as_mask``: an image named after another image's ``position_id``, then
      one of :data:`MASK_NAME_SUFFIXES` (or ``_`` and one of ``mask_names``), in any case:
      ``f1_mask.tif`` beside ``f1.tif``. The longest such position owns it, an image of the
      same folder first. Any other name is an image: ``A1_2.tif`` beside ``A1.tif``,
      ``f1_DNA.tif`` beside ``f1.tif``.
    - ``mask_folder_in_experiment``: a folder named like a masks folder (a word of
      :data:`MASK_FOLDER_WORDS` in its name: ``masks``, ``ctrl_masks``, ``CellLabels``)
      holding images named as masks of the other images, with any name
      (``<position>`` or ``<position>_<anything>``).

    ``image_named_as_mask`` is an error (design v0.4 §3): a mask is never analysed as an
    image, so the file is renamed or excluded. ``mask_folder_in_experiment`` is a warning:
    the folder may hold images that only look like masks.

    Parameters
    ----------
    leaves
        The experiment's leaf folders (:attr:`DiscoveryResult.leaves`).
    mask_names
        Mask names the caller expects (``"cells"`` for ``f1_cells.tif``), screened besides
        :data:`MASK_NAME_SUFFIXES`; :func:`discover_experiment` gives none.
    excluded
        Images the user excluded: not reported (they still own the masks named after them).

    Returns
    -------
    list of ValidationIssue
        The issues, or nothing.
    """
    excluded = set(excluded)
    leaves = list(leaves)
    images = [path for leaf in leaves for path in leaf.images]
    stems = _stems(images)
    suffixes = {suffix.lower() for suffix in MASK_NAME_SUFFIXES}
    suffixes |= {f"_{name.lower()}" for name in mask_names}
    named = images_named_as_masks(leaves, sorted(suffixes))
    named = [(file, owner) for file, owner in named if file not in excluded]
    by_folder: dict[str, list[tuple[str, str]]] = {}
    for relative in images:
        folder = mask_folder_part(relative)
        if folder is None or relative in excluded:
            continue
        stem = position_id(relative).lower()
        owner = stems[stem][0] if stem in stems else _mask_owner(stem, stems, "", None)
        if owner is not None:
            by_folder.setdefault(folder, []).append((relative, owner))
    issues = []
    if named:
        issues.append(
            error(
                "image_named_as_mask",
                f"{plural(len(named), 'image')} "
                f"{agree(named, 'is', 'are')} named as a mask of another "
                "image: that image's name followed by "
                + ", ".join(sorted(suffixes))
                + f" ({_few([f'{file} (of {owner})' for file, owner in named])}). A mask is "
                "never a position, so it is never analysed as an image.",
                [file for file, _ in named],
                "If they are masks, move them into a masks folder outside the experiment "
                "folder, laid out like the experiment (<masks>/<the image's folders>/"
                "<image>_<type>.tif), or exclude them. If they are images, rename them.",
            )
        )
    if by_folder:
        found = [pair for pairs in by_folder.values() for pair in pairs]
        issues.append(
            warning(
                "mask_folder_in_experiment",
                f"{plural(len(by_folder), 'folder')} inside the experiment "
                f"{'is' if len(by_folder) == 1 else 'are'} named like a masks folder and "
                f"{'holds' if len(by_folder) == 1 else 'hold'} files named as masks of the "
                f"images ({_few([f'{file} (of {owner})' for file, owner in found])}). They are "
                "analysed as images, with the folder as a condition or repeat.",
                sorted(by_folder),
                "If they are masks, move the folder out of the experiment folder and give it as "
                "the masks folder. If they are images, rename the folder.",
            )
        )
    return issues


def images_named_as_masks(
    leaves: Iterable[LeafFolder], suffixes: Sequence[str] = MASK_NAME_SUFFIXES
) -> list[tuple[str, str]]:
    """Return the images named as a mask of another image, each with that image.

    The detection of :func:`mask_name_issues`'s ``image_named_as_mask``, for a brick that acts
    on the files itself (MorphoRelate leaves out of its images those named with a mask name
    the user declared): an image is named as a mask of another when its ``position_id`` is
    that image's ``position_id`` followed by one of ``suffixes``, compared in lower case,
    anywhere in the experiment. The longest such position owns it, an image of the same
    folder first. Images inside a folder named like a masks folder (:func:`mask_folder_part`)
    are left out, as owners too.

    Parameters
    ----------
    leaves
        The experiment's leaf folders (:attr:`DiscoveryResult.leaves`); a caller leaves out
        images it has already set aside by giving leaves without them.
    suffixes
        The name endings, each with its ``_`` (``"_mask"``, ``"_cells"``); by default
        :data:`MASK_NAME_SUFFIXES`.

    Returns
    -------
    list of tuple
        ``(image, owner)`` relative paths, in the leaves' order.
    """
    images = [path for leaf in leaves for path in leaf.images]
    regular = [path for path in images if mask_folder_part(path) is None]
    stems = _stems(images)
    wanted = {suffix.lower() for suffix in suffixes}
    named = []
    for relative in regular:
        path = PurePosixPath(relative)
        owner = _mask_owner(position_id(path.name).lower(), stems, str(path.parent), wanted)
        if owner is not None:
            named.append((relative, owner))
    return named


def _stems(images: Sequence[str]) -> dict[str, list[str]]:
    """Return the images outside masks folders by their ``position_id`` in lower case."""
    stems: dict[str, list[str]] = {}
    for relative in images:
        if mask_folder_part(relative) is None:
            stems.setdefault(position_id(relative).lower(), []).append(relative)
    return stems


def mask_folder_part(relative: str) -> str | None:
    """Return the first folder of an image's path named like a masks folder, or None.

    For example ``"masks"`` for ``"masks/ctrl/rep1/f1.tif"``; a name counts when one of its
    words (``CellLabels`` is ``Cell`` and ``Labels``) is in :data:`MASK_FOLDER_WORDS`.
    """
    parts = PurePosixPath(relative).parts[:-1]
    for index, part in enumerate(parts):
        if {word.lower() for word in _WORDS.findall(part)} & MASK_FOLDER_WORDS:
            return "/".join(parts[: index + 1])
    return None


def _mask_owner(
    stem: str, stems: Mapping[str, list[str]], folder: str, suffixes: set[str] | None
) -> str | None:
    """Return the image whose ``position_id`` is followed in ``stem`` by ``_`` and a suffix.

    Any suffix when ``suffixes`` is None. ``stem`` and the keys of ``stems`` are lower case.
    The longest such position wins, an image of ``folder`` first; None when there is none.
    """
    keys = [
        key
        for key in stems
        if key != stem
        and stem.startswith(key + "_")
        and (suffixes is None or stem[len(key) :] in suffixes)
    ]
    if not keys:
        return None
    owners = stems[max(keys, key=len)]
    near = [owner for owner in owners if str(PurePosixPath(owner).parent) == folder]
    return (near or owners)[0]


def _few(items: list[str], shown: int = 5) -> str:
    more = f" and {len(items) - shown} more" if len(items) > shown else ""
    return ", ".join(items[:shown]) + more
