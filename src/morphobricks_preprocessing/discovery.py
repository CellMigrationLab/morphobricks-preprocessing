"""Folder discovery: find the leaf folders of an experiment and label them.

The experiment root holds up to three levels of folders (condition, biological
repeat, technical repeat). A folder with images and no subfolders is a leaf;
its labels are the folder names on the way down, with ``single`` for the levels
the branch does not have. A folder may itself be named ``single`` (in lower
case) when it is the only folder in its place: like a missing level, it is a
level with one member (the MorphoBricks convention). The rules are in
``docs/contract.md`` §1.

Images are TIFF files (``.tif``, ``.tiff``) and OME-Zarr images, which are
folders whose names end in ``.zarr`` (usually ``.ome.zarr``). An OME-Zarr folder
counts as one image, not as a subfolder, and is never entered.

Each image is one position (field of view): files with several positions are
refused when their headers are checked. A position is identified by its labels
and its ``position_id``, the image name without its extension (contract §1).

Only folder and file names are read here; image headers are inspected by
:mod:`morphobricks_preprocessing.metadata`.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import numpy as np
from morphobricks_normalization.records import LEVELS, SINGLE

from .validation import ValidationIssue, error, has_errors, plural, warning
from .zarr_input import is_zarr_name

logger = logging.getLogger(__name__)

TIFF_SUFFIXES = (".tif", ".tiff")

# Files and folders created by operating systems and tools, not by users. They
# are skipped without a warning so that copying an experiment from a Mac or
# opening it in Jupyter does not flood the preflight with messages.
SYSTEM_NAMES = frozenset({"thumbs.db", "desktop.ini", "__macosx"})

OUTPUT_FOLDER_SUFFIX = "_MorphoBricks_Preprocessing"

ROOT = "."

# Image name endings removed to give the position identifier, longest first, so
# that "img_01.ome.tif" and "img_01.ome.zarr" both give "img_01".
IMAGE_SUFFIXES = (".ome.tiff", ".ome.tif", ".ome.zarr", ".tiff", ".tif", ".zarr")
# The extensions position_id removes: the images this brick reads, then the other formats
# MorphoBricks bricks read (CellPatcher: PNG, JPEG, BMP, GIF, NumPy), so every brick names a
# position by this one function (MorphoBricks decision D85, 2026-10-29). Naming only: the
# images discovery accepts are still IMAGE_SUFFIXES.
NAME_SUFFIXES = (*IMAGE_SUFFIXES, ".png", ".jpeg", ".jpg", ".bmp", ".gif", ".npy")

FOV_KEY = (*LEVELS, "position_id")
"""The columns that identify one position (field of view) in every MorphoBricks table:
``condition``, ``biological_repeat``, ``technical_repeat`` and ``position_id``
(cross-brick decisions D02 and D86)."""

MISSING_TEXT = frozenset({"", "na", "nan", "none", "null", "n/a", "<na>"})
"""Names refused as labels and position identifiers, in any case: table readers read them
as a missing value (MorphoBricks decision D-e)."""


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
moved this screen here from MorphoRelate), and ``_masks`` and Cellpose's ``_cp_masks``
(v0.4.0, design audit M-3)."""

MASK_FOLDER_WORDS = frozenset(
    {"mask", "masks", "label", "labels", "seg", "segmentation", "segmentations"}
)
"""Words that make a folder name read as a masks folder (``masks``, ``ctrl_masks``,
``CellLabels``), compared in any case after splitting the name into words."""

_WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


class IdentityError(ValueError):
    """Raised for a label, position identifier or plane index that cannot identify anything."""


def check_identity_text(value: object, field: str) -> str:
    """Return a label or ``position_id`` if every brick can use it as an identity, else raise.

    One rule for the labels (``condition``, ``biological_repeat``, ``technical_repeat``) and
    ``position_id`` in every MorphoBricks brick (decision D-e, 30 October 2026): the value is
    text, has no leading or trailing whitespace, and is not ``""``, ``"NA"``, ``"nan"``,
    ``"none"``, ``"null"``, ``"n/a"`` or ``"<NA>"`` in any case (:data:`MISSING_TEXT`), which
    table readers turn into a missing value. Discovery applies
    it to every folder and image name; names that differ only in case are refused by
    :func:`discover_experiment` (``case_conflict``).

    Parameters
    ----------
    value
        The label or identifier.
    field
        What it is, for the message (``"condition"``, ``"position_id"``, ...).

    Returns
    -------
    str
        The unchanged value.

    Raises
    ------
    IdentityError
        Naming the field and the value.
    """
    if not isinstance(value, str):
        raise IdentityError(f"{field} must be text, got {type(value).__name__} {value!r}")
    if value != value.strip():
        raise IdentityError(f"{field} {value!r} starts or ends with whitespace")
    if value.casefold() in MISSING_TEXT:
        raise IdentityError(
            f"{field} {value!r} reads as a missing value in tables (empty, NA, nan, none, null, "
            "n/a or <NA>)"
        )
    return value


def check_plane_index(value: object, axis: str) -> int | None:
    """Return a depth slice or time point index if it is one, else raise.

    The rule of every MorphoBricks brick (decision D-e): None (the image has no such axis) or
    an integer of at least 0, a Python ``int`` or a NumPy integer (returned as a plain
    ``int``, so ``numpy.int64(1)`` from a table is index 1). Nothing else is converted: a
    bool, a float (also ``1.0``) and text such as ``"1"`` are refused, so a value that was
    never an index is not read as one.

    Parameters
    ----------
    value
        The index.
    axis
        ``"z"`` or ``"t"``, for the message.

    Returns
    -------
    int or None
        The index as a plain ``int``, or None.

    Raises
    ------
    IdentityError
        Naming the axis and the value.
    """
    if value is None:
        return None
    if isinstance(value, np.integer):
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise IdentityError(
            f"{axis} must be an int of at least 0, or None; got {type(value).__name__} {value!r}"
        )
    return value


def check_identity_case(keys: Iterable[Sequence[str]]) -> None:
    """Refuse identities of one run whose names differ only in upper/lower case.

    The case rule of decision D-e for bricks that read identities from tables rather than
    from folders (discovery applies the same rule to folder names, ``case_conflict``): a
    ``condition``, ``biological_repeat`` or ``technical_repeat`` is spelled one way in the
    whole run, so ``Rep1`` in one condition and ``rep1`` in another clash. ``position_id``
    keeps its own scope (D85): unique within its technical repeat ignoring case, so ``Img``
    and ``img`` clash in one technical repeat and not across two. The same key may occur
    any number of times (several planes of one position).

    Parameters
    ----------
    keys
        One key per row: the labels (:data:`morphobricks_normalization.records.LEVELS`
        order), optionally followed by the ``position_id`` (:data:`FOV_KEY` order).

    Raises
    ------
    IdentityError
        Naming the field and both spellings, at the first clash.
    ValueError
        If a key does not have three or four values.
    """
    labels: list[dict[str, str]] = [{} for _ in LEVELS]
    positions: dict[tuple[str, ...], dict[str, str]] = {}
    for key in keys:
        values = tuple(key)
        if len(values) not in (len(LEVELS), len(FOV_KEY)):
            raise ValueError(f"the key {values!r} does not have the values of {FOV_KEY}")
        for level, value in enumerate(values[: len(LEVELS)]):
            _same_spelling(labels[level], value, LEVELS[level], "in one run")
        if len(values) == len(FOV_KEY):
            seen = positions.setdefault(values[: len(LEVELS)], {})
            _same_spelling(seen, values[-1], "position_id", "within one technical repeat")


def _same_spelling(seen: dict[str, str], value: str, field: str, scope: str) -> None:
    first = seen.setdefault(str(value).casefold(), value)
    if first != value:
        raise IdentityError(
            f"{field} {first!r} and {value!r} differ only in upper/lower case {scope}; "
            "spell each name one way"
        )


def is_system_name(name: str) -> bool:
    """Return True for system files and folders that are skipped silently.

    These are dot-files and dot-folders (``.DS_Store``, ``._image.tif``,
    ``.ipynb_checkpoints``), ``Thumbs.db``, ``desktop.ini`` and ``__MACOSX``.
    """
    return name.startswith(".") or name.lower() in SYSTEM_NAMES


def is_tiff_name(name: str) -> bool:
    """Return True if ``name`` has a TIFF extension (``.tif`` or ``.tiff``, any case)."""
    return name.lower().endswith(TIFF_SUFFIXES)


def is_ome_zarr_folder(entry: os.DirEntry) -> bool:
    """Return True for a folder that is an OME-Zarr image (its name ends in ``.zarr``).

    Such a folder counts as one image, not as a subfolder: it is never entered.
    """
    return entry.is_dir() and is_zarr_name(entry.name)


def position_id(image: str) -> str:
    """Return the position identifier of an image: its name without the extension.

    Every image is exactly one position, because files holding several
    positions, scenes or tiles are refused, so the file name identifies the
    position within its technical repeat. The full position (field of view) key
    is ``condition``, ``biological_repeat``, ``technical_repeat`` and this
    identifier (contract §1). The extension is ``.tif``, ``.tiff`` or ``.zarr``
    (any case), together with a preceding ``.ome``, or one of the other image
    formats MorphoBricks bricks read (``.png``, ``.jpg``, ``.jpeg``, ``.bmp``,
    ``.gif``, ``.npy``; :data:`NAME_SUFFIXES`), so every brick names a position
    with this one function (MorphoBricks D85).

    Parameters
    ----------
    image
        The image's path relative to the experiment root, or its name.

    Returns
    -------
    str
        For example ``"img_01"`` for ``"ctrl/rep1/img_01.ome.tif"``.

    Raises
    ------
    ValueError
        If the name is only an extension (``".tif"``, ``".ome.zarr"``): it has
        no name to identify a position by. The longest extension decides, so
        ``".ome.tif"`` is refused rather than read as ``".ome"``.
    """
    name = image.replace("\\", "/").rsplit("/", 1)[-1]
    lowered = name.lower()
    for suffix in NAME_SUFFIXES:
        if lowered.endswith(suffix):
            if len(name) == len(suffix):
                raise ValueError(
                    f"{image!r} is only an extension: it has no name to identify a position by"
                )
            return name[: -len(suffix)]
    return name


def default_output_folder(experiment_root: str | os.PathLike) -> Path:
    """Return the default output folder, a sibling of the experiment folder.

    For ``/data/exp1`` this is ``/data/exp1_MorphoBricks_Preprocessing``.
    """
    root = Path(experiment_root).resolve()
    return root.with_name(root.name + OUTPUT_FOLDER_SUFFIX)


@dataclass(frozen=True)
class LeafFolder:
    """A folder whose images are analysed.

    Attributes
    ----------
    relative_path
        Path relative to the experiment root with ``/`` separators; ``"."`` when
        the experiment root itself is the leaf.
    condition, biological_repeat, technical_repeat
        Labels from the folder names; ``"single"`` for missing levels and for
        folders named ``single``.
    images
        Relative paths of the images (TIFF files and OME-Zarr folders) directly in
        this folder, sorted.
    """

    relative_path: str
    condition: str
    biological_repeat: str
    technical_repeat: str
    images: tuple[str, ...]

    @property
    def labels(self) -> tuple[str, str, str]:
        """The three labels, from condition down to technical repeat."""
        return (self.condition, self.biological_repeat, self.technical_repeat)


@dataclass(frozen=True)
class DiscoveryResult:
    """What was found in an experiment folder.

    Attributes
    ----------
    root
        The experiment root, as an absolute path.
    leaves
        Leaf folders in sorted path order.
    issues
        Errors and warnings about the folder structure.
    """

    root: Path
    leaves: tuple[LeafFolder, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def images(self) -> tuple[str, ...]:
        """Relative paths of every image (TIFF or OME-Zarr) that will be analysed, sorted."""
        return tuple(sorted(path for leaf in self.leaves for path in leaf.images))

    @property
    def ok(self) -> bool:
        """True if no issue blocks processing."""
        return not has_errors(self.issues)

    def labels_for(self, image: str) -> tuple[str, str, str]:
        """Return the labels of an image given by its relative path."""
        for leaf in self.leaves:
            if image in leaf.images:
                return leaf.labels
        raise KeyError(f"{image!r} is not an analysed image of this experiment.")


def check_output_folder(
    experiment_root: str | os.PathLike, output_folder: str | os.PathLike
) -> list[ValidationIssue]:
    """Check that the output folder is not the experiment folder or inside it.

    Writing inside the experiment would mix results with source images and make
    the next scan pick them up.

    Returns
    -------
    list of ValidationIssue
        One error if the output folder is inside the experiment, otherwise empty.
    """
    root = Path(experiment_root).resolve()
    output = Path(output_folder).resolve()
    if output != root and root not in output.parents:
        return []
    return [
        error(
            "output_inside_experiment",
            "The output folder is inside the experiment folder. Results must be kept "
            "separate from the source images.",
            [output.relative_to(root).as_posix() or ROOT],
            f"Choose an output folder outside the experiment, for example "
            f"{default_output_folder(root)}.",
        )
    ]


def discover_experiment(
    experiment_root: str | os.PathLike, output_folder: str | os.PathLike | None = None
) -> DiscoveryResult:
    """Walk an experiment folder, find its leaf folders and check its structure.

    Folders and files are visited in sorted order, so the same folder always
    gives the same result. Nothing is written and no image is opened.

    Parameters
    ----------
    experiment_root
        The experiment folder selected by the user.
    output_folder
        The planned output folder, if known. It is checked with
        :func:`check_output_folder` and, if it lies inside the experiment, it is
        not scanned.

    Returns
    -------
    DiscoveryResult
        The leaf folders with their labels and every issue found.
    """
    root = Path(experiment_root).resolve()
    if not root.is_dir():
        issue = error(
            "experiment_not_found",
            f"The experiment folder {root} does not exist or is not a folder.",
            action="Select an existing folder that contains your images.",
        )
        return DiscoveryResult(root=root, issues=(issue,))

    walker = _Walker(root)
    if output_folder is not None:
        walker.issues.extend(check_output_folder(root, output_folder))
        walker.excluded = Path(output_folder).resolve()
    walker.visit(root, ())
    issues = (*walker.finish(), *mask_name_issues(walker.leaves))
    return DiscoveryResult(root=root, leaves=tuple(walker.leaves), issues=issues)


def mask_name_issues(
    leaves: Iterable[LeafFolder], mask_names: Sequence[str] = ()
) -> list[ValidationIssue]:
    """Report masks in the experiment folder, which would be analysed as images.

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

    ``image_named_as_mask`` is an error (v0.4.0, owner decision of 1 November 2026): such a
    file is blocked as an image, so a mask (a Cellpose ``_cp_masks`` file next to its image)
    is never analysed; an image that only looks like a mask is renamed, or excluded.
    ``mask_folder_in_experiment`` stays a warning.

    Parameters
    ----------
    leaves
        The experiment's leaf folders (:attr:`DiscoveryResult.leaves`).
    mask_names
        Mask names the caller expects (``"cells"`` for ``f1_cells.tif``), screened besides
        :data:`MASK_NAME_SUFFIXES`; :func:`discover_experiment` gives none.

    Returns
    -------
    list of ValidationIssue
        The issues, or nothing.
    """
    leaves = list(leaves)
    images = [path for leaf in leaves for path in leaf.images]
    stems = _stems(images)
    suffixes = {suffix.lower() for suffix in MASK_NAME_SUFFIXES}
    suffixes |= {f"_{name.lower()}" for name in mask_names}
    named = images_named_as_masks(leaves, sorted(suffixes))
    by_folder: dict[str, list[tuple[str, str]]] = {}
    for relative in images:
        folder = mask_folder_part(relative)
        if folder is None:
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
                f"{plural(len(named), 'file')} {_are(named)} named as a mask of another "
                "image: that image's name followed by "
                + ", ".join(sorted(suffixes))
                + f" ({_few([f'{file} (of {owner})' for file, owner in named])}). A mask is "
                "never a position, so these files are not analysed as images.",
                [file for file, _ in named],
                "If they are masks, move them into a masks folder outside the experiment "
                "folder, laid out like the experiment (<masks>/<the image's folders>/"
                "<image>_<type>.tif). If they are images, rename them, or exclude them.",
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


class _Walker:
    """Collects leaf folders and problem paths during one walk."""

    def __init__(self, root: Path):
        self.root = root
        self.excluded: Path | None = None
        self.leaves: list[LeafFolder] = []
        self.issues: list[ValidationIssue] = []
        self.reserved: list[str] = []
        self.single_with_siblings: list[str] = []
        # Every spelling of each level's folder names, by its case-folded name: names that
        # differ only in case clash anywhere in the experiment (decision D-e).
        self.spellings: list[dict[str, dict[str, list[str]]]] = [{} for _ in LEVELS]
        self.invalid_names: list[str] = []
        self.too_deep: list[str] = []
        self.unreadable: list[str] = []
        self.non_tiff: list[str] = []
        self.empty: list[str] = []
        self.outside_leaf: list[str] = []
        self.same_position: list[str] = []

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix() or ROOT

    def visit(self, folder: Path, names: tuple[str, ...]) -> None:
        try:
            entries = sorted(os.scandir(folder), key=lambda entry: entry.name)
        except OSError:
            logger.debug("Cannot list %s", folder, exc_info=True)
            self.unreadable.append(self.relative(folder))
            return

        subfolders: list[Path] = []
        tiffs: list[str] = []
        for entry in entries:
            path = Path(entry.path)
            if is_system_name(entry.name):
                logger.debug("Skipping system file %s", path)
            elif is_ome_zarr_folder(entry):
                tiffs.append(self.relative(path))
            elif entry.is_dir():
                if path != self.excluded:
                    subfolders.append(path)
            elif is_tiff_name(entry.name):
                tiffs.append(self.relative(path))
            else:
                self.non_tiff.append(self.relative(path))

        if not subfolders:
            if tiffs:
                labels = names + (SINGLE,) * (len(LEVELS) - len(names))
                self.leaves.append(LeafFolder(self.relative(folder), *labels, tuple(tiffs)))
                self._check_positions(tiffs)
            else:
                self.empty.append(self.relative(folder))
            return

        self.outside_leaf.extend(tiffs)
        self._check_names(subfolders, len(names))
        if len(names) == len(LEVELS):
            # Stop here: the folders below a technical repeat have no level to map
            # to, and listing them is enough for the error message.
            self.too_deep.extend(self.relative(path) for path in subfolders)
            return
        for path in subfolders:
            self.visit(path, (*names, path.name))

    def _check_names(self, subfolders: list[Path], level: int) -> None:
        # Names are compared ignoring case, across the whole experiment (decision D-e):
        # Windows and macOS would merge "Ctrl" and "ctrl" when the experiment is copied
        # there, and later bricks would key "Rep1" and "rep1" as two biological repeats
        # where design pairing and other tools take them as one.
        for path in subfolders:
            if level < len(LEVELS):
                spellings = self.spellings[level].setdefault(path.name.casefold(), {})
                spellings.setdefault(path.name, []).append(self.relative(path))
                try:
                    check_identity_text(path.name, LEVELS[level])
                except IdentityError:
                    self.invalid_names.append(self.relative(path))
            if path.name.casefold() != SINGLE:
                continue
            # A folder named "single" gives its level the value a missing level gets:
            # one member. Two cases would make "single" ambiguous, so they stay errors:
            # another spelling ("Single" would be a different label from the "single"
            # written for missing levels), and other folders beside it (the level
            # would then have several members, one of them called "single").
            if path.name != SINGLE:
                self.reserved.append(self.relative(path))
            elif len(subfolders) > 1:
                self.single_with_siblings.append(self.relative(path))

    def _check_positions(self, images: list[str]) -> None:
        # Two images of one technical repeat with the same position identifier would
        # be the same field of view to every later brick, so this is refused rather
        # than guessed. Case is ignored for the same reason as for folder names.
        by_key: dict[str, list[str]] = {}
        for image in images:
            by_key.setdefault(position_id(image).casefold(), []).append(image)
            try:
                check_identity_text(position_id(image), "position_id")
            except IdentityError:
                self.invalid_names.append(image)
        for paths in by_key.values():
            if len(paths) > 1:
                self.same_position.extend(paths)

    def finish(self) -> tuple[ValidationIssue, ...]:
        issues = self.issues
        if self.unreadable:
            issues.append(
                error(
                    "unreadable_folder",
                    f"{plural(len(self.unreadable), 'folder')} could not be opened, so "
                    "images in them would be missed.",
                    self.unreadable,
                    "Check that the folders exist and that you have permission to read them.",
                )
            )
        if self.reserved:
            issues.append(
                error(
                    "reserved_folder_name",
                    f'A folder named "{SINGLE}" marks a level with one member, and its label '
                    f'is always written "{SINGLE}" in lower case. Written in another case it '
                    "would be a different label.",
                    self.reserved,
                    f'Rename these folders to "{SINGLE}" in lower case, or give them a name of '
                    "their own.",
                )
            )
        if self.single_with_siblings:
            issues.append(
                error(
                    "reserved_folder_name",
                    f'A folder named "{SINGLE}" marks a level with one member, so it must be '
                    "the only folder in its place. These folders have other folders beside "
                    "them.",
                    self.single_with_siblings,
                    "Give these folders a name of their own (for example rep1), or move the "
                    "other folders out.",
                )
            )
        case_conflicts = sorted(
            path
            for level in self.spellings
            for spellings in level.values()
            if len(spellings) > 1
            for paths in spellings.values()
            for path in paths
        )
        if case_conflicts:
            issues.append(
                error(
                    "case_conflict",
                    "Some folders of one level (condition, biological repeat or technical "
                    "repeat) have names that differ only in upper/lower case, in the same "
                    "place or anywhere else in the experiment (for example Rep1 in one "
                    "condition and rep1 in another). They would be confused on Windows and "
                    "macOS, and taken for two different labels by the other bricks.",
                    case_conflicts,
                    "Rename the folders so that each name is spelled one way everywhere, or "
                    "so that the names differ by more than case.",
                )
            )
        if self.invalid_names:
            issues.append(
                error(
                    "invalid_name",
                    f"{plural(len(self.invalid_names), 'folder or image name')} cannot be "
                    "used as labels or position identifiers: they start or end with a "
                    "space, or are NA, nan, none, null, n/a or <NA> (in any case), which "
                    "tables read as a "
                    "missing value.",
                    sorted(self.invalid_names),
                    "Rename these folders or images, without spaces at the start or end, and "
                    "not NA, nan, none, null, n/a or <NA>.",
                )
            )
        if self.too_deep:
            issues.append(
                error(
                    "folder_too_deep",
                    "Some folders are below the technical-repeat level. Only three levels "
                    "(condition, biological repeat, technical repeat) are supported.",
                    self.too_deep,
                    "Move the images up into the technical-repeat folder, or select a "
                    "folder lower in the hierarchy as the experiment folder.",
                )
            )
        if self.same_position:
            issues.append(
                error(
                    "duplicate_position_id",
                    "Some images in the same folder have the same name apart from the file "
                    "extension or upper/lower case (for example img_01.tif and img_01.ome.zarr). "
                    "Each image is one position, named after its file, so these positions "
                    "could not be told apart.",
                    sorted(self.same_position),
                    "Rename the images so that their names differ by more than the extension "
                    "or case, or move the extra copies out of the experiment folder.",
                )
            )
        if self.non_tiff:
            issues.append(
                warning(
                    "non_tiff_file",
                    f"{plural(len(self.non_tiff), 'file')} "
                    f"{'is not an image' if len(self.non_tiff) == 1 else 'are not images'}"
                    " (TIFF or OME-Zarr) and will not be analysed.",
                    self.non_tiff,
                    "No action needed if these are not images. Convert images in other "
                    "formats to TIFF or OME-Zarr to include them.",
                )
            )
        if self.empty:
            issues.append(
                warning(
                    "empty_folder",
                    f"{plural(len(self.empty), 'folder')} {_contain(self.empty)} no images "
                    "and no subfolders and will not be analysed.",
                    self.empty,
                    "No action needed if this is expected. Otherwise add the missing images "
                    "or remove the folders.",
                )
            )
        if self.outside_leaf:
            issues.append(
                # An error, not a warning (owner decision, October 2026): a folder holding
                # both images and subfolders has no level for its images, and every brick
                # refuses it, so they are never dropped silently.
                error(
                    "tiff_outside_leaf",
                    f"{plural(len(self.outside_leaf), 'image')} {_are(self.outside_leaf)} "
                    "in folders that also contain subfolders. A folder holds either images "
                    "or subfolders, never both, so these images have no place in the "
                    "experiment's levels.",
                    self.outside_leaf,
                    "Move these images into a folder of their own, or out of the experiment "
                    "folder.",
                )
            )
        if not self.leaves:
            issues.append(
                error(
                    "no_images",
                    "No images were found in folders without subfolders, so there is "
                    "nothing to analyse.",
                    [ROOT],
                    "Check that you selected the right experiment folder and that the "
                    "images are .tif or .tiff files or .ome.zarr folders.",
                )
            )
        return tuple(issues)


def _are(items: list) -> str:
    return "is" if len(items) == 1 else "are"


def _contain(items: list) -> str:
    return "contains" if len(items) == 1 else "contain"
