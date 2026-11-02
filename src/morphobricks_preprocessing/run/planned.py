"""The input changes a run would report, named before it runs (design v0.4 §1, PP-10).

A run uses every input it is given and takes the others from the earlier run in its output
folder; its report names each input given that differs from the recorded one, with its
effect (:mod:`.recorded`). :func:`planned_changes` names them before the run, from the same
functions, so the notebook can show what a run would change. It reads only the earlier
run's manifest and design; it writes nothing.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..calibration import CalibrationReview
from ..design import ExperimentDesign
from .design_file import design_for_run
from .recorded import GivenInputs, InputChange, calibration_change, resolve_inputs, usable
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
