"""Find the masks of every image and check them from their headers (``docs/contract.md`` §15).

Masks (segmentations: label images of cells, nuclei, ...) are kept in a masks folder outside
the experiment, one subfolder per mask type, each mirroring the experiment's folders::

    <masks folder>/<mask type>/<condition>/<biological repeat>/<technical repeat>/<image>.tif

A mask belongs to the image with the same position (same leaf folder, same ``position_id``,
contract §1). The type may also follow the image name instead of being a folder::

    <masks folder>/<condition>/<biological repeat>/<technical repeat>/<image>_<mask type>.tif

and the two layouts may be mixed. Its file may be a TIFF (``.tif``, ``.tiff``, any case, with
an optional ``.ome``). The label images of an OME-Zarr image (its OME-NGFF ``labels`` group)
are masks too, of the type named by the label image, when the ``zarr`` extra is installed.

Any number of mask types is accepted; each is named by its folder, by the suffix after the
image name, or by its label image, and two
names may not differ only in case. Only headers are read here, never pixels: a mask has the
image's Y and X sizes and integer or boolean pixels. Its other dimensions (length-1 axes
aside; Y and X are kept whatever their length, so a mask one pixel high is a 2D ``YX`` mask)
must be labelled depth (Z) or time (T) in its own metadata, never guessed, and every
mask of one image has the same depth slices and time points. Whether those are the image's
planes needs the confirmed axis roles: :func:`mask_plane_issues`, run by
:func:`.run.run_experiment` (issue #36). Reading the pixels and relating the masks to each
other is :mod:`.mask_scan`.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import numpy as np
import tifffile

from .axes import AxisMapping
from .discovery import DiscoveryResult, is_system_name, is_tiff_name, position_id
from .metadata import TiffMetadata, named_axes
from .validation import ValidationIssue, error, has_errors, plural, warning
from .zarr_input import (
    NgffError,
    ZarrUnavailableError,
    is_zarr_name,
    ngff_label_names,
    open_ngff_labels,
)

logger = logging.getLogger(__name__)

FOLDER = "folder"
"""Source of a mask kept as a TIFF file in the masks folder."""

OME_ZARR = "ome-zarr"
"""Source of a mask kept as a label image inside an OME-Zarr image."""

PLANE_AXES = ("Z", "T")
"""The dimensions a mask may have besides Y and X: depth slices and time points."""

_YX = frozenset("YX")
"""The axes of a mask plane, kept by the header and the readers even when of length 1."""


@dataclass(frozen=True)
class MaskFile:
    """One mask of one image.

    Attributes
    ----------
    image
        The image's path relative to the experiment root.
    mask_type
        The mask type: its folder name in the masks folder, or the label image's name.
    path
        The mask's path: relative to the masks folder for a TIFF mask; for a label image of
        an OME-Zarr image, ``<image>/labels/<name>`` relative to the experiment root.
    source
        :data:`FOLDER` or :data:`OME_ZARR`.
    shape
        Shape of the mask without its length-1 axes other than Y and X, ``(Y, X)`` for a 2D
        mask (``(1, 512)`` for a mask one pixel high).
    dtype
        Pixel type, or None when the header could not be read.
    axes
        One letter per entry of ``shape``, from the mask's own metadata: ``"YX"`` for a 2D
        mask, with ``Z`` and/or ``T`` for a mask with several depth slices or time points
        (e.g. ``"TYX"``). Empty when the header could not be read.
    """

    image: str
    mask_type: str
    path: str
    source: str
    shape: tuple[int, ...] = ()
    dtype: np.dtype | None = None
    axes: str = ""

    @property
    def planes(self) -> dict[str, int]:
        """Number of depth slices (``"Z"``) and time points (``"T"``); 1 for an axis it lacks."""
        return {
            axis: self.shape[self.axes.index(axis)] if axis in self.axes else 1
            for axis in PLANE_AXES
        }


@dataclass(frozen=True)
class MaskInventory:
    """Every mask found for the images of an experiment.

    Attributes
    ----------
    folder
        The masks folder, as an absolute path; None when only OME-Zarr label images were used.
    types
        The mask types found, sorted.
    files
        One entry per mask, sorted by image then type.
    issues
        Errors and warnings about the masks.
    """

    folder: Path | None
    types: tuple[str, ...] = ()
    files: tuple[MaskFile, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        """True if no issue blocks using the masks."""
        return not has_errors(self.issues)

    @property
    def images(self) -> tuple[str, ...]:
        """Images with at least one mask, sorted."""
        return tuple(sorted({f.image for f in self.files}))

    def for_image(self, image: str) -> Mapping[str, MaskFile]:
        """Return the masks of one image, by type (empty when it has none)."""
        return MappingProxyType({f.mask_type: f for f in self.files if f.image == image})

    def to_manifest(self) -> dict:
        """Return the inventory as recorded in the run manifest (contract §9)."""
        return {
            "folder": None if self.folder is None else self.folder.as_posix(),
            "types": list(self.types),
            "files": [
                {
                    "image": f.image,
                    "type": f.mask_type,
                    "path": f.path,
                    "source": f.source,
                    "axes": f.axes,
                    "shape": list(f.shape),
                }
                for f in self.files
            ],
        }


def find_masks(
    masks_folder: str | os.PathLike | None,
    discovery: DiscoveryResult,
    images: Iterable[TiffMetadata],
    *,
    ome_zarr_labels: bool = True,
) -> MaskInventory:
    """Pair every image with its masks and check them from their headers, without pixels.

    Parameters
    ----------
    masks_folder
        The masks folder (contract §15), or None to use only the OME-Zarr images' own label
        images.
    discovery
        The experiment's folders, which say where each image is.
    images
        Header information of the analysed images (excluded images left out); their Y and X
        sizes are what each mask must match.
    ome_zarr_labels
        Also use the label images of OME-Zarr images (needs the ``zarr`` extra; skipped
        silently without it).

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
        issues.extend(_folder_issues(folder, discovery.root))
        if not has_errors(issues):
            found, extra = _folder_masks(folder, discovery, images)
            files.extend(found)
            issues.extend(extra)
    unreadable: list[str] = []
    if ome_zarr_labels:
        found, unreadable = _label_masks(discovery.root, images)
        files.extend(found)
    issues.extend(_name_issues(files))
    files.sort(key=lambda f: (f.image, f.mask_type))
    issues.extend(_duplicate_issues(files))
    issues.extend(_header_issues(files, images, folder, discovery.root, unreadable))
    types = tuple(sorted({f.mask_type for f in files}))
    issues.extend(_missing_issues(types, files, images))
    return MaskInventory(
        folder=folder,
        types=types,
        files=tuple(files),
        issues=tuple(sorted(issues, key=lambda issue: (not issue.is_error, issue.code))),
    )


def read_mask(
    mask: MaskFile,
    masks_folder: Path | None,
    experiment_root: Path,
    *,
    z: int | None = None,
    t: int | None = None,
) -> np.ndarray:
    """Read one plane of a mask as a 2D array.

    Parameters
    ----------
    mask
        The mask, as found by :func:`find_masks`.
    masks_folder, experiment_root
        Where its file is (:attr:`MaskInventory.folder` for a TIFF mask, the experiment for
        a label image).
    z, t
        The depth slice and time point to read, zero-based, the image's ``z`` and ``t``. Each
        is required when the mask has that axis (``mask.axes``), and may only be None or 0
        otherwise: a mask's planes are never guessed (issue #36).

    Raises
    ------
    ValueError
        If the mask's dimensions are not the ones its header gave (``mask.axes``), or ``z``
        or ``t`` is missing or outside the mask, or the plane holds a label outside 0 to
        :data:`LARGEST_LABEL` (:func:`check_labels`).
    """
    wanted = {"Z": z, "T": t}
    for axis, index in wanted.items():
        length = mask.planes[axis] if mask.axes else 1
        if index is None and length > 1:
            raise ValueError(
                f"the mask {mask.path} has {length} planes along {axis}; say which one to read"
            )
        if index is not None and not 0 <= index < length:
            raise ValueError(f"the mask {mask.path} has no plane {axis}={index}")
    if mask.source == FOLDER:
        pixels, axes = _read_tiff_plane(Path(masks_folder) / mask.path, wanted)
    else:
        name = mask.path.rsplit("/labels/", 1)[1]
        image = experiment_root / mask.path.rsplit("/labels/", 1)[0]
        labels = open_ngff_labels(image, name)
        selection = _plane_index(labels.axes, labels.array.shape, wanted)
        pixels = np.asarray(labels.array[selection])
        axes = "".join(a for a, s in zip(labels.axes, selection, strict=True) if s == slice(None))
    if axes != "YX" or (mask.axes and pixels.shape != tuple(_yx(mask.axes, mask.shape))):
        raise ValueError(f"the mask {mask.path} is not the 2D plane its header gave ({axes})")
    check_labels(pixels, f"the mask {mask.path}")
    return pixels


def read_mask_file(
    path: str | os.PathLike, *, z: int | None = None, t: int | None = None
) -> np.ndarray:
    """Read one plane of a mask file by its own header, without :func:`find_masks`.

    For bricks given a mask path directly (MorphoBricks D86): the mask is read as
    :func:`read_mask` reads it, with the same rules. Its axes are those its metadata names
    (length-1 axes aside, except Y and X: a mask one pixel high or wide is read as a 2D
    array): Y and X, plus at most a depth axis Z and a time axis T; a
    dimension the metadata does not name (a plain stack, ``IYX``) is refused, never taken
    for depth, time or a channel. ``z`` and ``t`` select the plane and are required for a
    mask with that axis. The labels are checked with :func:`check_labels`.

    Parameters
    ----------
    path
        A TIFF mask, or a label image of an OME-Zarr image, ``<image>.zarr/labels/<name>``
        (needs the ``zarr`` extra).
    z, t
        The depth slice and time point to read, zero-based.

    Returns
    -------
    numpy.ndarray
        The 2D plane, in the mask's own pixel type.

    Raises
    ------
    ValueError
        If the mask cannot be opened or holds several series, has a dimension its metadata
        does not name as Z or T, is not of integers or booleans, ``z`` or ``t`` is missing
        or outside the mask, or a label is outside 0 to :data:`LARGEST_LABEL`.
    """
    path = Path(path).absolute()  # the reader joins the mask's path to its folder
    label_image = path.parent.name == "labels" and is_zarr_name(path.parent.parent.name)
    mask = MaskFile("", "", path.as_posix(), OME_ZARR if label_image else FOLDER)
    try:
        axes, shape, dtype = _mask_header(mask, path.parent, path.parent)
    except (OSError, ValueError, tifffile.TiffFileError, NgffError, ZarrUnavailableError) as exc:
        raise ValueError(f"the mask {path} could not be opened: {exc}") from exc
    if not _known_axes(axes):
        raise ValueError(
            f"the mask {path} has axes {axes!r} (shape {shape}): {_axes_problem(axes, shape)}"
        )
    if dtype.kind not in "biu":
        raise ValueError(
            f"the mask {path} is of {dtype}, not integer labels or true/false; save the mask "
            "as integers"
        )
    return read_mask(
        MaskFile("", "", mask.path, mask.source, shape, dtype, axes),
        path.parent,
        path.parent,
        z=z,
        t=t,
    )


def _read_tiff_plane(path: Path, wanted: Mapping[str, int | None]) -> tuple[np.ndarray, str]:
    """Read the plane at ``wanted`` Z and T of a TIFF mask; return it and its axes.

    One page is read when the planes are stored one per page (the usual layout); otherwise
    the whole mask is read and the plane taken from it.
    """
    with tifffile.TiffFile(path) as tif:
        series = tif.series[0]
        axes, shape = _tiff_axes(series)
        index = _plane_index(axes, shape, wanted)
        kept = "".join(a for a, i in zip(axes, index, strict=True) if i == slice(None))
        pages = len(series.pages)
        split = next(
            (k for k in range(len(shape) + 1) if math.prod(shape[:k]) == pages),
            None,
        )
        if (
            split is not None
            and all(isinstance(i, int) for i in index[:split])
            and all(n == 1 or a in "YX" for a, n in zip(axes[split:], shape[split:], strict=True))
            and pages > 1
        ):
            key = int(np.ravel_multi_index(index[:split], shape[:split]))
            pixels = tif.asarray(key=key, series=0).reshape(shape[split:])
            pixels = pixels[index[split:]]
        else:
            pixels = series.asarray().reshape(shape)[index]
    return pixels, kept


def _tiff_axes(series: tifffile.TiffPageSeries) -> tuple[str, tuple[int, ...]]:
    """Return the axes and shape of a TIFF mask's series, as ``tifffile`` gives them.

    One exception: a series without Y or X stored in one 2D page, for example a 1D array
    written with ``tifffile``'s default metadata (``{"shape": [N]}``, axes ``X``), is that
    page: ``YX`` with the page's shape, one row of N pixels. The page's rows and columns are
    the TIFF's own (as for any plain 2D page, decision D-d), so nothing is guessed; any other
    series without Y or X keeps its axes and is refused.
    """
    axes, shape = str(series.axes), tuple(int(n) for n in series.shape)
    if "Y" in axes and "X" in axes:
        return axes, shape
    page = series.keyframe
    if len(series.pages) == 1 and page.axes == "YX" and math.prod(page.shape) == math.prod(shape):
        return "YX", tuple(int(n) for n in page.shape)
    return axes, shape


def _plane_index(
    axes: str, shape: tuple[int, ...], wanted: Mapping[str, int | None]
) -> tuple[int | slice, ...]:
    """Return the index of the plane at ``wanted`` Z and T: whole Y and X, 0 on other axes.

    Y and X are kept whatever their length, so a mask one pixel high (or wide) stays 2D;
    every other length-1 axis is dropped (index 0). The header check (:func:`_mask_header`,
    :func:`_known_axes`) has already refused any other axis longer than 1.
    """
    index: list[int | slice] = []
    for a, n in zip(axes, shape, strict=True):
        if a in _YX:
            index.append(slice(None))
        elif a in wanted and n > 1:
            index.append(wanted.get(a) or 0)
        else:
            index.append(0 if n == 1 else slice(None))
    return tuple(index)


def _yx(axes: str, shape: tuple[int, ...]) -> tuple[int, int]:
    return shape[axes.index("Y")], shape[axes.index("X")]


LARGEST_LABEL = int(np.iinfo(np.int32).max)
"""The largest label a mask may hold, 2,147,483,647: MorphoBricks labels are int32
(decision D-b, 30 October 2026), so every brick stores a label the same way."""


def check_labels(pixels: np.ndarray, name: str = "the mask") -> None:
    """Refuse a mask whose pixels are not labels from 0 to :data:`LARGEST_LABEL`.

    Label 0 is the background and every object has a positive label (contract §15). A
    negative value is neither, so it is refused rather than read as an object (which would
    also make the background one). A label above :data:`LARGEST_LABEL` cannot be stored as
    an int32 label, the one label type of every MorphoBricks brick (decision D-b), so it is
    refused rather than wrapped or widened. Pixels that are not whole numbers (floats) are
    not labels. Boolean masks are labels 0 and 1. This is the one label check every brick
    calls (MorphoBricks D86).

    Parameters
    ----------
    pixels
        The mask, or one plane of it.
    name
        The mask as the message names it, for example ``"the mask Cells/ctrl/a.tif"``.

    Raises
    ------
    ValueError
        Naming the mask and the offending value: a pixel below 0, a pixel above
        :data:`LARGEST_LABEL`, or a pixel type other than integer or boolean.
    """
    pixels = np.asarray(pixels)
    if pixels.dtype.kind not in "biu":
        raise ValueError(
            f"{name} does not hold whole-number labels (pixel type {pixels.dtype}); save the "
            "mask as integers (labels) or true/false."
        )
    if pixels.dtype.kind == "b" or not pixels.size:
        return
    smallest = pixels.min()
    if smallest < 0:
        raise ValueError(
            f"{name} holds negative labels (smallest {smallest}); label 0 is the "
            "background and objects must have positive labels."
        )
    largest = pixels.max()
    if largest > LARGEST_LABEL:
        raise ValueError(
            f"{name} holds labels above {LARGEST_LABEL} (largest {largest}); MorphoBricks "
            "stores labels as 32-bit signed numbers (int32), so relabel the objects from 1."
        )


def _folder_issues(folder: Path, experiment_root: Path) -> list[ValidationIssue]:
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
    return []


def _folder_masks(
    folder: Path, discovery: DiscoveryResult, images: Mapping[str, TiffMetadata]
) -> tuple[list[MaskFile], list[ValidationIssue]]:
    """Pair the TIFF files of the masks folder with the images, in either layout.

    A file is a mask of an image when its path is ``<type>/<image's folders>/<image name>`` (one
    folder per mask type) or ``<image's folders>/<image name>_<type>`` (the type after the
    image name, as segmentation tools often write it). Each file is read the way that matches
    an analysed image; a file matching two images or both layouts is refused, never guessed.
    """
    by_key, by_leaf = {}, {}
    for leaf in discovery.leaves:
        prefix = "" if leaf.relative_path == "." else leaf.relative_path + "/"
        for image in leaf.images:
            if image in images:
                stem = position_id(image.rsplit("/", 1)[-1])
                by_key[(prefix + stem).casefold()] = image
                by_leaf.setdefault(prefix.casefold(), []).append((stem, image))
    files, unpaired, loose, ambiguous = [], [], [], []
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
        if len(found) == 1:
            files.append(MaskFile(found[0][0], found[0][1], mask_path, FOLDER))
        else:
            (ambiguous if found else unpaired).append(mask_path)
    issues = _layout_issues(loose, unpaired)
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
    issues = []
    if loose:
        issues.append(
            warning(
                "mask_outside_type_folder",
                f"{plural(len(loose), 'file')} in the masks folder {_are(loose)} not masks "
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
                f"{plural(len(unpaired), 'file')} in the masks folder {_match(unpaired)} no "
                "analysed image (same folders and image name) and will not be used.",
                unpaired,
                "Check the folder names and file names against the experiment, or remove "
                "masks of excluded images.",
            )
        )
    return issues


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


def _header_issues(
    files: list[MaskFile],
    images: Mapping[str, TiffMetadata],
    folder: Path | None,
    root: Path,
    unreadable: Iterable[str] = (),
) -> list[ValidationIssue]:
    """Read each mask's header (axes, shape, pixel type) and check it against its image.

    Also check that the masks of one image have the same depth slices and time points.
    ``unreadable`` are paths already known not to open (OME-Zarr ``labels`` groups).
    """
    problems: dict[str, list[str]] = {}
    if unreadable:
        problems["unreadable_mask"] = list(unreadable)
    for index, mask in enumerate(files):
        try:
            axes, shape, dtype = _mask_header(mask, folder, root)
        except (
            OSError,
            ValueError,
            tifffile.TiffFileError,
            NgffError,
            ZarrUnavailableError,
        ) as exc:
            logger.info("Mask %s could not be opened: %s", mask.path, exc)
            problems.setdefault("unreadable_mask", []).append(mask.path)
            continue
        if not _known_axes(axes):
            problems.setdefault("mask_not_2d", []).append(mask.path)
            continue
        files[index] = MaskFile(
            mask.image, mask.mask_type, mask.path, mask.source, shape, dtype, axes
        )
        expected = _image_yx(images[mask.image])
        if expected is not None and _yx(axes, shape) != expected:
            problems.setdefault("mask_size_mismatch", []).append(mask.path)
        if dtype.kind not in "biu":
            problems.setdefault("mask_pixel_type", []).append(mask.path)
    by_image: dict[str, list[MaskFile]] = {}
    for mask in files:
        if mask.axes:
            by_image.setdefault(mask.image, []).append(mask)
    for masks in by_image.values():
        if len({tuple(m.planes.items()) for m in masks}) > 1:
            problems.setdefault("mask_planes_differ", []).extend(m.path for m in masks)
    return [
        error(code, _TEXTS[code][0], paths, _TEXTS[code][1])
        for code, paths in sorted(problems.items())
    ]


_TEXTS = {
    "unreadable_mask": (
        "Some masks could not be opened.",
        "Check that they are TIFF files (or OME-Zarr label images, listed in the image's "
        "labels group) with one image each and not damaged; the technical details are in the "
        "log.",
    ),
    "mask_not_2d": (
        "Some masks have dimensions other than Y and X that their metadata does not name as "
        "depth (Z) or time (T), so their planes cannot be matched to the image's.",
        "Save each mask as a 2D label image, or with its depth and time axes named in its "
        "metadata (for example an ImageJ hyperstack or OME-TIFF with axes TYX or ZYX).",
    ),
    "mask_planes_differ": (
        "The masks of some images have different numbers of depth slices or time points, so "
        "they cannot all belong to the same planes of their image.",
        "Make every mask of an image with the image's depth slices and time points.",
    ),
    "mask_size_mismatch": (
        "Some masks do not have the size (Y and X) of their image.",
        "Check that each mask was made from its image, at full resolution.",
    ),
    "mask_pixel_type": (
        "Some masks do not hold whole numbers: mask pixels must be integers (labels) or "
        "true/false.",
        "Save the masks as integer label images.",
    ),
}


def _mask_header(mask: MaskFile, folder: Path | None, root: Path) -> tuple:
    """Return a mask's axes and shape without length-1 axes other than Y and X, and its dtype.

    The axes are the mask's own metadata (``tifffile``'s series axes, or the OME-NGFF axes of
    the label image); no pixels are read. Y and X are kept whatever their length: a mask one
    pixel high is ``YX`` with shape ``(1, N)``, never the 1D ``X`` (``tifffile`` and OME-NGFF
    give both axes, so nothing is guessed). Only the other length-1 axes are dropped.
    """
    if mask.source == FOLDER:
        with tifffile.TiffFile(Path(folder) / mask.path) as tif:
            if len(tif.series) != 1:
                raise ValueError(f"{len(tif.series)} image series")
            axes, shape = _tiff_axes(tif.series[0])
            dtype = tif.series[0].dtype
    else:
        image, name = mask.path.rsplit("/labels/", 1)
        labels = open_ngff_labels(root / image, name)
        axes, shape, dtype = labels.axes, labels.array.shape, np.dtype(labels.array.dtype)
    kept = [(a, int(n)) for a, n in zip(axes, shape, strict=True) if n != 1 or a in _YX]
    return (
        "".join(a for a, _ in kept),
        tuple(n for _, n in kept),
        dtype.newbyteorder("="),
    )


def _axes_problem(axes: str, shape: tuple[int, ...]) -> str:
    """Say why axes that :func:`_known_axes` refuses are not a mask's.

    The axes are :func:`_mask_header`'s: length-1 axes other than Y and X already left out.
    """
    missing = [a for a in "YX" if a not in axes]
    if missing:
        return (
            f"its header gives no {' or '.join(missing)} axis, so its rows and columns are not "
            "known"
        )
    repeated = sorted({a for a in axes if axes.count(a) > 1})
    if repeated:
        return f"its header gives {', '.join(repeated)} more than once"
    other = [
        f"{a} (length {n})"
        for a, n in zip(axes, shape, strict=True)
        if a not in _YX and a not in PLANE_AXES
    ]
    several = len(other) > 1
    return (
        f"the dimension{'s' if several else ''} {', '.join(other)} {'are' if several else 'is'} "
        f"neither Y nor X, and its metadata does not name {'them' if several else 'it'} depth "
        "(Z) or time (T)"
    )


def _known_axes(axes: str) -> bool:
    """Return True if the axes are Y and X, each once, plus at most one Z and one T."""
    return (
        axes.count("Y") == 1
        and axes.count("X") == 1
        and all(axes.count(a) <= 1 for a in PLANE_AXES)
        and set(axes) <= {"Y", "X", *PLANE_AXES}
    )


def mask_plane_issues(
    inventory: MaskInventory,
    images: Iterable[TiffMetadata],
    file_mappings: Mapping[str, AxisMapping],
    *,
    skip: Iterable[str] = (),
) -> list[ValidationIssue]:
    """Check that every mask has the depth slices and time points of its image (issue #36).

    An image's depth slices and time points come from its confirmed axis roles; a mask's
    from its own metadata (:attr:`MaskFile.planes`). They must be equal: a 2D mask belongs
    to an image with one plane, never to every plane of a Z-stack or time-lapse.

    Parameters
    ----------
    inventory
        The masks found by :func:`find_masks`.
    images
        Header information of the analysed images.
    file_mappings
        Each image's own axis mapping (:attr:`.axes.AxisReview.file_mappings`); images
        without one are left to the axis review's errors.
    skip
        Images not to check: those with an axis error, as in :func:`mask_size_issues`.

    Returns
    -------
    list of ValidationIssue
        The error ``mask_planes_mismatch`` naming the masks that do not fit, or nothing.
    """
    headers = {meta.relative_path: meta for meta in images}
    skip = set(skip)
    mismatched = []
    for mask in inventory.files:
        mapping = file_mappings.get(mask.image)
        if not mask.axes or mapping is None or mask.image not in headers or mask.image in skip:
            continue
        shape = headers[mask.image].shape
        planes = {}
        for axis in PLANE_AXES:
            index = mapping.index_of(axis)
            planes[axis] = 1 if index is None else int(shape[index])
        if planes != mask.planes:
            mismatched.append(mask.path)
    if not mismatched:
        return []
    return [
        error(
            "mask_planes_mismatch",
            f"{plural(len(mismatched), 'mask')} {_do(mismatched)} not have the depth slices "
            f"and time points of {_their(mismatched)} image: a 2D mask fits only an image with "
            "one plane, and a mask with depth (Z) or time (T) needs the same number of slices "
            "or time points as its image.",
            mismatched,
            "Save one mask plane for every depth slice and time point of the image, with the "
            "axes named in the mask's metadata, or check the image's axis roles.",
        )
    ]


def _image_yx(meta: TiffMetadata) -> tuple[int, int] | None:
    """Return the Y and X sizes the image's header names, or None when it does not name them.

    Used by :func:`find_masks`, before the axis roles are confirmed. A plain stack's ``Y``
    and ``X`` are only where its pages' rows and columns are stored
    (:func:`.metadata.named_axes`), so its masks are compared by the confirmed roles only
    (:func:`mask_size_issues`).
    """
    named = named_axes(meta)
    if "Y" not in named or "X" not in named:
        return None
    return (meta.shape[named.index("Y")], meta.shape[named.index("X")])


def _missing_issues(
    types: tuple[str, ...], files: list[MaskFile], images: Mapping[str, TiffMetadata]
) -> list[ValidationIssue]:
    have = {(f.image, f.mask_type) for f in files}
    issues = []
    for mask_type in types:
        missing = sorted(image for image in images if (image, mask_type) not in have)
        if missing:
            issues.append(
                warning(
                    "mask_missing",
                    f"{plural(len(missing), 'image')} {_have(missing)} no {mask_type} mask; "
                    f"their {mask_type} objects cannot be measured.",
                    missing,
                    f"Add the {mask_type} masks of these images, or exclude the images.",
                )
            )
    return issues


def _do(items: list) -> str:
    return "does" if len(items) == 1 else "do"


def _their(items: list) -> str:
    return "its" if len(items) == 1 else "their"


def _are(items: list) -> str:
    return "is" if len(items) == 1 else "are"


def _match(items: list) -> str:
    return "matches" if len(items) == 1 else "match"


def _have(items: list) -> str:
    return "has" if len(items) == 1 else "have"
