"""Run the brick: check an experiment, then write or update its histogram table.

:func:`preflight` is pass 1: it walks the folder and inspects image headers
without reading pixels, and suggests an axis mapping for the user to confirm.
:func:`run_experiment` is pass 2: with the confirmed mapping it checks
everything again, reads only the images that are new or changed since the last
run, and writes ``plane_histograms.csv`` and ``run_manifest.json`` to the output
folder (``docs/plan.md`` §5–§7). Given the confirmed experiment design, it also
writes ``experiment_design.csv`` and records its hash in the manifest; given the
confirmed image type, it records it there too. After writing, it checks the
images' intensities from the table's rows (:mod:`.intensity`) and records the
limits used. Every plane is binned with the chosen histogram range, and its
range check reports channels whose central intensity range covers too few bins
(``narrow_histogram_range``, follow-up P6).

Each row depends only on its own plane, so rows of unchanged files are kept as
they are. Everything is recomputed when there is no usable earlier output, when
a rebuild is requested, or when a setting that affects every row (pixel type,
bin count, histogram range, axis mapping) changed. Nothing is written unless the whole run
succeeds; the earlier output is then replaced.
"""

from __future__ import annotations

from .pipeline import run_experiment
from .preflight import Preflight, SamplePlane, preflight, sample_planes
from .result import RunResult

__all__ = ["Preflight", "RunResult", "SamplePlane", "preflight", "run_experiment", "sample_planes"]
