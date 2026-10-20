"""Folder discovery: find the leaf folders of an experiment and label them.

The experiment root holds up to three levels of folders (condition, biological
repeat, technical repeat). A folder with images and no subfolders is a leaf;
its labels are the folder names on the way down, with ``single`` for the levels
the branch does not have. The rules are in ``docs/contract.md`` §1.

Images are TIFF files (``.tif``, ``.tiff``) and OME-Zarr images, which are
folders whose names end in ``.zarr`` (usually ``.ome.zarr``). An OME-Zarr folder
counts as one image, not as a subfolder, and is never entered.

Only folder and file names are read here; image headers are inspected by
:mod:`morphobricks_preprocessing.metadata`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .records import LEVELS, SINGLE
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
        Labels from the folder names; ``"single"`` for missing levels.
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
    return DiscoveryResult(root=root, leaves=tuple(walker.leaves), issues=walker.finish())


class _Walker:
    """Collects leaf folders and problem paths during one walk."""

    def __init__(self, root: Path):
        self.root = root
        self.excluded: Path | None = None
        self.leaves: list[LeafFolder] = []
        self.issues: list[ValidationIssue] = []
        self.reserved: list[str] = []
        self.case_conflicts: list[str] = []
        self.too_deep: list[str] = []
        self.unreadable: list[str] = []
        self.non_tiff: list[str] = []
        self.empty: list[str] = []
        self.outside_leaf: list[str] = []

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
            else:
                self.empty.append(self.relative(folder))
            return

        self.outside_leaf.extend(tiffs)
        self._check_names(subfolders)
        if len(names) == len(LEVELS):
            # Stop here: the folders below a technical repeat have no level to map
            # to, and listing them is enough for the error message.
            self.too_deep.extend(self.relative(path) for path in subfolders)
            return
        for path in subfolders:
            self.visit(path, (*names, path.name))

    def _check_names(self, subfolders: list[Path]) -> None:
        # Names are compared ignoring case because Windows and macOS do too:
        # "Ctrl" and "ctrl" would collide when the experiment is copied there.
        by_key: dict[str, list[Path]] = {}
        for path in subfolders:
            by_key.setdefault(path.name.casefold(), []).append(path)
            if path.name.casefold() == SINGLE:
                self.reserved.append(self.relative(path))
        for paths in by_key.values():
            if len(paths) > 1:
                self.case_conflicts.extend(self.relative(path) for path in paths)

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
                    f'"{SINGLE}" is reserved for missing hierarchy levels and cannot be '
                    "used as a folder name.",
                    self.reserved,
                    f'Rename these folders to something other than "{SINGLE}".',
                )
            )
        if self.case_conflicts:
            issues.append(
                error(
                    "case_conflict",
                    "Some folders in the same place have names that differ only in "
                    "upper/lower case. They would be confused on Windows and macOS.",
                    self.case_conflicts,
                    "Rename the folders so that their names differ by more than case, or "
                    "merge them.",
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
                warning(
                    "tiff_outside_leaf",
                    f"{plural(len(self.outside_leaf), 'image')} {_are(self.outside_leaf)} "
                    "in folders that also contain subfolders and will not be analysed. Only "
                    "images in folders without subfolders are used.",
                    self.outside_leaf,
                    "Move these images into a folder of their own if they should be analysed.",
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
