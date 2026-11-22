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
  or failed with the error (:func:`run_batch`); nothing is dropped. The full normalisation,
  the flow iterations, and whether built-in weights were downloaded (and from where) are in it.

Mask paths, pairing, the mask-type rules, writing, the folder rules and the record are the
hand-off's own (:mod:`.segmentation_handoff`, :mod:`.segmentation_record`); nothing is repeated.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ._io import file_sha256
from .manifest import MANIFEST_FILENAME
from .run_inputs import Fov, RunInputs
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
    dinov3_install_command,
    install_dinov3,
    installed_dinov3_commit,
    installed_version,
    load_model,
    weights_file,
)
from .segmentation_handoff import SegmentationPlan, images_to_segment, write_mask
from .segmentation_record import (
    SegmentationBatch,
    SegmentationError,
    SegmentedImage,
    check_mask_type,
    mask_sha256,
    new_batch_id,
    write_record,
)

__all__ = [
    "BUILTIN_MODELS",
    "CELLPOSE_VERSION",
    "DEFAULT_MODEL",
    "DINOV3_COMMIT",
    "DINOV3_URL",
    "DINO_MODELS",
    "LoadedModel",
    "ModelChoice",
    "Settings",
    "check_device",
    "check_dinov3",
    "choose_model",
    "default_normalization",
    "dinov3_install_command",
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
FLOW_ITERATIONS = 200
"""Cellpose's flow iterations at the native scale; it divides it by the rescale factor."""
NATIVE_SCALE = (
    "native scale of the model: Cellpose 4.2.1.1 does not estimate the diameter, so a blank "
    "diameter means the model's own scale"
)
IMAGE_SKIPPED = "a mask of this type is already there; not replaced (overwrite is off)"


# --- settings and what is asked for -----------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    """The inference settings the user chooses.

    Attributes
    ----------
    diameter
        Cell diameter in **pixels**, or None for the model's native scale (Cellpose 4.2.1.1
        does not estimate a diameter). Never 0.
    flow_threshold
        Flow error threshold, 0 or more (Cellpose's default 0.4).
    cellprob_threshold
        Cell probability threshold (Cellpose's default 0).
    normalize
        Cellpose's own normalisation: each channel to its 1st to 99th percentile. False
        passes the stored values unchanged.
    batch_size
        Tiles run together on the device, 1 or more.
    """

    diameter: float | None = None
    flow_threshold: float = 0.4
    cellprob_threshold: float = 0.0
    normalize: bool = True
    batch_size: int = 8

    def __post_init__(self) -> None:
        """Refuse a value Cellpose would accept but this brick will not record as it is."""
        if self.diameter is not None and not _positive(self.diameter):
            raise SegmentationError(
                f"The diameter must be a positive number of pixels, or blank for the model's "
                f"native scale; got {self.diameter!r}. Zero is refused: it would silently mean "
                "the native scale."
            )
        if not _finite(self.flow_threshold) or self.flow_threshold < 0:
            raise SegmentationError(
                f"The flow threshold must be 0 or more, not {self.flow_threshold!r}."
            )
        if not _finite(self.cellprob_threshold):
            raise SegmentationError(
                f"The cell probability threshold must be a number, not {self.cellprob_threshold!r}."
            )
        if not isinstance(self.normalize, bool):
            raise SegmentationError("normalize is on or off.")
        if (
            isinstance(self.batch_size, bool)
            or not isinstance(self.batch_size, int)
            or self.batch_size < 1
        ):
            raise SegmentationError(
                f"The batch size must be a whole number, 1 or more, not {self.batch_size!r}."
            )


def _finite(value: object) -> bool:
    """Return True for a real number that is neither NaN nor infinite (a bool is not one)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _positive(value: object) -> bool:
    """Return True for a finite number above 0."""
    return _finite(value) and value > 0  # type: ignore[operator]


def diameter_from_text(text: str) -> float | None:
    """Return the diameter typed in a text field: blank is None (native scale), else pixels.

    Raises
    ------
    SegmentationError
        For text that is not a positive number (0 included).
    """
    if not text.strip():
        return None
    try:
        value = float(text)
    except ValueError:
        raise SegmentationError(
            f"The diameter {text!r} is not a number. Type pixels (for example 30) or leave it "
            "blank."
        ) from None
    return Settings(diameter=value).diameter


# --- one image --------------------------------------------------------------------------------


def rescale_factor(settings: Settings) -> float:
    """Return the factor Cellpose derives from the diameter: 30 / diameter, or 1 (native scale)."""
    return 1.0 if settings.diameter is None else 30.0 / settings.diameter


def flow_iterations(settings: Settings) -> int:
    """Return the flow iterations Cellpose 4.2.1.1 uses when none are given: 200 / rescale.

    Cellpose computes ``int(200 / rescale)`` (resampling is on); it is passed explicitly and
    recorded, so what ran is not left to Cellpose's derivation.
    """
    return max(1, int(FLOW_ITERATIONS / rescale_factor(settings)))


def normalization(settings: Settings) -> dict[str, Any]:
    """Return the full normalisation given to Cellpose: its defaults now, with the user's values.

    The defaults (``tile_norm_blocksize``, ``sharpen_radius``, ``invert``, ``lowhigh``, ...) come
    from Cellpose's module global at call time, copied; ``normalize`` and the percentiles are
    then set from the settings. Nothing is left to Cellpose's defaults, so the record holds all.
    """
    return {
        **default_normalization(),
        "normalize": settings.normalize,
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
    """

    inputs: RunInputs
    plan: SegmentationPlan
    loaded: LoadedModel
    settings: Settings
    mask_type: str
    masks_root: Path
    overwrite: bool = False

    def __post_init__(self) -> None:
        """Refuse a job without resolved channels, before anything is segmented."""
        if self.plan is None:
            raise SegmentationError("No channels are resolved: choose channels the run has.")


@dataclass(frozen=True)
class BatchResult:
    """What a batch did: its record entry and where the record was written.

    Attributes
    ----------
    batch
        The batch as recorded (one entry per image handled).
    record
        The record file, or None when no image was handled.
    """

    batch: SegmentationBatch
    record: Path | None

    def rows(self) -> tuple[tuple[str, str, str], ...]:
        """Return ``(image, status, message)`` for every image: nothing is left out."""
        return tuple((i.image, i.status, i.message) for i in self.batch.images)

    def counts(self) -> dict[str, int]:
        """Return how many images were written, skipped and failed."""
        statuses = [i.status for i in self.batch.images]
        return {s: statuses.count(s) for s in ("written", "skipped", "failed")}


def non_2d_images(inputs: RunInputs) -> tuple[str, ...]:
    """Return the images with more than one Z plane or time point (refused: 2D only for now)."""
    return tuple(f.relative_path for f in inputs.fovs() if f.z_count > 1 or f.t_count > 1)


def _segment_one(job: SegmentationJob, fov: Fov) -> SegmentedImage:
    """Segment and write one image; any failure is returned as a ``failed`` entry."""
    try:
        labels = segment_image(job.loaded, job.settings, job.plan.read(fov))
        path = write_mask(
            job.masks_root, job.mask_type, fov, labels, job.inputs, overwrite=job.overwrite
        )
        return SegmentedImage(fov.relative_path, "written", mask_sha256=mask_sha256(path))
    except Exception as exc:  # per-image failures are reported in the table, never dropped
        return SegmentedImage(fov.relative_path, "failed", f"{type(exc).__name__}: {exc}")


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
        parameters=inference_parameters(loaded, job.settings, job.plan.channel_ids),
        channel_ids=job.plan.channel_ids,
        device=loaded.device,
        run_folder=os.fspath(job.inputs.output_folder),
        run_manifest_sha256=file_sha256(job.inputs.output_folder / MANIFEST_FILENAME),
        images=tuple(images),
    )


def _write_batch(
    job: SegmentationJob, batch_id: str, entries: list[SegmentedImage]
) -> tuple[SegmentationBatch, Path | None]:
    """Build the batch and write its record (none when no image was handled)."""
    batch = _batch(job, batch_id, entries)
    return batch, write_record(job.masks_root, job.mask_type, batch) if entries else None


def run_batch(
    job: SegmentationJob,
    fovs: Sequence[Fov] | None = None,
    *,
    progress: Callable[[int, int, str], None] | None = None,
    batch_id: str | None = None,
) -> BatchResult:
    """Segment images one at a time, write each mask and record every outcome.

    Images that already have a mask of the type are ``skipped`` (never replaced) unless
    ``job.overwrite``; an image that fails is ``failed`` with the error and the batch goes on.
    The record is written even when the batch is interrupted, with the images done so far.

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
        For a masks folder or mask type the hand-off refuses, or a record that cannot be
        written (the masks written stay).
    """
    chosen = tuple(job.inputs.fovs() if fovs is None else fovs)
    missing = {
        f.relative_path for f in images_to_segment(job.inputs, job.mask_type, job.masks_root)
    }
    started = batch_id or new_batch_id()
    entries: list[SegmentedImage] = []
    try:
        for done, fov in enumerate(chosen):
            if progress is not None:
                progress(done, len(chosen), fov.relative_path)
            if job.overwrite or fov.relative_path in missing:
                entries.append(_segment_one(job, fov))
            else:
                entries.append(SegmentedImage(fov.relative_path, "skipped", IMAGE_SKIPPED))
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
    batch, record = _write_batch(job, started, entries)
    return BatchResult(batch, record)


# --- the run, the channels and what is shown --------------------------------------------------


def open_run(run_folder: str) -> RunInputs:
    """Open the run in ``run_folder`` (preprocessing's own checks; refusals are unchanged).

    Raises
    ------
    SegmentationError
        For a blank folder.
    RunInputsError
        For a run that cannot be opened (:meth:`.run_inputs.RunInputs.open`).
    """
    if not run_folder.strip():
        raise SegmentationError("Give the run's output folder (the one holding run_manifest.json).")
    return RunInputs.open(Path(run_folder.strip()))


def channel_coverage(inputs: RunInputs) -> dict[str, tuple[int, int]]:
    """Return ``{channel ID: (images that have it, images)}``, in the order first met."""
    fovs = inputs.fovs()
    order = dict.fromkeys(c for f in fovs for c in (f.channel_ids or ()))
    return {c: (sum(c in (f.channel_ids or ()) for f in fovs), len(fovs)) for c in order}


def next_steps_lines(mask_type: str, masks_root: str | os.PathLike) -> tuple[str, ...]:
    """Return what to do after the masks are made, as lines of text.

    Raises
    ------
    SegmentationError
        For a mask type preprocessing would refuse.
    """
    check_mask_type(mask_type)
    return (
        f"1. Declare the new mask type in the experiment description, in the preprocessing "
        f"notebook (step 4, Masks) or by adding to morphobricks.toml:  [masks.{mask_type}]  "
        f'source_type = "{mask_type}"  representation = "instances".',
        "2. Save the description.",
        f"3. Run preprocessing again with the masks folder {os.fspath(masks_root)} (the "
        "histograms are reused; the masks are read and checked).",
        "4. Describe the masks (step 4 of the preprocessing notebook).",
        "5. Then continue with MorphoFeature.",
    )
