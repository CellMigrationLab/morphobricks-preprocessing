"""A small clean preprocessing run for the other bricks' tests (design v0.4 §8, PP-6).

:func:`make_clean_run` writes a tiny experiment of named-axis TIFFs (and, if asked, a masks
folder), runs preprocessing on it and returns where everything is, so a brick's tests read
their inputs through :class:`.run_inputs.RunInputs` exactly as a user's run would give them.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import tifffile

from .axes import prefill_mapping
from .dataset import biological_repeats
from .description import Description
from .design import ExperimentDesign, default_design
from .discovery import discover_experiment
from .run import run_experiment
from .testing_described import CleanRun as CleanRun
from .testing_described import make_described_run as make_described_run

DEFAULTS = {"axes": "CYX", "shape": (2, 16, 20), "pixel_size": 0.5, "design": False}
"""The defaults of the arguments a ``description`` replaces (changing one then raises)."""


def make_clean_run(
    folder: str | os.PathLike,
    *,
    axes: str = DEFAULTS["axes"],
    shape: tuple[int, ...] = DEFAULTS["shape"],
    mask_types: tuple[str, ...] = (),
    pixel_size: float | None = DEFAULTS["pixel_size"],
    design: bool = DEFAULTS["design"],
    seed: int = 0,
    description: Description | bytes | str | None = None,
) -> CleanRun:
    """Write a two-condition experiment of named-axis TIFFs and run preprocessing on it.

    Each condition (``ctrl``, ``drug``) has one biological repeat (``rep1``) of two images
    (``a.tif``, ``b.tif``) of random ``uint16`` pixels, with their axes named in their
    metadata. Each mask type gets one 2-D label image per image (two square objects).

    Parameters
    ----------
    folder
        An empty folder; the experiment, masks and output are written inside it.
    axes, shape
        The images' axes (Y before X; ``YX`` planes with ``C``, ``Z`` and/or ``T``) and shape.
    mask_types
        Mask types to write (``("Cells",)``); 2-D masks need images with one plane.
    pixel_size
        The X and Y pixel size in micrometres written to the headers, or None for none.
    design
        Run with a confirmed experiment design (``ctrl`` the control, each ``rep1`` its own
        repeat group), so the run writes ``experiment_design.csv``.
    seed
        The random seed of the pixels.
    description
        A published experiment description (its bytes or text, published unchanged, or a
        :class:`.description.Description`): the run is then a described run built by
        :func:`.testing_described.make_described_run`, the tree is the one the description
        declares (``image_count`` images per member) and ``mask_types`` are IDs of the
        description's masks, written and checked by the mask step. ``axes``, ``shape``,
        ``pixel_size``, ``design`` and ``seed`` come from the description or are not used, and
        changing one raises ``ValueError``. For masks left unchecked, call
        :func:`.testing_described.make_described_run` with ``mask_step=False``.

    Returns
    -------
    CleanRun

    Raises
    ------
    ValueError
        If a ``description`` is given with an argument it replaces, or with an unknown mask ID.
    RuntimeError
        If the run does not write its outputs (its issues are in the message).
    """
    if description is not None:
        given = {"axes": axes, "shape": shape, "pixel_size": pixel_size, "design": design}
        changed = sorted(k for k, v in given.items() if v != DEFAULTS[k]) + (["seed"] * bool(seed))
        if changed:
            raise ValueError(f"{changed} cannot be set together with a description")
        return make_described_run(folder, description, masks=tuple(mask_types))
    root = Path(folder)
    experiment, masks = root / "experiment", (root / "masks" if mask_types else None)
    rng = np.random.default_rng(seed)
    metadata: dict = {"axes": axes}
    if pixel_size is not None:
        metadata.update(PhysicalSizeX=pixel_size, PhysicalSizeY=pixel_size)
    for condition in ("ctrl", "drug"):
        for name in ("a", "b"):
            path = experiment / condition / "rep1" / f"{name}.ome.tif"
            path.parent.mkdir(parents=True, exist_ok=True)
            data = rng.integers(0, 4000, shape, dtype=np.uint16)
            tifffile.imwrite(path, data, ome=True, photometric="minisblack", metadata=metadata)
            for mask_type in mask_types:
                _write_mask(masks / mask_type / condition / "rep1" / f"{name}.tif", shape[-2:])
    output = root / "output"
    result = run_experiment(
        experiment,
        prefill_mapping(axes),
        output_folder=output,
        masks_folder=masks,
        design=_confirmed_design(experiment) if design else None,
    )
    if not result.written:
        raise RuntimeError("The clean run was not written: " + "; ".join(map(str, result.issues)))
    return CleanRun(experiment, output, masks, result)


def _confirmed_design(experiment: Path) -> ExperimentDesign:
    """Confirm the default design of an experiment, with ``ctrl`` as the control."""
    found = default_design(biological_repeats(discover_experiment(experiment)))
    for group in found.groups:
        found = found.with_confirmed(group, True, "2026-01-01")
    return found.with_control("ctrl", True)


def _write_mask(path: Path, yx: tuple[int, ...]) -> None:
    """Write a 2-D label image with two square objects (labels 1 and 2)."""
    labels = np.zeros(yx, np.uint16)
    labels[1:4, 1:4] = 1
    labels[-4:-1, -4:-1] = 2
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, labels, photometric="minisblack", metadata={"axes": "YX"})
