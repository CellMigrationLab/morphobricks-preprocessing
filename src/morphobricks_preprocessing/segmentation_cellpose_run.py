"""The run, its channels and the closing words of the Cellpose notebook.

Split from :mod:`.segmentation_cellpose`, which re-exports them, to keep each file small.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from .run_inputs import RunInputs
from .segmentation_record import SegmentationError, check_mask_type


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


def next_steps_lines(
    mask_types: str | Sequence[str], masks_root: str | os.PathLike
) -> tuple[str, ...]:
    """Return what to do after the masks are made, as lines of text.

    ``mask_types`` is the mask type made, or the two of the output Both (#440): each is
    declared on its own. The notebook never pairs a cell with a nucleus; that relationship is
    computed downstream from the geometry (MorphoRelate, containment or overlap).

    Raises
    ------
    SegmentationError
        For a mask type preprocessing would refuse.
    """
    types = (mask_types,) if isinstance(mask_types, str) else tuple(mask_types)
    for mask_type in types:
        check_mask_type(mask_type)
    sections = "; ".join(
        f'[masks.{t}]  source_type = "{t}"  representation = "instances"' for t in types
    )
    named = "the new mask type" if len(types) == 1 else f"the {len(types)} new mask types"
    lines = [
        f"1. Declare {named} in the experiment description, in the preprocessing "
        f"notebook (step 4, Masks) or by adding to morphobricks.toml:  {sections}.",
        "2. Save the description.",
        f"3. Run preprocessing again with the masks folder {os.fspath(masks_root)} (the "
        "histograms are reused; the masks are read and checked).",
        "4. Describe the masks (step 4 of the preprocessing notebook).",
        "5. Then continue with MorphoFeature.",
    ]
    if len(types) > 1:
        lines.append(
            f"The mask types {' and '.join(types)} are separate: this notebook never pairs a "
            "cell with a nucleus by label number. Relate them downstream from their geometry "
            "(MorphoRelate: containment or overlap)."
        )
    return tuple(lines)
