"""A small described preprocessing run for the other bricks' tests (Stage 2 design, PR 1).

:func:`make_described_run` writes the experiment folder a published experiment description
(``morphobricks.toml``, experiment-v1) declares: one folder per condition, repeat and
technical repeat, ``image_count`` tiny images per member (its total, spread over its
technical repeats; each image has as many channels as the unit declares, or is RGB colour
for an ``rgb`` unit), and, when asked, one mask per image for each declared mask. It
publishes the description beside the output, runs preprocessing on it and,
unless told not to, runs the mask step, so a brick's tests read their inputs through
:class:`.run_inputs.RunInputs` exactly as a user's described run gives them.

:func:`.testing.make_clean_run` calls it for ``make_clean_run(description=...)``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tifffile

from .axes import AxisMapping, prefill_mapping
from .description import Description, Unit, description_from_bytes
from .description_write import canonical_bytes
from .image_type import RGB
from .run import RunResult, run_experiment
from .run.mask_step import describe_masks, prepare_mask_step


@dataclass(frozen=True)
class CleanRun:
    """A clean run made by :func:`.testing.make_clean_run` or :func:`make_described_run`.

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
    description
        The experiment description of a described run, else None.
    """

    experiment: Path
    output: Path
    masks: Path | None
    result: RunResult
    description: Description | None = None


PUBLISHED_NAME = "morphobricks.toml"
"""The published description's file name in the output folder."""
IMAGE_SIZE = 8
"""The side of every image and mask, in pixels."""


def description_of(description: Description | bytes | str) -> tuple[Description, bytes]:
    """Return the description and the exact bytes to publish.

    Bytes and text are published as given (the run records their SHA-256); a
    :class:`.description.Description` is written canonically.

    Raises
    ------
    morphobricks_preprocessing.description.DescriptionError
        If bytes or text are not a valid description.
    """
    if isinstance(description, Description):
        return description, canonical_bytes(description)
    data = description.encode("utf-8") if isinstance(description, str) else bytes(description)
    return description_from_bytes(data), data


def unit_folder(description: Description, repeat_id: str, condition_id: str, unit: Unit) -> Path:
    """Return the unit's folder relative to the experiment root (no level for ``single``)."""
    member = description.repeats[repeat_id].members[condition_id]
    parts = [description.conditions[condition_id].source_folder, member.source_repeat]
    if unit.source_folder != "single":
        parts.append(unit.source_folder)
    return Path(*parts)


def _units(description: Description) -> Iterator[tuple[Path, Unit, int]]:
    """Yield ``(folder, unit, image count)`` of every declared unit.

    A member's ``image_count`` is its total over its technical repeats, so it is spread
    over them as evenly as possible: every unit gets ``image_count // n`` images and the
    first ``image_count % n`` units, by sorted unit ID, one more.

    Raises
    ------
    ValueError
        If a member has fewer images than technical repeats (a unit would be empty).
    """
    for repeat_id, repeat in description.repeats.items():
        for condition_id, member in repeat.members.items():
            unit_ids = sorted(member.technical_repeats)
            base, extra = divmod(member.image_count, len(unit_ids))
            if base == 0:
                raise ValueError(
                    f"member {condition_id!r} of repeat {repeat_id!r} has image_count "
                    f"{member.image_count} for {len(unit_ids)} technical repeats; every "
                    "technical repeat needs at least one image"
                )
            for position, unit_id in enumerate(unit_ids):
                unit = member.technical_repeats[unit_id]
                folder = unit_folder(description, repeat_id, condition_id, unit)
                yield folder, unit, base + (1 if position < extra else 0)


def _mapping(description: Description) -> AxisMapping:
    """Return the axes of the images this description's units are written as."""
    kinds = {unit.image_type == RGB for _, unit, _ in _units(description)}
    if len(kinds) != 1:
        raise ValueError("a run cannot mix rgb and fluorescence units")
    return prefill_mapping("YXS" if kinds == {True} else "CYX")


def _write_image(path: Path, unit: Unit, seed: int) -> None:
    """Write a tiny image of the unit's channels (RGB colour for an ``rgb`` unit)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    if unit.image_type == RGB:
        pixels = rng.integers(0, 200, (IMAGE_SIZE, IMAGE_SIZE, 3), dtype=np.uint8)
        tifffile.imwrite(path, pixels, photometric="rgb", metadata={"axes": "YXS"})
        return
    pixels = rng.integers(0, 200, (len(unit.channels), IMAGE_SIZE, IMAGE_SIZE), dtype=np.uint8)
    tifffile.imwrite(path, pixels, photometric="minisblack", metadata={"axes": "CYX"})


def _write_mask(path: Path, representation: str) -> None:
    """Write a 2-D mask: two labelled squares (``instances``), else one region of 1."""
    labels = np.zeros((IMAGE_SIZE, IMAGE_SIZE), np.uint8)
    labels[1:3, 1:3] = 1
    if representation == "instances":
        labels[-3:-1, -3:-1] = 2
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, labels, photometric="minisblack", metadata={"axes": "YX"})


def write_described_tree(
    root: Path, masks_root: Path | None, description: Description, mask_ids: tuple[str, ...] = ()
) -> list[str]:
    """Write the images (and the masks of ``mask_ids``) of a description; return image paths.

    Raises
    ------
    ValueError
        If a mask ID is not declared by the description, or masks are asked without a folder.
    """
    unknown = [m for m in mask_ids if m not in description.masks]
    if unknown or (mask_ids and masks_root is None):
        raise ValueError(
            f"mask IDs {unknown} are not declared by the description "
            f"(declared: {sorted(description.masks)})"
        )
    found: list[str] = []
    for folder, unit, count in _units(description):
        for index in range(count):
            rel = (folder / f"img{index}.tif").as_posix()
            _write_image(root / rel, unit, seed=len(found) + 1)
            for mask_id in mask_ids:
                mask = description.masks[mask_id]
                _write_mask(masks_root / mask.source_type / rel, mask.representation)
            found.append(rel)
    return sorted(found)


def make_described_run(
    folder: str | os.PathLike,
    description: Description | bytes | str,
    *,
    masks: tuple[str, ...] = (),
    mask_step: bool = True,
) -> CleanRun:
    """Write the experiment a description declares, publish it and run preprocessing.

    The folder holds ``experiment`` (the images), ``masks`` (when asked), and ``output``
    (the run, with the published ``morphobricks.toml`` as given).

    Parameters
    ----------
    folder
        An empty folder.
    description
        The description: its bytes or text (published unchanged) or a ``Description``.
    masks
        IDs of the description's masks to write, one file per image and ID (instances: two
        labelled squares; binary: one region of 1s). None by default.
    mask_step
        Run the mask step (:func:`.run.mask_step.describe_masks`) after the run, so the masks
        are ``checked``; False leaves them ``not yet checked``. Ignored without ``masks``.

    Returns
    -------
    CleanRun
        Where everything is, and the run's result.

    Raises
    ------
    ValueError
        For an unknown mask ID, a description that mixes rgb and fluorescence units, or a
        member whose ``image_count`` is below its number of technical repeats.
    RuntimeError
        If the run or the mask step does not write its outputs (its issues are in the message).
    """
    root = Path(folder)
    parsed, data = description_of(description)
    experiment, output = root / "experiment", root / "output"
    masks_root = root / "masks" if masks else None
    mapping = _mapping(parsed)
    write_described_tree(experiment, masks_root, parsed, tuple(masks))
    output.mkdir(parents=True, exist_ok=True)
    (output / PUBLISHED_NAME).write_bytes(data)
    result = run_experiment(experiment, mapping, output_folder=output, masks_folder=masks_root)
    if not result.written:
        raise RuntimeError(
            "The described run was not written: " + "; ".join(map(str, result.issues))
        )
    if masks and mask_step:
        step = describe_masks(prepare_mask_step(None, output))
        if step.issues:
            raise RuntimeError("The mask step failed: " + "; ".join(map(str, step.issues)))
    return CleanRun(experiment, output, masks_root, result, parsed)
