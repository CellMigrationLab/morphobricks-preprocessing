"""A small clean, described preprocessing run for the other bricks' tests (design v0.4 §8, PP-6).

:func:`make_clean_run` writes a tiny experiment of named-axis TIFFs (and, if asked, a masks
folder), publishes its experiment description (``morphobricks.toml``: every run needs one, Spec
ADR 0002 AR10), runs preprocessing and the mask step on it and returns where everything is, so a
brick's tests read their inputs through :class:`.run_inputs.RunInputs` exactly as a user's run
would give them.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import tifffile

from .axes import prefill_mapping
from .description import Channel, Condition, Description, Mask, Member, Repeat, Unit
from .testing_described import CleanRun as CleanRun
from .testing_described import described_images as described_images
from .testing_described import make_described_run as make_described_run
from .testing_described import publish_and_run

DEFAULTS = {"axes": "CYX", "shape": (2, 16, 20), "pixel_size": 0.5, "design": False}
"""The defaults of the arguments a ``description`` replaces (changing one then raises)."""
CONDITIONS = ("ctrl", "drug")
"""The condition folders of :func:`make_clean_run` (and their condition IDs)."""
CONFIRMED_ON = "2026-01-01"
"""The date the repeat ``rep1`` is confirmed on in :func:`clean_description`."""


def clean_description(
    channels: int, mask_types: tuple[str, ...] = (), *, reference: bool = False
) -> Description:
    """Return the description of :func:`make_clean_run`'s experiment.

    Conditions ``ctrl`` and ``drug`` (folders of the same names), one repeat ``rep1`` confirmed
    on :data:`CONFIRMED_ON` holding both (two images each, no technical-repeat folder), the
    channels ``ch1`` .. ``chN`` (the images' headers name none), and one ``instances`` mask per
    mask type, its ID the type's name.

    Parameters
    ----------
    channels
        The number of channels of every image.
    mask_types
        The mask types (each a valid ID, e.g. ``"Cells"``).
    reference
        True: ``drug`` is compared against ``ctrl`` (``ctrl`` is then the control of the
        derived ``experiment_design.csv``).
    """
    ids = tuple(f"ch{index + 1}" for index in range(channels))
    unit = {"single": Unit("single", ids)}
    return Description(
        conditions={
            "ctrl": Condition("ctrl"),
            "drug": Condition("drug", reference="ctrl" if reference else None),
        },
        channels={channel: Channel() for channel in ids},
        repeats={
            "rep1": Repeat(True, CONFIRMED_ON, {c: Member("rep1", 2, unit) for c in CONDITIONS})
        },
        used_ids={
            "conditions": CONDITIONS,
            "channels": ids,
            "repeats": ("rep1",),
            "technical_repeats": ("single",),
        },
        masks={name: Mask(name, "instances") for name in mask_types},
    )


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
    """Write a two-condition experiment of named-axis TIFFs, describe it and run preprocessing.

    Each condition (``ctrl``, ``drug``) has one biological repeat (``rep1``) of two images
    (``a.tif``, ``b.tif``) of random ``uint16`` pixels, with their axes named in their
    metadata. Each mask type gets one 2-D label image per image (two square objects). The
    description (:func:`clean_description`) is published in the output folder before the run,
    and the mask step runs after it, so every mask is ``checked``.

    Parameters
    ----------
    folder
        An empty folder; the experiment, masks and output are written inside it.
    axes, shape
        The images' axes (Y before X; ``YX`` planes with ``C``, ``Z`` and/or ``T``) and shape.
    mask_types
        Mask types to write (``("Cells",)``); 2-D masks need images with one plane. Each is
        also the mask's ID in the description, so it must be a valid ID.
    pixel_size
        The X and Y pixel size in micrometres written to the headers, or None for none.
    design
        ``drug`` is compared against ``ctrl`` in the description, so the derived
        ``experiment_design.csv`` marks ``ctrl`` as the control. The run always writes the
        derived design.
    seed
        The random seed of the pixels.
    description
        A published experiment description (its bytes or text, published unchanged, or a
        :class:`.description.Description`): the run is then built by
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
        If the run or the mask step does not write its outputs (its issues are in the message).
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
    for condition in CONDITIONS:
        for name in ("a", "b"):
            path = experiment / condition / "rep1" / f"{name}.ome.tif"
            path.parent.mkdir(parents=True, exist_ok=True)
            data = rng.integers(0, 4000, shape, dtype=np.uint16)
            tifffile.imwrite(path, data, ome=True, photometric="minisblack", metadata=metadata)
            for mask_type in mask_types:
                _write_mask(masks / mask_type / condition / "rep1" / f"{name}.tif", shape[-2:])
    channels = shape[axes.index("C")] if "C" in axes else 1
    described = clean_description(channels, tuple(mask_types), reference=design)
    return publish_and_run(root, experiment, masks, prefill_mapping(axes), described)


def _write_mask(path: Path, yx: tuple[int, ...]) -> None:
    """Write a 2-D label image with two square objects (labels 1 and 2)."""
    labels = np.zeros(yx, np.uint16)
    labels[1:4, 1:4] = 1
    labels[-4:-1, -4:-1] = 2
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, labels, photometric="minisblack", metadata={"axes": "YX"})
