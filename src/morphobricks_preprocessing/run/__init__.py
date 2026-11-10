"""Run the brick: check an experiment, then write or update its histogram table.

:func:`preflight` is pass 1: it walks the folder and inspects image headers
without reading pixels, and suggests an axis mapping for the user to confirm.
:func:`run_experiment` is pass 2: with the confirmed mapping it checks
everything again, reads only the images that are new or changed since the last
run, and writes ``plane_histograms.csv`` and ``run_manifest.json`` to the output
folder (``docs/plan.md`` §5–§7). Every run needs the experiment description published in
the output folder (``morphobricks.toml``; ``A_DESCRIPTION_REQUIRED`` without it): the run
writes the ``experiment_design.csv`` derived from it and records its hash in the manifest;
given the confirmed image type, it records it there too. After writing, it checks the
images' intensities from the table's rows (:mod:`.intensity`) and records the
limits used. Every plane is binned with the chosen histogram range, and its
range check reports channels whose central intensity range covers too few bins
(``narrow_histogram_range``, follow-up P6).

Each row depends only on its own plane, so rows of unchanged files are kept as
they are. Everything is recomputed when there is no usable earlier output, when
a rebuild is requested, or when a setting that affects every row (pixel type,
bin count, histogram range, axis mapping) changed. A refused run publishes no scientific
output and leaves the sources untouched (its discovery report is written); a run that
succeeds replaces the earlier output.
"""

from __future__ import annotations

from .calibrate import write_calibration
from .check_step import write_check_report
from .lock import LOCK_FILENAME, LockHolder, OutputLockedError, read_lock, remove_stale_lock
from .mask_step import (
    MaskStep,
    MaskStepResult,
    RecordedMasks,
    describe_masks,
    prepare_mask_step,
    recorded_masks,
)
from .modes import FULL, MODES, RESTART, RunMode
from .pipeline import run_experiment
from .preflight import Preflight, SamplePlane, preflight, sample_planes
from .result import RunResult

__all__ = [
    "FULL",
    "LOCK_FILENAME",
    "MODES",
    "RESTART",
    "LockHolder",
    "MaskStep",
    "MaskStepResult",
    "OutputLockedError",
    "Preflight",
    "RecordedMasks",
    "RunMode",
    "RunResult",
    "SamplePlane",
    "describe_masks",
    "preflight",
    "prepare_mask_step",
    "read_lock",
    "recorded_masks",
    "remove_stale_lock",
    "run_experiment",
    "sample_planes",
    "write_calibration",
    "write_check_report",
]
