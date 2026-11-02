"""The input changes a run would report, named before it runs (design v0.4 §1, PP-10).

It also works out, from the headers only, how each image's planes would be read within
the memory limit (:func:`plane_layouts`, :func:`memory_plan`), for the notebook's "What
will happen".

A run uses every input it is given and takes the others from the earlier run in its output
folder; its report names each input given that differs from the recorded one, with its
effect (:mod:`.recorded`). :func:`planned_changes` names them before the run, from the same
functions, so the notebook can show what a run would change. It reads only the earlier
run's manifest and design; it writes nothing.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from morphobricks_normalization.ranges import HistogramSettings

from ..axes import AxisMapping
from ..calibration import CalibrationReview
from ..design import ExperimentDesign
from ..planes import PlaneLayout, PlaneReadError, plane_layout
from .design_file import design_for_run
from .recorded import (
    GivenInputs,
    InputChange,
    calibration_change,
    recorded_mask_axes,
    recorded_masks_folder,
    resolve_inputs,
    usable,
)
from .reuse import read_previous


def planned_changes(
    given: GivenInputs,
    output_folder: str | os.PathLike,
    *,
    design: ExperimentDesign | None = None,
    calibration: CalibrationReview | None = None,
) -> tuple[InputChange, ...]:
    """Return the input changes a run given these inputs would report.

    Parameters
    ----------
    given
        The inputs the run would be given (:class:`.recorded.GivenInputs`).
    output_folder
        The run's output folder.
    design
        The design the run would be given, or None.
    calibration
        The pixel sizes as the run would record them (after
        :func:`.preflight.fit_calibration`), or None to leave the calibration out.

    Returns
    -------
    tuple of InputChange
        As :attr:`.result.RunResult.input_changes`; empty without a usable earlier run.
    """
    output = Path(output_folder).resolve()
    previous = read_previous(output)[0] if output.is_dir() else None
    changes = resolve_inputs(given, previous).changes
    designed = design_for_run(design, output, previous)[2] if design is not None else ()
    calibrated = () if calibration is None else calibration_change(usable(previous), calibration)
    return (*changes, *designed, *calibrated)


@dataclass(frozen=True)
class PlannedMasks:
    """The masks a run would use: given, else recorded by the earlier run.

    Attributes
    ----------
    folder
        The masks folder the run would use, or None (only OME-Zarr label images, if any).
    mask_axes
        The mask axes stated per mask type: those given, added to the recorded ones.
    recorded_folder
        The masks folder the earlier run in the output folder recorded, or None.
    recorded_axes
        The mask axes the earlier run recorded, ``{type: roles}``.
    """

    folder: Path | None
    mask_axes: dict[str, str]
    recorded_folder: Path | None = None
    recorded_axes: dict[str, str] = field(default_factory=dict)


def planned_masks(given: GivenInputs, output_folder: str | os.PathLike) -> PlannedMasks:
    """Return the masks folder and mask axes a run given these inputs would use.

    The run's own rule (:func:`.recorded.resolve_inputs`, design v0.4 §1): a masks folder
    given is used; none given reuses the one the earlier run recorded, and ``False``
    drops it; stated mask axes add to the recorded ones. It reads only the earlier run's
    manifest; it writes nothing.

    Parameters
    ----------
    given
        The inputs the run would be given; only ``masks_folder`` and ``mask_axes`` count.
    output_folder
        The run's output folder.

    Returns
    -------
    PlannedMasks
    """
    output = Path(output_folder).resolve()
    previous = usable(read_previous(output)[0]) if output.is_dir() else None
    masks = GivenInputs(masks_folder=given.masks_folder, mask_axes=given.mask_axes)
    resolved = resolve_inputs(masks, previous)
    if previous is None:
        return PlannedMasks(resolved.masks_folder, resolved.mask_axes)
    return PlannedMasks(
        resolved.masks_folder,
        resolved.mask_axes,
        recorded_masks_folder(previous),
        recorded_mask_axes(previous),
    )


def plane_layouts(
    root: Path,
    file_mappings: Mapping[str, AxisMapping],
    *,
    memory_limit: int,
    bin_count: int | None = None,
    histogram_settings: HistogramSettings | None = None,
) -> dict[str, PlaneLayout]:
    """Return how each image's planes would be read, from its header only.

    Parameters
    ----------
    root
        The experiment folder.
    file_mappings
        Each image's axis mapping (:attr:`.axes.AxisReview.file_mappings`).
    memory_limit, bin_count, histogram_settings
        As :func:`.planes.plane_layout`.

    Returns
    -------
    dict
        ``{relative path: layout}``, in path order; an image whose layout cannot be worked
        out is left out (the run reports it as ``unsupported_layout``).
    """
    layouts = {}
    for rel, mapping in sorted(file_mappings.items()):
        try:
            layouts[rel] = plane_layout(
                root / rel,
                mapping,
                memory_limit=memory_limit,
                bin_count=bin_count,
                histogram_settings=histogram_settings,
            )
        except PlaneReadError:
            continue
    return layouts


@dataclass(frozen=True)
class MemoryPlan:
    """Which images the memory limit makes the run read in blocks, or stop on.

    Attributes
    ----------
    blocked
        Images with planes too large to read whole, read in blocks (same histograms).
    largest
        The most memory a plane of ``blocked`` needs whole, in bytes (0 without any).
    over
        Images whose smallest readable piece is over the limit: the run stops on them.
    need
        The most memory the smallest piece of a plane of ``over`` needs, in bytes (0
        without any).
    """

    blocked: tuple[str, ...] = ()
    largest: int = 0
    over: tuple[str, ...] = ()
    need: int = 0


def memory_plan(layouts: Mapping[str, PlaneLayout]) -> MemoryPlan:
    """Return which images are read in blocks and which cannot be read within the limit.

    Parameters
    ----------
    layouts
        Each image's layout (:func:`plane_layouts`).

    Returns
    -------
    MemoryPlan
    """
    blocked = {rel: lay for rel, lay in layouts.items() if lay.fits and lay.in_blocks}
    over = {rel: lay for rel, lay in layouts.items() if not lay.fits}
    return MemoryPlan(
        tuple(blocked),
        max((lay.memory_estimate for lay in blocked.values()), default=0),
        tuple(over),
        max((lay.min_memory_estimate for lay in over.values()), default=0),
    )
