"""The arguments Cellpose's ``eval`` is given, and the parameters a batch records.

Split from :mod:`.segmentation_cellpose` (which re-exports these names) to keep each file
small. Every value Cellpose would default is given explicitly, so what is recorded is what ran:
the full normalisation (Cellpose's defaults read at call time, with the user's values), the
flow iterations, the tile size; and, for the record, where the weights came from (the pinned
repository, revision and URL, :mod:`.segmentation_cellpose_weights`) and whether they, or
DINOv3, were fetched in this session.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .segmentation_cellpose_model import (
    CELLPOSE_VERSION,
    DEVICE_NOTE,
    DINOV3_URL,
    LoadedModel,
    default_normalization,
)
from .segmentation_cellpose_settings import (
    NORMALIZE_REFUSAL,
    Settings,
    flow_iterations,
    rescale_factor,
)
from .segmentation_record import SegmentationError

TILE_OVERLAP, MIN_SIZE, MAX_SIZE_FRACTION = 0.1, 15, 0.4
"""Cellpose's own defaults, passed explicitly so that the record holds the effective values."""
NORMALIZE_PERCENTILE = [1.0, 99.0]
NATIVE_SCALE = (
    "native scale of the model: Cellpose 4.2.1.1 does not estimate the diameter, so a blank "
    "diameter means the model's own scale"
)


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


def inference_parameters(
    loaded: LoadedModel, settings: Settings, channel_ids: Sequence[str]
) -> dict[str, Any]:
    """Return the parameters of a batch for the record: every effective setting, plain JSON.

    The diameter is in pixels, or null with the statement that it is the model's native
    scale; ``rescale`` is the factor Cellpose derives from it (30 / diameter, or 1). The
    normalisation is the full dictionary given to Cellpose and ``niter`` the flow iterations
    it derives from the rescale. ``weights_downloaded`` says whether this session downloaded
    the built-in weights; ``weights_source``, ``weights_repository`` and ``weights_revision``
    where from (the URL, the Hugging Face repository and its commit; null for a model file of
    one's own); the file's SHA-256 is the batch's model ``sha256``. ``dinov3_source`` names
    the DINOv3 repository of ``dinov3_commit`` and ``dinov3_installed`` whether this session
    installed it. ``notes`` holds Cellpose's device behaviour
    (:data:`.segmentation_cellpose_model.DEVICE_NOTE`) for a GPU run.
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
        "dinov3_source": DINOV3_URL if loaded.dinov3_commit else None,
        "dinov3_installed": loaded.dinov3_installed,
        "weights_downloaded": loaded.downloaded,
        "weights_source": loaded.source,
        "weights_repository": loaded.repository,
        "weights_revision": loaded.revision,
        "notes": notes,
    }


__all__ = [
    "MAX_SIZE_FRACTION",
    "MIN_SIZE",
    "NATIVE_SCALE",
    "NORMALIZE_PERCENTILE",
    "TILE_OVERLAP",
    "eval_arguments",
    "inference_parameters",
    "normalization",
]
