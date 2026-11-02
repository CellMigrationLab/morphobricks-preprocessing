"""Write a run's report; the run's outputs are published as one generation (contract §9).

The table, the design, the manifest and the report are published together, only when the
run succeeds, as one generation made current in one atomic step (:mod:`.generation`);
before that nothing is erased (design v0.4 §1). A run that stops writes only its report
(:func:`write_report`). Moved from ``run.py`` (PP-3).
"""

from __future__ import annotations

import json
from pathlib import Path

from .._io import write_text_atomic
from ..discovery_report import REPORT_FILENAME, REPORT_TEXT_FILENAME, DiscoveryReport


def write_report(report: DiscoveryReport, folder: Path) -> Path:
    """Write the report as JSON and as text to ``folder``, each file atomically.

    Parameters
    ----------
    report
        The report.
    folder
        The output folder, or the publication folder of the run's generation.

    Returns
    -------
    Path
        The JSON file.
    """
    write_text_atomic(folder / REPORT_TEXT_FILENAME, report.text())
    text = json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n"
    return write_text_atomic(folder / REPORT_FILENAME, text)
