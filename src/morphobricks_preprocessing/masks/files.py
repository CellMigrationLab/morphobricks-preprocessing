"""Find the masks of every image and pair them by name (contract §15).

Moved from ``masks.py`` (PP-5).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from ..discovery import DiscoveryResult, is_system_name, is_tiff_name
from ..image_names import position_id
from ..metadata import TiffMetadata
from ..validation import ValidationIssue, agree, error, has_errors, plural, warning
from ..zarr_input import (
    NgffError,
    ZarrUnavailableError,
    ngff_label_names,
)
from .checks import _header_issues
from .model import FOLDER, OME_ZARR, MaskFile, MaskInventory

logger = logging.getLogger(__name__)


def find_masks(
    masks_folder: str | os.PathLike | None,
    discovery: DiscoveryResult,
    images: Iterable[TiffMetadata],
    *,
    mask_axes: Mapping[str, str] | None = None,
    output_folder: str | os.PathLike | None = None,
) -> MaskInventory:
    """Pair every image with its masks and check them from their headers, without pixels.

    Parameters
    ----------
    masks_folder
        The masks folder (contract §15), or None to use only the OME-Zarr images' own label
        images. The label images of OME-Zarr images are always used (design v0.4 §2).
    discovery
        The experiment's folders, which say where each image is.
    images
        Header information of the analysed images (excluded images left out); their Y and X
        sizes are what each mask must match.
    mask_axes
        The axes the user states for the masks of a mask type, ``{type: roles}``: one role
        (``Y``, ``X``, ``Z`` or ``T``) per axis of the mask's header, length-1 axes other
        than Y and X left out (``"TYX"`` for a plain ``QYX`` stack of time points; ``"YX"``
        for a 2-D mask shared by every plane of its image). A statement may not contradict
        an axis the metadata names (``mask_axes_contradict_header``). Never a reordering: a
        mask is read as stored.
    output_folder
        The run's output folder: inside the masks folder it is the error
        ``output_inside_masks`` (the run's files would be read as masks).

    Returns
    -------
    MaskInventory
        The masks found and every issue about them.
    """
    images = {meta.relative_path: meta for meta in images}
    issues: list[ValidationIssue] = []
    files: list[MaskFile] = []
    folder = None
    if masks_folder is not None:
        folder = Path(masks_folder).resolve()
        issues.extend(_folder_issues(folder, discovery.root, output_folder))
        if not has_errors(issues):
            found, extra = _folder_masks(folder, discovery, images)
            files.extend(found)
            issues.extend(extra)
    found, unreadable = _label_masks(discovery.root, images)
    files.extend(found)
    issues.extend(_name_issues(files))
    files.sort(key=lambda f: (f.image, f.mask_type))
    issues.extend(_duplicate_issues(files))
    stated = dict(mask_axes or {})
    issues.extend(_header_issues(files, images, folder, discovery.root, unreadable, stated))
    types = tuple(sorted({f.mask_type for f in files}))
    issues.extend(_unknown_type_issues(stated, types))
    issues.extend(_missing_issues(types, files, images))
    return MaskInventory(
        folder=folder,
        types=types,
        files=tuple(files),
        issues=tuple(sorted(issues, key=lambda issue: (not issue.is_error, issue.code))),
    )


def _folder_issues(
    folder: Path, experiment_root: Path, output_folder: str | os.PathLike | None
) -> list[ValidationIssue]:
    """Refuse a masks folder that is missing, or that holds or is held by the run's folders."""
    if not folder.is_dir():
        return [
            error(
                "masks_folder_not_found",
                f"The masks folder {folder} does not exist or is not a folder.",
                action="Select the folder that holds one subfolder per mask type.",
            )
        ]
    if folder == experiment_root or experiment_root in folder.parents:
        return [
            error(
                "masks_inside_experiment",
                "The masks folder is inside the experiment folder, where its folders would be "
                "read as conditions and repeats.",
                [folder.relative_to(experiment_root).as_posix() or "."],
                "Move the masks folder next to the experiment folder and select it there.",
            )
        ]
    output = None if output_folder is None else Path(output_folder).resolve()
    if output is not None and (output == folder or folder in output.parents):
        return [
            error(
                "output_inside_masks",
                "The output folder is inside the masks folder, where the run's own files would "
                "be read as masks.",
                [output.relative_to(folder).as_posix() or "."],
                "Choose an output folder outside the masks folder.",
            )
        ]
    return []


def _folder_masks(
    folder: Path, discovery: DiscoveryResult, images: Mapping[str, TiffMetadata]
) -> tuple[list[MaskFile], list[ValidationIssue]]:
    """Pair the TIFF files of the masks folder with the images, in either layout.

    A file is a mask of an image when its path is ``<type>/<image's folders>/<image name>`` (one
    folder per mask type) or ``<image's folders>/<image name>_<type>`` (the type after the
    image name, as segmentation tools often write it). Each file is read the way that matches
    an analysed image; a file matching two images or both layouts is refused, never guessed.
    A mask matching no image is the warning ``mask_without_image``, and a mask of an excluded
    image the warning ``mask_of_excluded_image``: neither is used (design v0.4 §3 keeps the
    severity of every existing check).
    """
    by_key, by_leaf = {}, {}
    for leaf in discovery.leaves:
        prefix = "" if leaf.relative_path == "." else leaf.relative_path + "/"
        for image in leaf.images:
            # Excluded images pair too, so their masks are told apart from unpaired files.
            stem = position_id(image.rsplit("/", 1)[-1])
            by_key[(prefix + stem).casefold()] = image
            by_leaf.setdefault(prefix.casefold(), []).append((stem, image))
    files, unpaired, loose, ambiguous, excluded = [], [], [], [], []
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder)
        if any(is_system_name(part) for part in relative.parts) or not path.is_file():
            continue
        mask_path = relative.as_posix()
        if not is_tiff_name(path.name):
            (loose if len(relative.parts) == 1 else unpaired).append(mask_path)
            continue
        found = _suffix_matches(relative, by_leaf)
        if len(relative.parts) > 1:
            key = Path(*relative.parts[1:]).with_name(position_id(path.name)).as_posix()
            if key.casefold() in by_key:
                found.append((by_key[key.casefold()], relative.parts[0]))
        if len(found) == 1 and found[0][0] not in images:
            excluded.append(mask_path)
        elif len(found) == 1:
            files.append(MaskFile(found[0][0], found[0][1], mask_path, FOLDER))
        else:
            (ambiguous if found else unpaired).append(mask_path)
    issues = [*_layout_issues(loose, unpaired), *_excluded_issues(excluded)]
    if ambiguous:
        issues.append(
            error(
                "ambiguous_mask",
                f"{plural(len(ambiguous), 'mask')} could belong to more than one image or mask "
                "type (an image name that starts another image's name, or a path that fits both "
                "layouts), so they cannot be paired.",
                ambiguous,
                "Rename the masks (or the images) so that each mask names one image and one type.",
            )
        )
    return files, issues


def _suffix_matches(relative: Path, by_leaf: Mapping[str, list]) -> list[tuple[str, str]]:
    """Return ``(image, mask type)`` for every image whose name, then ``_``, starts the file's."""
    prefix = "" if len(relative.parts) == 1 else relative.parent.as_posix() + "/"
    stem = position_id(relative.name)
    found = []
    for image_stem, image in by_leaf.get(prefix.casefold(), []):
        head = image_stem + "_"
        if len(stem) > len(head) and stem.casefold().startswith(head.casefold()):
            found.append((image, stem[len(head) :]))
    return found


def _layout_issues(loose: list[str], unpaired: list[str]) -> list[ValidationIssue]:
    """Warn about files that are not masks and masks that match no image."""
    issues = []
    if loose:
        issues.append(
            warning(
                "mask_outside_type_folder",
                f"{plural(len(loose), 'file')} in the masks folder "
                f"{agree(loose, 'is', 'are')} not masks "
                "and will not be used.",
                loose,
                "Keep only masks there: Nuclei/<condition>/.../<image>.tif, or "
                "<condition>/.../<image>_Nuclei.tif.",
            )
        )
    if unpaired:
        issues.append(
            warning(
                "mask_without_image",
                f"{plural(len(unpaired), 'file')} in the masks folder "
                f"{agree(unpaired, 'matches', 'match')} no "
                "image of the experiment (same folders and image name) and will not be used.",
                unpaired,
                "Check the folder names and file names against the experiment.",
            )
        )
    return issues


def _excluded_issues(excluded: list[str]) -> list[ValidationIssue]:
    """Report the masks of excluded images apart from unpaired masks: they are not used."""
    if not excluded:
        return []
    return [
        warning(
            "mask_of_excluded_image",
            f"{plural(len(excluded), 'mask')} belong to excluded images and are not used.",
            excluded,
            "Nothing to do; analyse the images again to use their masks.",
        )
    ]


def _unknown_type_issues(stated: Mapping[str, str], types: tuple[str, ...]) -> list:
    """Refuse axes stated for a mask type that is not found."""
    unknown = sorted(set(stated) - set(types))
    if not unknown:
        return []
    return [
        error(
            "invalid_mask_axes",
            f"Mask axes were stated for {', '.join(unknown)}, which "
            f"{agree(unknown, 'is not a mask type', 'are not mask types')} found "
            f"(found: {', '.join(types) or 'none'}).",
            unknown,
            "State the axes by the mask type's name, as its folder or suffix gives it.",
        )
    ]


def _label_masks(
    root: Path, images: Mapping[str, TiffMetadata]
) -> tuple[list[MaskFile], list[str]]:
    """Return the label images of every OME-Zarr image, as masks.

    Also return the ``<image>/labels`` groups that could not be read: their masks would
    otherwise vanish without a word (and with them the ``mask_missing`` warning), so they
    are reported as ``unreadable_mask``.
    """
    files, unreadable = [], []
    for image, meta in images.items():
        if meta.kind != "ome-zarr" or not meta.readable:
            continue
        try:
            names = ngff_label_names(root / image)
        except ZarrUnavailableError as exc:  # documented: label images need the zarr extra
            logger.info("No label images read from %s: %s", image, exc)
            continue
        except NgffError as exc:
            logger.info("The label images of %s could not be read: %s", image, exc)
            unreadable.append(f"{image}/labels")
            continue
        files.extend(MaskFile(image, name, f"{image}/labels/{name}", OME_ZARR) for name in names)
    return files, unreadable


def _name_issues(files: list[MaskFile]) -> list[ValidationIssue]:
    by_folded: dict[str, set[str]] = {}
    for f in files:
        by_folded.setdefault(f.mask_type.casefold(), set()).add(f.mask_type)
    clashes = sorted(name for names in by_folded.values() if len(names) > 1 for name in names)
    if not clashes:
        return []
    return [
        error(
            "mask_names_differ_in_case",
            "Some mask types have names that differ only in upper and lower case, so they "
            "cannot be told apart: " + ", ".join(clashes) + ".",
            clashes,
            "Give every mask type its own name.",
        )
    ]


def _duplicate_issues(files: list[MaskFile]) -> list[ValidationIssue]:
    seen: dict[tuple[str, str], MaskFile] = {}
    duplicates = []
    for f in files:
        key = (f.image, f.mask_type.casefold())
        if key in seen:
            duplicates += [seen[key].path, f.path]
        seen[key] = f
    if not duplicates:
        return []
    return [
        error(
            "duplicate_mask",
            "Some images have two masks of the same type (for example a file in the masks "
            "folder and a label image of the same name, or two files with the same name and "
            "different extensions).",
            sorted(set(duplicates)),
            "Keep one mask per image and type.",
        )
    ]


def _missing_issues(
    types: tuple[str, ...], files: list[MaskFile], images: Mapping[str, TiffMetadata]
) -> list[ValidationIssue]:
    """Warn about images without a mask of a type that other images have."""
    have = {(f.image, f.mask_type) for f in files}
    issues = []
    for mask_type in types:
        missing = sorted(image for image in images if (image, mask_type) not in have)
        if missing:
            issues.append(
                warning(
                    "mask_missing",
                    f"{plural(len(missing), 'image')} "
                    f"{agree(missing, 'has', 'have')} no {mask_type} mask; "
                    f"their {mask_type} objects cannot be measured.",
                    missing,
                    f"Add the {mask_type} masks of these images, or exclude the images.",
                )
            )
    return issues
