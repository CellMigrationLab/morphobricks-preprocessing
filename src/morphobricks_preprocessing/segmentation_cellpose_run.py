"""The run, its channels and the closing words of the Cellpose notebook.

Split from :mod:`.segmentation_cellpose`, which re-exports them, to keep each file small.
"""

from __future__ import annotations

import os
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
