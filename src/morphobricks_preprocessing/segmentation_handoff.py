"""The hand-off to a segmentation tool: images in by channel ID, masks out as §15 expects them.

A segmentation tool (Cellpose in the shipped notebook) runs outside this brick. This module
is everything the tool's notebook needs from preprocessing, with numpy and tifffile only, and
imports no segmentation tool (contract §15.5):

- :func:`images_to_segment`: the run's images that have no mask of a mask type yet;
- :func:`segmentation_input`: the channels asked for **by canonical channel ID**, as a
  ``(C, Y, X)`` array of the stored values in the order asked, never by position;
- :func:`mask_path` and :func:`write_mask`: one label image per image at
  ``<masks>/<type>/<image's folders>/<image name>.tif``, checked against the image, written as
  a compressed ``int32`` TIFF, atomically, and never over a mask that is already there unless
  asked;
- the provenance file of the type (:mod:`.segmentation_record`).

Nothing is guessed. A channel is resolved by :meth:`.run_inputs.RunInputs.resolve_request`,
whose refusals pass through unchanged (a channel that some images lack is
``A_SCOPE_INCOMPLETE``: choose channels every image has). Images with more than one Z plane or
time point are refused: this hand-off is 2D only. A mask is never filled, rescaled or
converted: wrong labels are refused before anything is written.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
import tifffile

from ._io import give_output_mode
from .folder_rules import masks_folder_issues
from .links import relative_inside
from .run_inputs import Fov, RunInputs
from .run_inputs_request import Request
from .segmentation_record import SegmentationError, check_mask_type

INT32_MAX = 2_147_483_647
"""The largest label every brick accepts (contract §15, §16)."""
_SUFFIXES = (".ome.tiff", ".ome.tif", ".tiff", ".tif")  # the TIFF names pairing accepts (§15)


def images_to_segment(
    inputs: RunInputs, mask_type: str, masks_root: str | os.PathLike
) -> tuple[Fov, ...]:
    """Return the run's images that have no mask of ``mask_type`` yet, sorted by path.

    An image has a mask when its folder in the mask type's folder holds a TIFF named as the
    image's position ID, in any case and with any TIFF extension (the pairing of contract
    §15). Excluded images are not in the run and are never listed.

    Parameters
    ----------
    inputs
        The opened run.
    mask_type
        The mask type, one folder name (``Cells``).
    masks_root
        The masks folder.

    Raises
    ------
    SegmentationError
        For a mask type that is not one folder name, or a masks folder inside the
        experiment or the output folder (or holding them).
    """
    check_mask_type(mask_type)
    _check_folders(inputs, masks_root)
    folders: dict[Path, frozenset[str]] = {}
    found = []
    for fov in inputs.fovs():
        folder = _folder(masks_root, mask_type, fov)
        if folder not in folders:
            folders[folder] = _stems(folder)
        if fov.key[3].casefold() not in folders[folder]:
            found.append(fov)
    return tuple(found)


def segmentation_input(
    inputs: RunInputs, channel_ids: tuple[str, ...] | list[str], fov: Fov, z: int = 0, t: int = 0
) -> np.ndarray:
    """Read the channels ``channel_ids`` of one image as a ``(C, Y, X)`` array of stored values.

    The channels are resolved by ID for the whole run (``Request(channel_ids)``), then each is
    read at the C index **this image** stores it at (``FovSelection.index_of``), so a channel
    that sits at another position in other repeats is still the same stain. The result is in
    the order of ``channel_ids``, with the stored pixel type: nothing is scaled or converted.

    Parameters
    ----------
    inputs
        The opened run.
    channel_ids
        Canonical channel IDs, at least one, each once.
    fov
        The image (one of ``inputs.fovs()``).
    z, t
        The plane; 0 for the only plane of a 2D image.

    Raises
    ------
    SegmentationError
        For no channel, a channel twice, or an image with more than one Z plane or time point.
    RunInputsError
        The resolver's refusal, with its code and message unchanged (``A_SCOPE_INCOMPLETE``
        for a channel some images lack, ``A_RGB_NOT_SUPPORTED``, ...).
    """
    ids = tuple(channel_ids)
    if not ids or len(set(ids)) != len(ids):
        raise SegmentationError("Name at least one channel ID, each once.")
    _check_2d(fov)
    selections = inputs.resolve_request(Request(ids)).require()
    chosen = next((s for s in selections if s.relative_path == fov.relative_path), None)
    if chosen is None:
        raise SegmentationError(f"{fov.relative_path} is not an image of this run.")
    plane = inputs.read_plane(fov, z=z, t=t)
    return np.stack([plane[chosen.index_of(channel_id)] for channel_id in ids])


def mask_path(masks_root: str | os.PathLike, mask_type: str, fov: Fov) -> Path:
    """Return where the mask of ``mask_type`` for ``fov`` is: the layout of contract §15.

    ``<masks>/<type>/<the image's folders>/<position ID>.tif``: the image's folders as they
    are in the experiment, and its name without its extensions (``a.ome.tif`` -> ``a.tif``).

    Raises
    ------
    SegmentationError
        For a mask type that is not one folder name.
    """
    check_mask_type(mask_type)
    return _folder(masks_root, mask_type, fov) / f"{fov.key[3]}.tif"


def write_mask(
    masks_root: str | os.PathLike,
    mask_type: str,
    fov: Fov,
    labels: np.ndarray,
    inputs: RunInputs,
    *,
    overwrite: bool = False,
) -> Path:
    """Write ``labels`` as the mask of ``mask_type`` for ``fov``, as preprocessing expects it.

    The labels must be 2D with the image's Y and X (header only; no pixel is read), of an
    integer or boolean type, with values from 0 to 2,147,483,647. They are written as a
    zlib-compressed ``int32`` TIFF with the axes named ``YX``, to a temporary file and then
    moved into place, so a reader never sees half a mask. Nothing is converted other than the
    integer width and nothing is filled.

    Parameters
    ----------
    masks_root
        The masks folder, outside the experiment and the output folder; created if missing.
    mask_type
        The mask type, one folder name.
    fov
        The image the mask belongs to.
    labels
        The label image, 0 for the background.
    inputs
        The opened run (the image's size, and the experiment and output folders).
    overwrite
        Replace a mask that is already there. Without it an existing mask is refused.

    Returns
    -------
    Path
        The file written.

    Raises
    ------
    SegmentationError
        For labels that are not 2D, not the image's size, floating point or complex, negative
        or too large; an image with more than one Z plane or time point; a mask that exists
        without ``overwrite`` (or another TIFF of the same name); or a masks folder inside the
        experiment or the output folder.
    """
    target = mask_path(masks_root, mask_type, fov)
    _check_folders(inputs, masks_root)
    _check_2d(fov)
    data = _checked_labels(labels, inputs.plane_shape(fov), fov.relative_path)
    others = sorted(n for n in _names(target.parent) if _stem(n) == fov.key[3].casefold())
    if others and (not overwrite or others != [target.name]):
        raise SegmentationError(
            f"The {mask_type} mask of {fov.relative_path} already exists "
            f"({', '.join(others)} in {target.parent}). Use overwrite=True to replace "
            f"{target.name}; a file with another name must be moved away first."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    os.close(handle)
    try:
        tifffile.imwrite(
            temporary, data, photometric="minisblack", compression="zlib", metadata={"axes": "YX"}
        )
        give_output_mode(temporary, target)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return target


def _checked_labels(labels: np.ndarray, shape: tuple[int, int], image: str) -> np.ndarray:
    """Return ``labels`` as an int32 array, or refuse them (see :func:`write_mask`)."""
    array = np.asarray(labels)
    if array.ndim != 2:
        raise SegmentationError(
            f"The labels for {image} have {array.ndim} dimension(s); a mask is 2D (Y, X)."
        )
    if array.shape != tuple(shape):
        raise SegmentationError(
            f"The labels for {image} are {array.shape[0]} x {array.shape[1]} (Y x X) but the "
            f"image is {shape[0]} x {shape[1]}. Segment the image as it is; never resize a mask."
        )
    if array.dtype.kind not in "iub":
        raise SegmentationError(
            f"The labels for {image} are {array.dtype}; a mask holds integer labels (or "
            "boolean). Convert them yourself if they really are whole numbers."
        )
    low, high = int(array.min()), int(array.max())
    if low < 0:
        raise SegmentationError(f"The labels for {image} hold a negative value ({low}).")
    if high > INT32_MAX:
        raise SegmentationError(
            f"The labels for {image} hold {high}, above the limit {INT32_MAX:,} (int32) that "
            "every brick shares."
        )
    return array.astype(np.int32)


def _check_2d(fov: Fov) -> None:
    """Refuse an image with several Z planes or time points (2D only for now)."""
    if fov.z_count > 1 or fov.t_count > 1:
        raise SegmentationError(
            f"{fov.relative_path} has {fov.z_count} Z plane(s) and {fov.t_count} time "
            "point(s). Segmentation here is 2D only: images with Z or T above 1 are not "
            "supported yet."
        )


def _check_folders(inputs: RunInputs, masks_root: str | os.PathLike) -> None:
    """Refuse a masks folder inside the experiment or the output folder (or holding them)."""
    issues = masks_folder_issues(masks_root, inputs.experiment_root, inputs.output_folder)
    if not issues and relative_inside(Path(masks_root).resolve(), inputs.output_folder):
        raise SegmentationError(
            f"The masks folder {masks_root} is inside the output folder of the run. Choose a "
            "masks folder next to the experiment and output folders."
        )
    if issues:
        issue = issues[0]
        raise SegmentationError(f"{issue.message} {issue.action} ({issue.code})")


def _folder(masks_root: str | os.PathLike, mask_type: str, fov: Fov) -> Path:
    """Return the folder of ``fov``'s mask: the type's folder plus the image's own folders."""
    return Path(masks_root, mask_type, *Path(fov.relative_path).parent.parts)


def _names(folder: Path) -> list[str]:
    """Return the names of the files in ``folder`` (none if it does not exist)."""
    try:
        return [e.name for e in os.scandir(folder) if e.is_file()]
    except FileNotFoundError:
        return []


def _stem(name: str) -> str | None:
    """Return a TIFF file's name without its extension, lower case; None for other files."""
    lower = name.casefold()
    for suffix in _SUFFIXES:
        if lower.endswith(suffix):
            return lower[: -len(suffix)]
    return None


def _stems(folder: Path) -> frozenset[str]:
    """Return the lower-case stems of the TIFF files in ``folder``."""
    return frozenset(s for s in map(_stem, _names(folder)) if s is not None)
