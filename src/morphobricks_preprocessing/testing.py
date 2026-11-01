"""A small clean preprocessing run for the other bricks' tests (design v0.4 §8, PP-6).

:func:`make_clean_run` writes a tiny experiment of named-axis TIFFs (and, if asked, a masks
folder), runs preprocessing on it and returns where everything is, so a brick's tests read
their inputs through :class:`.run_inputs.RunInputs` exactly as a user's run would give them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tifffile

from .axes import prefill_mapping
from .run import RunResult, run_experiment


@dataclass(frozen=True)
class CleanRun:
    """A clean run made by :func:`make_clean_run`.

    Attributes
    ----------
    experiment
        The experiment folder.
    output
        The run's output folder.
    masks
        The masks folder, or None.
    result
        What :func:`.run.run_experiment` returned.
    """

    experiment: Path
    output: Path
    masks: Path | None
    result: RunResult


def make_clean_run(
    folder: str | os.PathLike,
    *,
    axes: str = "CYX",
    shape: tuple[int, ...] = (2, 16, 20),
    mask_types: tuple[str, ...] = (),
    pixel_size: float | None = 0.5,
    seed: int = 0,
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
    seed
        The random seed of the pixels.

    Returns
    -------
    CleanRun

    Raises
    ------
    RuntimeError
        If the run does not write its outputs (its issues are in the message).
    """
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
        experiment, prefill_mapping(axes), output_folder=output, masks_folder=masks
    )
    if not result.written:
        raise RuntimeError("The clean run was not written: " + "; ".join(map(str, result.issues)))
    return CleanRun(experiment, output, masks, result)


def _write_mask(path: Path, yx: tuple[int, ...]) -> None:
    """Write a 2-D label image with two square objects (labels 1 and 2)."""
    labels = np.zeros(yx, np.uint16)
    labels[1:4, 1:4] = 1
    labels[-4:-1, -4:-1] = 2
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, labels, photometric="minisblack", metadata={"axes": "YX"})
