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
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tifffile

from ._io import give_output_mode
from .discovery import discover_experiment
from .folder_rules import masks_folder_issues
from .links import relative_inside
from .masks import FOLDER
from .masks.files import (
    _description_name_issues,
    _duplicate_issues,
    _folder_masks,
    _label_masks,
    _name_issues,
)
from .masks.model import MaskFile
from .metadata import OME_ZARR, TiffMetadata
from .run_inputs import Fov, RunInputs
from .run_inputs_request import FovSelection, Request
from .segmentation_record import SegmentationError, check_mask_type, hold_lock
from .zarr_input import is_zarr_name

WRITE_LOCK_FILENAME = ".segmentation_write.lock"
"""The lock a writer holds in a type folder from the twin check to the published mask. A
dot-file, so the run's masks discovery skips it like the record's lock."""

INT32_MAX = 2_147_483_647
"""The largest label every brick accepts (contract §15, §16)."""


def images_to_segment(
    inputs: RunInputs, mask_type: str, masks_root: str | os.PathLike
) -> tuple[Fov, ...]:
    """Return the run's images that have no mask of ``mask_type`` yet, sorted by path.

    "Has a mask" is decided by preprocessing's own pairing: the masks folder
    (:func:`.masks.files._folder_masks`) and the label images inside OME-Zarr images
    (:func:`.masks.files._label_masks`), the sources the run and the mask step use, so this
    list and the next run can never disagree: either layout of contract §15, any case in the
    folders, the name and the type, any TIFF extension. An OME-Zarr image with a label image of
    the type has its mask and is not listed. Excluded images are not in the run and are never
    listed.

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
        For a mask type that is not one folder name, a masks folder inside the experiment or
        the output folder (or holding them), or a masks folder the run itself would refuse
        (an ambiguous mask, a link leaving the folder).
    """
    check_mask_type(mask_type)
    _check_folders(inputs, masks_root)
    made = {m.image for m in _paired(inputs, masks_root, mask_type)}
    return tuple(fov for fov in inputs.fovs() if fov.relative_path not in made)


@dataclass(frozen=True)
class SegmentationPlan:
    """The channels of one request resolved once for the whole run, read image by image.

    Attributes
    ----------
    inputs
        The opened run.
    channel_ids
        The canonical channel IDs, in the order they are returned.
    selections
        The resolver's selection of each image, by its path relative to the experiment.
    """

    inputs: RunInputs
    channel_ids: tuple[str, ...]
    selections: dict[str, FovSelection]

    def read(self, fov: Fov, z: int = 0, t: int = 0) -> np.ndarray:
        """Read the planned channels of one image as a ``(C, Y, X)`` array of stored values.

        Each channel is read at the C index this image stores it at
        (``FovSelection.index_of``), in the order of :attr:`channel_ids`; nothing is scaled
        or converted.

        Raises
        ------
        SegmentationError
            For an image with more than one Z plane or time point, or one not in the run.
        """
        _check_2d(fov)
        chosen = self.selections.get(fov.relative_path)
        if chosen is None:
            raise SegmentationError(f"{fov.relative_path} is not an image of this run.")
        plane = self.inputs.read_plane(fov, z=z, t=t)
        return np.stack([plane[chosen.index_of(channel_id)] for channel_id in self.channel_ids])


def resolve_segmentation(
    inputs: RunInputs, channel_ids: tuple[str, ...] | list[str]
) -> SegmentationPlan:
    """Resolve ``channel_ids`` by ID for the whole run once; read each image with ``plan.read``.

    Resolving costs a pass over the run's manifest, so a batch resolves once and reads many
    images (:func:`segmentation_input` resolves on every call).

    Raises
    ------
    SegmentationError
        For no channel, or a channel twice.
    RunInputsError
        The resolver's refusal, with its code and message unchanged (``A_SCOPE_INCOMPLETE``
        for a channel some images lack, ``A_RGB_NOT_SUPPORTED``, ...).
    """
    ids = tuple(channel_ids)
    if not ids or len(set(ids)) != len(ids):
        raise SegmentationError("Name at least one channel ID, each once.")
    selections = inputs.resolve_request(Request(ids)).require()
    return SegmentationPlan(inputs, ids, {s.relative_path: s for s in selections})


def segmentation_input(
    inputs: RunInputs, channel_ids: tuple[str, ...] | list[str], fov: Fov, z: int = 0, t: int = 0
) -> np.ndarray:
    """Read the channels ``channel_ids`` of one image as a ``(C, Y, X)`` array of stored values.

    The channels are resolved by ID for the whole run (``Request(channel_ids)``), then each is
    read at the C index **this image** stores it at (``FovSelection.index_of``), so a channel
    that sits at another position in other repeats is still the same stain. The result is in
    the order of ``channel_ids``, with the stored pixel type: nothing is scaled or converted.
    It resolves on every call: a batch calls :func:`resolve_segmentation` once and reads each
    image with ``plan.read``.

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
    _check_2d(fov)
    return resolve_segmentation(inputs, channel_ids).read(fov, z=z, t=t)


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
    integer width and nothing is filled. One writer at a time works on a mask type: its write
    lock is held from the check for another mask of the image to the published file, in both
    modes, so an overwriting writer and a default one can never replace each other's mask.

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
        without ``overwrite`` (or another TIFF of the same name); a masks folder inside the
        experiment or the output folder; or another writer holding the type's write lock
        (``<masks>/<type>/.segmentation_write.lock``), which is refused at once and names the
        holder.
    """
    target = mask_path(masks_root, mask_type, fov)
    _check_folders(inputs, masks_root)
    _check_2d(fov)
    data = _checked_labels(labels, inputs.plane_shape(fov), fov.relative_path)
    type_folder = Path(masks_root) / mask_type
    with (
        _no_empty_folders_if_refused(type_folder, target.parent),
        hold_lock(type_folder / WRITE_LOCK_FILENAME, f"The {mask_type} masks in {masks_root}"),
    ):  # one writer per type for the twin check, the claim and the publish, in both modes
        wanted = target.relative_to(masks_root).as_posix()
        twins = sorted(
            m.path for m in _paired(inputs, masks_root, mask_type) if m.image == fov.relative_path
        )
        if twins and (not overwrite or twins != [wanted]):
            raise SegmentationError(
                f"The {mask_type} mask of {fov.relative_path} already exists "
                f"({', '.join(twins)} in {masks_root}). Use overwrite=True to replace {wanted}; "
                "a mask of this image and type in another place or case, or in the other layout, "
                "must be moved away first (the next run would refuse both as duplicate_mask)."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        os.close(handle)
        try:
            tifffile.imwrite(
                temporary,
                data,
                photometric="minisblack",
                compression="zlib",
                metadata={"axes": "YX"},
            )
            give_output_mode(temporary, target)
            _publish(
                Path(temporary), target, overwrite, f"The {mask_type} mask of {fov.relative_path}"
            )
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
    return target


@contextmanager
def _no_empty_folders_if_refused(type_folder: Path, leaf: Path) -> Iterator[None]:
    """Remove the folders this call created (``type_folder`` down to ``leaf``) if it is refused.

    A refused write leaves nothing behind, not even the empty folders the lock needed.
    """
    created = None  # the topmost folder this call has to make
    for folder in (type_folder, *type_folder.parents):
        if folder.exists():
            break
        created = folder
    try:
        yield
    except SegmentationError:
        folder = leaf
        while created is not None and (folder == created or created in folder.parents):
            try:
                if folder.exists():  # a folder not made yet (refused before it) is skipped
                    folder.rmdir()
            except OSError:
                break  # not empty: something else is in it
            folder = folder.parent
        raise


def _publish(temporary: Path, target: Path, overwrite: bool, what: str) -> None:
    """Move the finished ``temporary`` file to ``target``; never over a file unless asked.

    Without ``overwrite`` the name is first **claimed** with an exclusive create
    (``O_CREAT | O_EXCL``, one step that fails if anything is there, a link included), then
    the finished file is moved over our own empty placeholder. Two writers cannot both claim
    the name, so neither replaces the other's mask, and this needs no hard links (a Google
    Drive mount has none). A reader never sees half a mask: the real file arrives in one
    move. For that instant the name holds an empty file; if the move fails the placeholder is
    removed again (only while it is still empty, so another writer's file is never touched).
    A process killed between the claim and the move leaves the empty file, which the next
    write refuses as an existing mask: delete it, then write again.
    """
    if overwrite:
        os.replace(temporary, target)
        return
    try:
        claimed = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
    except FileExistsError:
        raise SegmentationError(
            f"{what} appeared at {target} while it was being written; nothing was replaced. "
            "Use overwrite=True to replace it."
        ) from None
    try:
        os.close(claimed)
        os.replace(temporary, target)
    except BaseException:
        try:
            if target.stat().st_size == 0:  # still our placeholder, not another writer's mask
                target.unlink()
        except OSError:
            pass
        raise


def _paired(inputs: RunInputs, masks_root: str | os.PathLike, mask_type: str) -> list[MaskFile]:
    """Return the masks of ``mask_type`` that preprocessing's pairing finds, of any source.

    Built from the discovery the run uses: the TIFF masks of the masks folder
    (:func:`.masks.files._folder_masks`) and the label images of the OME-Zarr images
    (:func:`.masks.files._label_masks`), then the run's own checks on them
    (:func:`.masks.files._name_issues`, :func:`.masks.files._description_name_issues`,
    :func:`.masks.files._duplicate_issues`), so the rules are the run's and exist once. Refused
    here as the run refuses them: an ambiguous mask or a link leaving the folder (any error of
    the pairing), a label group that cannot be read, any discovered mask type that cannot be a
    ``source_type`` (``R_MASK_TYPE_NAME_INVALID``, a decomposed name), the type spelled in two
    ways (``mask_names_differ_in_case``, ``mask_type`` itself counted) and an image with two
    masks of one type (``duplicate_mask``), whichever the two sources.

    Raises
    ------
    SegmentationError
        For any of those, naming the files.
    """
    discovery = discover_experiment(inputs.experiment_root, inputs.output_folder)
    paths = {entry.relative_path for entry in inputs.files}
    found: list[MaskFile] = []
    issues = []
    folder = Path(masks_root)
    if folder.is_dir():
        found, issues = _folder_masks(folder.resolve(), discovery, paths)
    zarr_images = {
        path: TiffMetadata(relative_path=path, readable=True, kind=OME_ZARR)
        for path in paths
        if is_zarr_name(path.rsplit("/", 1)[-1])
    }
    labels, unreadable = _label_masks(discovery.root, zarr_images)
    found = [*found, *labels]
    if unreadable:
        raise SegmentationError(
            f"The label images of {', '.join(sorted(unreadable))} cannot be read; the run "
            "reports them as unreadable_mask. Repair or remove them first."
        )
    asked = MaskFile("", mask_type, mask_type, FOLDER)  # the spelling being asked for counts too
    issues = [
        *issues,
        *_name_issues([*found, asked]),
        *_description_name_issues(found),
        *_duplicate_issues(found),
    ]
    refusing = [issue for issue in issues if issue.is_error]
    if refusing:
        first = refusing[0]
        named = f" ({', '.join(first.paths)})" if first.paths else ""
        raise SegmentationError(f"{first.message}{named} {first.action} ({first.code})")
    return [m for m in found if m.mask_type == mask_type]


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
