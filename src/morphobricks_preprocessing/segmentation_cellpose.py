"""Cellpose glue for the segmentation hand-off: nothing asked for is silently replaced.

The shipped Cellpose notebook (``notebooks/MorphoBricks_Cellpose.ipynb``) segments the images of a
described run with Cellpose and writes the masks with :func:`.segmentation_handoff.write_mask`.
Cellpose is **not** a dependency of this brick and is imported only inside functions
(``cellpose``, ``torch``, ``dinov3``), so importing this module, and discovery, stay inert. The
pinned release is :data:`CELLPOSE_VERSION` (4.2.1.1); another version is refused.

Loading a model (:func:`load_model`, in :mod:`.segmentation_cellpose_model`, re-exported here)
refuses what Cellpose 4.2.1.1 would do silently: substitute ``cpsam_v2`` for a model it cannot
find and fall back from the GPU to the CPU; built-in weights are downloaded on first use from a
pinned revision and checked against their pinned SHA-256
(:mod:`.segmentation_cellpose_weights`), never by Cellpose. This module adds the rest of what
an audit of the Cellpose hand-off asked for:

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
  normalisation, the flow iterations, and the built-in weights' source (repository, revision
  and URL) and whether they were downloaded are in it.
- **One torch warning hidden** while Cellpose runs (:func:`evaluate`,
  :func:`.segmentation_cellpose_model.sparse_warning_hidden`); it does not change any result.

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

from .run_inputs import Fov, RunInputs
from .segmentation_cellpose_commit import (
    after_commit,
    check_newest,
    is_recorded,
    replaced_earlier_mask,
)
from .segmentation_cellpose_eval import (
    MAX_SIZE_FRACTION,
    MIN_SIZE,
    NATIVE_SCALE,
    NORMALIZE_PERCENTILE,
    TILE_OVERLAP,
    eval_arguments,
    inference_parameters,
    normalization,
)
from .segmentation_cellpose_model import (
    BUILTIN_MODELS,
    CELLPOSE_VERSION,
    DEFAULT_MODEL,
    DEVICE_NOTE,
    DINO_MODELS,
    DINOV3_COMMIT,
    DINOV3_LABEL,
    DINOV3_URL,
    SPARSE_WARNING,
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
    sparse_warning_hidden,
    weights_file,
)
from .segmentation_cellpose_run import (
    MAX_CHANNELS,
    channel_coverage,
    check_channel_count,
    check_manifest,
    next_step_line,
    non_2d_images,
    open_run,
    pinned_manifest,
)
from .segmentation_cellpose_settings import (
    BOTH_NEEDS,
    BOTH_OUTPUT,
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
    output_inputs,
    rescale_factor,
)
from .segmentation_cellpose_weights import WEIGHTS_REPOSITORY, WEIGHTS_REVISION, weights_url
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
    "DEVICE_NOTE",
    "MAX_SIZE_FRACTION",
    "MIN_SIZE",
    "NATIVE_SCALE",
    "NORMALIZE_PERCENTILE",
    "TILE_OVERLAP",
    "eval_arguments",
    "flow_iterations",
    "inference_parameters",
    "normalization",
    "rescale_factor",
    "MAX_CHANNELS",
    "channel_coverage",
    "check_channel_count",
    "non_2d_images",
    "next_step_line",
    "open_run",
    "BUILTIN_MODELS",
    "CELLPOSE_VERSION",
    "DEFAULT_MODEL",
    "DINOV3_COMMIT",
    "DINOV3_LABEL",
    "DINOV3_URL",
    "DINO_MODELS",
    "BOTH_NEEDS",
    "BOTH_OUTPUT",
    "CELL_OUTPUT",
    "CELL_ROLE",
    "FLOW_ITERATIONS",
    "NO_CHANNEL",
    "NORMALIZE_REFUSAL",
    "NUCLEAR_ROLE",
    "NUCLEI_OUTPUT",
    "OUTPUT_MASK_TYPES",
    "SPARSE_WARNING",
    "WEIGHTS_REPOSITORY",
    "WEIGHTS_REVISION",
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
    "output_inputs",
    "install_dinov3",
    "installed_dinov3_commit",
    "installed_version",
    "load_model",
    "weights_file",
    "weights_url",
]

TOOL = "cellpose"
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
    return evaluate(loaded, settings, stack)[0]


def evaluate(loaded: LoadedModel, settings: Settings, stack: np.ndarray) -> tuple[np.ndarray, Any]:
    """Return the labels of :func:`segment_image` and Cellpose's flows (``flows[0]`` is RGB).

    The flows are for the preview (``cellpose.plot.show_segmentation``); the batch keeps only
    the labels. None when Cellpose returned no flows. While Cellpose runs, torch's warning that
    begins :data:`SPARSE_WARNING` is hidden, and only it (:func:`sparse_warning_hidden`).
    """
    array = np.asarray(stack)
    if array.ndim != 3:
        raise SegmentationError(f"The stack is {array.ndim}D; Cellpose gets (C, Y, X).")
    check_channel_count([f"channel {i}" for i in range(array.shape[0])])
    with sparse_warning_hidden():
        out = loaded.model.eval(array, **eval_arguments(loaded, settings))
    labels = np.asarray(out[0] if isinstance(out, tuple) else out)
    if labels.ndim != 2:
        raise SegmentationError(f"Cellpose returned {labels.ndim}D labels; a mask is 2D (Y, X).")
    return labels, (out[1] if isinstance(out, tuple) and len(out) > 1 else None)


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
        The mask type and the masks folder, fixed resolved when the job is made (a relative
        folder never follows a later change of the working directory; review ar, B1).
    overwrite
        Replace masks that are already there (default: skip them).
    channel_roles, output
        The role of each of ``plan.channel_ids`` and the output (``cells`` or ``nuclei``),
        recorded with them; ``()`` and None record none (null).
    channel_numbers
        The channel number typed for a channel chosen by number (#440), as
        ``((channel ID, number), ...)``; recorded with the ID it was translated to.
    run_manifest_sha256
        The SHA-256 of the run manifest ``inputs`` was read from, by default and only
        ``inputs.manifest_sha256`` (another is refused, never the file's now; review ar, B2).
        The record names it, and a batch refuses a manifest changed since.
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
    channel_numbers: tuple[tuple[str, int], ...] = ()
    run_manifest_sha256: str | None = None

    def __post_init__(self) -> None:
        """Fix the masks folder; refuse a job without resolved channels or with unfit roles."""
        object.__setattr__(self, "masks_root", Path(self.masks_root).expanduser().resolve())
        pinned = pinned_manifest(self.inputs, self.run_manifest_sha256)
        object.__setattr__(self, "run_manifest_sha256", pinned)
        if self.plan is None:
            raise SegmentationError("No channels are resolved: choose channels the run has.")
        self.roles_entry()

    def roles_entry(self) -> list[dict[str, Any]] | None:
        """Return the record's ``channel_roles`` (:func:`channel_roles_entry`)."""
        ids, numbers = self.plan.channel_ids, dict(self.channel_numbers)
        return channel_roles_entry(ids, self.channel_roles, self.output, numbers)


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
            check_manifest(job.inputs, job.run_manifest_sha256)  # the run opened, still (aq B2)
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
            return after_commit(entries, state["entry"], exc)
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
        return after_commit(entries, state["entry"], state["late"])
    return state["entry"], ""


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
            "channel_roles": job.roles_entry(),
            "output": job.output,
        },
        channel_ids=job.plan.channel_ids,
        device=loaded.device,
        run_folder=os.fspath(job.inputs.output_folder),
        run_manifest_sha256=job.run_manifest_sha256,  # pinned, never the file's now (aq B2)
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
    check_manifest(job.inputs, job.run_manifest_sha256)
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
