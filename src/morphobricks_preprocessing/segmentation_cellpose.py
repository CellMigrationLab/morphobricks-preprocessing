"""Cellpose glue for the segmentation hand-off: nothing asked for is silently replaced.

The shipped Cellpose notebook (``notebooks/MorphoBricks_Cellpose.ipynb``) segments the images of a
described run with Cellpose and writes the masks with :func:`.segmentation_handoff.write_mask`.
Cellpose is **not** a dependency of this brick and is imported only inside functions
(``cellpose``, ``torch``, ``dinov3``), so importing this module, and discovery, stay inert. The
pinned release is :data:`CELLPOSE_VERSION` (4.2.1.1); another version is refused.

Loading a model (:func:`load_model`, in :mod:`.segmentation_cellpose_model`, re-exported here)
refuses what Cellpose 4.2.1.1 would do silently: substitute ``cpsam_v2`` for a model it cannot
find, fall back from the GPU to the CPU, and download uncached weights. This module adds the
rest of what an audit of the Cellpose hand-off asked for:

- **Channels by ID.** The channels are resolved by canonical ID
  (:func:`.segmentation_handoff.resolve_segmentation`) and passed as a ``(C, Y, X)`` stack with
  ``channel_axis=0``. Cellpose reads only the **first three** channels and drops the rest by
  position, so more than three are refused (:func:`check_channel_count`).
- **Diameter.** Blank means the model's native scale: Cellpose 4.2.1.1 does **not** estimate a
  diameter. A diameter is in pixels and never 0 (:class:`Settings`).
- **Device behaviour of Cellpose.** On a GPU, Cellpose may move the flow-threshold quality step
  to the CPU when GPU memory is short and always computes the masks from the flows on the CPU
  on ``mps``. That is Cellpose's own behaviour, not refused; it is recorded as a note.
- **Record.** Every effective setting goes into the batch's parameters
  (:func:`inference_parameters`), every image into the batch with its outcome, written, skipped
  or failed with the error (:func:`run_batch`); nothing is dropped. Each mask's hash and record
  entry are written under the type's write lock; a failure there rolls the mask back. The full
  normalisation, the flow iterations, and whether built-in weights were downloaded (and from
  where) are in it.

Mask paths, pairing, the mask-type rules, writing, the folder rules and the record are the
hand-off's own (:mod:`.segmentation_handoff`, :mod:`.segmentation_record`); nothing is repeated.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ._io import file_sha256
from .manifest import MANIFEST_FILENAME
from .run_inputs import Fov, RunInputs
from .segmentation_cellpose_commit import check_newest, is_recorded, replaced_earlier_mask
from .segmentation_cellpose_model import (
    BUILTIN_MODELS,
    CELLPOSE_VERSION,
    DEFAULT_MODEL,
    DEVICE_NOTE,
    DINO_MODELS,
    DINOV3_COMMIT,
    DINOV3_URL,
    LoadedModel,
    ModelChoice,
    check_device,
    check_dinov3,
    choose_model,
    default_normalization,
    device_status,
    dinov3_install_command,
    install_dinov3,
    installed_dinov3_commit,
    installed_version,
    load_model,
    weights_file,
)
from .segmentation_cellpose_run import channel_coverage, next_steps_lines, open_run
from .segmentation_cellpose_settings import (
    CELL_OUTPUT,
    CELL_ROLE,
    FLOW_ITERATIONS,
    NO_CHANNEL,
    NORMALIZE_REFUSAL,
    NUCLEAR_ROLE,
    NUCLEI_OUTPUT,
    OUTPUT_MASK_TYPES,
    Settings,
    channel_roles_entry,
    diameter_from_text,
    flow_iterations,
    input_channels,
    rescale_factor,
)
from .segmentation_handoff import (
    SegmentationPlan,
    images_to_segment,
    mask_path,
    stray_backups,
    write_mask,
)
from .segmentation_record import (
    SegmentationBatch,
    SegmentationError,
    SegmentationRecord,
    SegmentedImage,
    mask_sha256,
    new_batch_id,
    read_record,
    record_path,
    write_record,
)

__all__ = [
    "channel_coverage",
    "next_steps_lines",
    "open_run",
    "BUILTIN_MODELS",
    "CELLPOSE_VERSION",
    "DEFAULT_MODEL",
    "DINOV3_COMMIT",
    "DINOV3_URL",
    "DINO_MODELS",
    "CELL_OUTPUT",
    "CELL_ROLE",
    "FLOW_ITERATIONS",
    "NO_CHANNEL",
    "NORMALIZE_REFUSAL",
    "NUCLEAR_ROLE",
    "NUCLEI_OUTPUT",
    "OUTPUT_MASK_TYPES",
    "LoadedModel",
    "ModelChoice",
    "Settings",
    "channel_roles_entry",
    "check_device",
    "check_dinov3",
    "choose_model",
    "default_normalization",
    "device_status",
    "diameter_from_text",
    "dinov3_install_command",
    "input_channels",
    "install_dinov3",
    "installed_dinov3_commit",
    "installed_version",
    "load_model",
    "weights_file",
]

TOOL = "cellpose"
MAX_CHANNELS = 3
"""Cellpose 4.2.1.1 reads at most three channels and drops the rest silently."""
TILE_OVERLAP, MIN_SIZE, MAX_SIZE_FRACTION = 0.1, 15, 0.4
"""Cellpose's own defaults, passed explicitly so that the record holds the effective values."""
NORMALIZE_PERCENTILE = [1.0, 99.0]
NATIVE_SCALE = (
    "native scale of the model: Cellpose 4.2.1.1 does not estimate the diameter, so a blank "
    "diameter means the model's own scale"
)
IMAGE_SKIPPED = "a mask of this type is already there; not replaced (overwrite is off)"
IMAGE_UNRECORDED = (
    "exists without provenance (not made by a recorded batch); not replaced (overwrite is off)"
)
NEEDS_ATTENTION = "needs_attention"
"""The table's outcome for an existing mask that is not shown to be its latest recorded one
(recorded ``skipped`` with the reason: the record's statuses are the hand-off's)."""
ROLLED_BACK_RESTORED = "rolled back: earlier mask restored"
ROLLED_BACK_REMOVED = "rolled back: the new mask was removed"


# --- one image --------------------------------------------------------------------------------


def normalization(settings: Settings) -> dict[str, Any]:
    """Return the full normalisation given to Cellpose: its defaults now, with the user's values.

    The defaults (``tile_norm_blocksize``, ``sharpen_radius``, ``invert``, ``lowhigh``, ...) come
    from Cellpose's module global at call time, copied; ``normalize`` (always on: a setting
    that is not True is refused) and the percentiles are then set. Nothing is left to Cellpose's
    defaults, so the record holds all.
    """
    if settings.normalize is not True:  # Settings refuses it; never reach Cellpose without it
        raise SegmentationError(NORMALIZE_REFUSAL)
    return {
        **default_normalization(),
        "normalize": True,
        "percentile": list(NORMALIZE_PERCENTILE),
    }


def eval_arguments(loaded: LoadedModel, settings: Settings) -> dict[str, Any]:
    """Return every argument given to ``model.eval``, the effective inference settings.

    ``channel_axis`` is 0: the stack from :meth:`.segmentation_handoff.SegmentationPlan.read`
    is ``(C, Y, X)`` in the order of the requested channel IDs. The tile size, the full
    normalisation, the flow iterations and the other values Cellpose would default are given
    explicitly, so what is recorded is what ran.
    """
    return {
        "batch_size": settings.batch_size,
        "resample": True,
        "channel_axis": 0,
        "normalize": normalization(settings),
        "diameter": settings.diameter,
        "flow_threshold": settings.flow_threshold,
        "cellprob_threshold": settings.cellprob_threshold,
        "do_3D": False,
        "min_size": MIN_SIZE,
        "max_size_fraction": MAX_SIZE_FRACTION,
        "niter": flow_iterations(settings),
        "augment": False,
        "tile_overlap": TILE_OVERLAP,
        "bsize": loaded.tile_size,
    }


def check_channel_count(channel_ids: Sequence[str]) -> None:
    """Refuse more channels than Cellpose reads (it would silently use the first three)."""
    if len(channel_ids) > MAX_CHANNELS:
        raise SegmentationError(
            f"Cellpose {CELLPOSE_VERSION} reads at most {MAX_CHANNELS} channels and would "
            f"silently drop the others by position; {len(channel_ids)} were chosen "
            f"({', '.join(channel_ids)}). Choose {MAX_CHANNELS} or fewer."
        )


def segment_image(loaded: LoadedModel, settings: Settings, stack: np.ndarray) -> np.ndarray:
    """Segment one ``(C, Y, X)`` stack (the chosen channels, in the order chosen).

    Returns
    -------
    numpy.ndarray
        The 2D integer label image Cellpose returns (0 is background); it is not changed.

    Raises
    ------
    SegmentationError
        For a stack that is not ``(C, Y, X)`` with 1 to 3 channels, or a result that is not a
        2D label image.
    """
    array = np.asarray(stack)
    if array.ndim != 3:
        raise SegmentationError(f"The stack is {array.ndim}D; Cellpose gets (C, Y, X).")
    check_channel_count([f"channel {i}" for i in range(array.shape[0])])
    out = loaded.model.eval(array, **eval_arguments(loaded, settings))
    labels = np.asarray(out[0] if isinstance(out, tuple) else out)
    if labels.ndim != 2:
        raise SegmentationError(f"Cellpose returned {labels.ndim}D labels; a mask is 2D (Y, X).")
    return labels


def inference_parameters(
    loaded: LoadedModel, settings: Settings, channel_ids: Sequence[str]
) -> dict[str, Any]:
    """Return the parameters of a batch for the record: every effective setting, plain JSON.

    The diameter is in pixels, or null with the statement that it is the model's native
    scale; ``rescale`` is the factor Cellpose derives from it (30 / diameter, or 1). The
    normalisation is the full dictionary given to Cellpose and ``niter`` the flow iterations
    it derives from the rescale. ``weights_downloaded`` and ``weights_source`` say whether this
    session downloaded the built-in weights and from where; ``notes`` holds Cellpose's device
    behaviour (:data:`.segmentation_cellpose_model.DEVICE_NOTE`) for a GPU run.
    """
    arguments = eval_arguments(loaded, settings)
    notes = [DEVICE_NOTE] if loaded.device != "cpu" else []
    return {
        **arguments,
        "diameter_unit": "pixels" if settings.diameter is not None else NATIVE_SCALE,
        "rescale": rescale_factor(settings),
        "channel_ids_order": list(channel_ids),
        "device_type": loaded.device,
        "backbone": loaded.backbone,
        "use_bfloat16": True,
        "cellpose_version": CELLPOSE_VERSION,
        "dinov3_commit": loaded.dinov3_commit,
        "weights_downloaded": loaded.downloaded,
        "weights_source": loaded.source,
        "notes": notes,
    }


# --- the batch --------------------------------------------------------------------------------


@dataclass(frozen=True)
class SegmentationJob:
    """Everything one batch needs: the run, the channels, the model, the settings and the target.

    Attributes
    ----------
    inputs, plan
        The opened run and its channels resolved once (``resolve_segmentation``).
    loaded, settings
        The verified model and the inference settings.
    mask_type, masks_root
        The mask type and the masks folder.
    overwrite
        Replace masks that are already there (default: skip them).
    channel_roles, output
        The role of each of ``plan.channel_ids`` and the output (``cells`` or ``nuclei``),
        recorded with them; ``()`` and None record none (null).
    """

    inputs: RunInputs
    plan: SegmentationPlan
    loaded: LoadedModel
    settings: Settings
    mask_type: str
    masks_root: Path
    overwrite: bool = False
    channel_roles: tuple[str, ...] = ()
    output: str | None = None

    def __post_init__(self) -> None:
        """Refuse a job without resolved channels or with roles that do not fit them."""
        if self.plan is None:
            raise SegmentationError("No channels are resolved: choose channels the run has.")
        channel_roles_entry(self.plan.channel_ids, self.channel_roles, self.output)


@dataclass(frozen=True)
class BatchResult:
    """What a batch did: its record entry and where the record was written.

    Attributes
    ----------
    batch
        The batch as recorded (one entry per image handled).
    record
        The record file, or None when no image was handled.
    stopped
        Why the batch stopped early (a mask that could not be hashed or recorded was rolled
        back), or an empty string when every image was handled.
    attention
        The images whose existing mask was not replaced and is not shown to be its latest
        recorded one (no entry, another SHA-256, not checked): ``needs_attention`` in the table.
    """

    batch: SegmentationBatch
    record: Path | None
    stopped: str = ""
    attention: frozenset[str] = frozenset()

    def rows(self) -> tuple[tuple[str, str, str], ...]:
        """Return ``(image, status, message)`` for every image: nothing is left out."""
        return tuple(
            (i.image, NEEDS_ATTENTION if i.image in self.attention else i.status, i.message)
            for i in self.batch.images
        )

    def counts(self) -> dict[str, int]:
        """Return how many images were written, skipped (verified), need attention and failed."""
        statuses = [status for _, status, _ in self.rows()]
        return {s: statuses.count(s) for s in ("written", "skipped", NEEDS_ATTENTION, "failed")}


def non_2d_images(inputs: RunInputs) -> tuple[str, ...]:
    """Return the images with more than one Z plane or time point (refused: 2D only for now)."""
    return tuple(f.relative_path for f in inputs.fovs() if f.z_count > 1 or f.t_count > 1)


def _segment_one(
    job: SegmentationJob, fov: Fov, batch_id: str, entries: list[SegmentedImage]
) -> tuple[SegmentedImage, str]:
    """Segment, write, hash and record one image; return its entry and why to stop (or "").

    The SHA-256 and the record write (the batch so far, with this image) happen in
    ``write_mask``'s ``on_published``, under the type's write lock (lock order: the write lock,
    then the record's lock inside :func:`.segmentation_record.write_record`; never the reverse),
    so no other writer can publish this type between the mask and its record entry. If hashing
    or recording fails, even by an interrupt (``KeyboardInterrupt``: the notebook's stop button),
    ``write_mask`` rolls the write back under that lock (an overwritten mask restored, a new one
    removed) and the batch stops; an interrupt is raised again. A failure before anything was
    published is a ``failed`` entry and the batch goes on.
    """
    image = fov.relative_path
    state: dict[str, Any] = {}
    root, kind = job.masks_root, job.mask_type
    strays = frozenset(stray_backups(root, kind))

    def commit(path: Path) -> None:  # under the type's write lock, right after the publish
        try:
            entry = SegmentedImage(image, "written", mask_sha256=mask_sha256(path))
            state["replaced"] = replaced_earlier_mask(root, kind, strays)  # exact, under the lock
            check_newest(root, kind, batch_id, image)
            state["expected"] = _batch(job, batch_id, [*entries, entry])
            _write_batch(job, batch_id, [*entries, entry], state["expected"])
            state["entry"] = entry
        except BaseException as exc:
            expected = state.get("expected")
            if expected is not None and is_recorded(root, kind, expected):
                # write_record saved the file and then failed: mask and record agree (the whole
                # provenance of this batch, not just its ID), keep both
                state["entry"], state["late"] = expected.images[-1], exc
                return
            state["error"] = exc
            raise

    try:
        labels = segment_image(job.loaded, job.settings, job.plan.read(fov))
        write_mask(
            job.masks_root,
            job.mask_type,
            fov,
            labels,
            job.inputs,
            overwrite=job.overwrite,
            on_published=commit,
        )
    except BaseException as exc:
        if "entry" in state:  # written and recorded; only the earlier mask's copy is left over
            return _after_commit(entries, state["entry"], exc)
        if "error" not in state:  # nothing was published
            if isinstance(exc, Exception):  # reported in the table, never dropped
                return SegmentedImage(image, "failed", f"{type(exc).__name__}: {exc}"), ""
            raise
        error = state["error"]
        if exc is error:  # write_mask rolled back
            note = ROLLED_BACK_RESTORED if state.get("replaced") else ROLLED_BACK_REMOVED
        else:  # the rollback failed: its error names the files
            note = f"WARNING: the rollback failed and the mask has NO provenance: {exc}"
        failed = SegmentedImage(image, "failed", f"{type(error).__name__}: {error}. {note}")
        if not isinstance(error, Exception):
            raise
        return failed, failed.message
    if "late" in state:  # the record was saved, then something failed: stop, keep the mask
        return _after_commit(entries, state["entry"], state["late"])
    return state["entry"], ""


def _after_commit(
    entries: list[SegmentedImage], entry: SegmentedImage, exc: BaseException
) -> tuple[SegmentedImage, str]:
    """Stop the batch after a failure that came once ``entry`` was recorded and its mask kept.

    An error is reported in the table and stops the batch. An interrupt (``KeyboardInterrupt``)
    is raised again, with the image in ``entries`` first so the final record keeps its entry.
    """
    if isinstance(exc, Exception):
        return entry, f"{type(exc).__name__}: {exc}"
    entries.append(entry)
    raise exc


def _batch(job: SegmentationJob, batch_id: str, images: list[SegmentedImage]) -> SegmentationBatch:
    """Return the record entry of the batch as it stands."""
    loaded = job.loaded
    return SegmentationBatch(
        batch_id=batch_id,
        tool=TOOL,
        tool_version=CELLPOSE_VERSION,
        model_name=loaded.name,
        model_sha256=loaded.sha256,
        model_file=loaded.file,
        parameters={
            **inference_parameters(loaded, job.settings, job.plan.channel_ids),
            "channel_roles": channel_roles_entry(
                job.plan.channel_ids, job.channel_roles, job.output
            ),
            "output": job.output,
        },
        channel_ids=job.plan.channel_ids,
        device=loaded.device,
        run_folder=os.fspath(job.inputs.output_folder),
        run_manifest_sha256=file_sha256(job.inputs.output_folder / MANIFEST_FILENAME),
        images=tuple(images),
    )


def _write_batch(
    job: SegmentationJob,
    batch_id: str,
    entries: list[SegmentedImage],
    batch: SegmentationBatch | None = None,
) -> tuple[SegmentationBatch, Path | None]:
    """Write the record of the batch (built here unless given; none when no image was handled)."""
    batch = batch if batch is not None else _batch(job, batch_id, entries)
    return batch, write_record(job.masks_root, job.mask_type, batch) if entries else None


def _skip_note(
    job: SegmentationJob, fov: Fov, record: SegmentationRecord | None
) -> tuple[bool, str]:
    """Say whether the existing mask of ``fov`` is its latest recorded one, and why not."""
    latest = record.latest(fov.relative_path) if record is not None else None
    if latest is None:
        return False, IMAGE_UNRECORDED
    path = mask_path(job.masks_root, job.mask_type, fov)
    if not path.is_file():  # a twin elsewhere pairs: the record says nothing about it
        return False, f"a mask of this image is paired, but not at {path}; not checked"
    try:
        found = mask_sha256(path)
    except OSError as exc:
        return False, f"exists, but could not be checked against its recorded provenance ({exc})"
    expected = latest[1].mask_sha256
    if found != expected:
        return False, (
            f"exists, but differs from its recorded provenance (sha256 {found}, "
            f"recorded {expected})"
        )
    return True, IMAGE_SKIPPED


def run_batch(
    job: SegmentationJob,
    fovs: Sequence[Fov] | None = None,
    *,
    progress: Callable[[int, int, str], None] | None = None,
    batch_id: str | None = None,
) -> BatchResult:
    """Segment images one at a time, write each mask and record every outcome.

    Each mask is hashed and recorded (the batch so far) right after it is published, under the
    type's write lock, before the next image starts. If that fails the write is rolled back
    under the same lock (an overwritten mask restored byte for byte, a new one removed), the
    image is ``failed`` with the error and what the rollback did, the images not yet started
    are ``failed`` as not started, and the batch **stops** (``BatchResult.stopped`` says why):
    a mask is never left without its record entry. Images that already have a mask of the type
    are not replaced unless ``job.overwrite``: ``skipped`` when the file's SHA-256 is that of
    its latest recorded entry, else ``needs_attention`` in the table (recorded ``skipped`` with
    the reason: no provenance, another SHA-256, or not checked).
    Any other image that fails is ``failed`` with the error and the batch goes on. The record is
    written even when the batch is interrupted, with the images done so far.

    Parameters
    ----------
    job
        The batch (:class:`SegmentationJob`).
    fovs
        The images to handle; by default all the run's images.
    progress
        Called as ``progress(done, total, image)`` before each image and once at the end.
    batch_id
        The record's batch ID; by default :func:`.segmentation_record.new_batch_id`.

    Raises
    ------
    SegmentationError
        For a masks folder or mask type the hand-off refuses, or a record that is not valid.
        A record that cannot be written at the end is raised too (every mask is recorded by then).
    """
    chosen = tuple(job.inputs.fovs() if fovs is None else fovs)
    missing = {
        f.relative_path for f in images_to_segment(job.inputs, job.mask_type, job.masks_root)
    }
    record = (
        read_record(job.masks_root, job.mask_type)
        if record_path(job.masks_root, job.mask_type).exists()
        else None
    )
    started = batch_id or new_batch_id()
    entries: list[SegmentedImage] = []
    attention: set[str] = set()
    stopped = ""
    try:
        for done, fov in enumerate(chosen):
            if progress is not None:
                progress(done, len(chosen), fov.relative_path)
            image = fov.relative_path
            if not (job.overwrite or image in missing):
                verified, note = _skip_note(job, fov, record)
                entries.append(SegmentedImage(image, "skipped", note))
                if not verified:
                    attention.add(image)
            elif stopped:
                entries.append(SegmentedImage(image, "failed", f"not started: {stopped}"))
            else:
                entry, stop = _segment_one(job, fov, started, entries)
                entries.append(entry)
                if stop:
                    stopped = f"the batch stopped at {image}: {stop}"
        if progress is not None:
            progress(len(chosen), len(chosen), "")
    except BaseException as interrupted:
        # The record of what was done is still written; if that fails too, the first error is
        # the one raised (the second is attached to it), never one that hides it.
        try:
            _write_batch(job, started, entries)
        except Exception as second:
            interrupted.add_note(f"The record could not be written either: {second}")
        raise
    try:
        batch, record = _write_batch(job, started, entries)
    except Exception as exc:
        if not stopped:
            raise
        stopped += f" The record could not be written either: {exc}"
        return BatchResult(_batch(job, started, entries), None, stopped, frozenset(attention))
    return BatchResult(batch, record, stopped, frozenset(attention))
