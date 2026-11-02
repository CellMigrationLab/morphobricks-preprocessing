"""Publish a run's outputs together, restoring the earlier ones on failure (contract §9).

The table, the design, the manifest and the report are published together, the report
last, only when the run succeeds; before that nothing is erased (design v0.4 §1). A run
that stops writes only its report (:func:`write_report`). Moved from ``run.py`` (PP-3).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from morphobricks_normalization.csv_io import (
    OUTPUT_FILENAME,
)

from .._io import write_text_atomic
from ..design import (
    DESIGN_FILENAME,
)
from ..discovery_report import REPORT_FILENAME, REPORT_TEXT_FILENAME, DiscoveryReport
from ..manifest import (
    MANIFEST_FILENAME,
)

logger = logging.getLogger(__name__)


class _OutputRollbackError(RuntimeError):
    """Raised when an output update fails and its earlier files cannot be restored."""


def write_report(report: DiscoveryReport, folder: Path) -> Path:
    """Write the report as JSON and as text to ``folder``, each file atomically.

    Parameters
    ----------
    report
        The report.
    folder
        The output folder, or the run's staging folder.

    Returns
    -------
    Path
        The JSON file.
    """
    write_text_atomic(folder / REPORT_TEXT_FILENAME, report.text())
    text = json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n"
    return write_text_atomic(folder / REPORT_FILENAME, text)


@contextmanager
def _staged_outputs(
    output: Path, *, with_design: bool = False, with_report: bool = False
) -> Iterator[Path]:
    """Prepare all outputs before publishing, and retain backups if recovery fails.

    The outputs are written to a temporary folder inside ``output``; ``with_design``
    publishes ``experiment_design.csv`` too, and ``with_report`` the report files.
    """
    staged = Path(tempfile.mkdtemp(prefix=".run-", dir=output))
    keep_backups = False
    try:
        yield staged
        _publish_outputs(output, staged, with_design, with_report)
    except _OutputRollbackError:
        keep_backups = True
        raise
    finally:
        if not keep_backups:
            try:
                shutil.rmtree(staged)
            except OSError:
                # A leftover temporary directory does not invalidate completed output.
                logger.warning("Cannot remove temporary output folder %s", staged, exc_info=True)


def _publish_outputs(
    output: Path, staged: Path, with_design: bool = False, with_report: bool = False
) -> None:
    """Replace the outputs, restoring their previous state on a publication failure.

    The manifest is published after the table and design, so a manifest never names a
    design that is not in place; the report, which names the manifest, comes last.
    """
    report = (REPORT_TEXT_FILENAME, REPORT_FILENAME) if with_report else ()
    filenames = (
        OUTPUT_FILENAME,
        *((DESIGN_FILENAME,) if with_design else ()),
        MANIFEST_FILENAME,
        *report,
    )
    backup = staged / "previous"
    backup.mkdir()
    # Copy before replacing either file, so even a backup failure leaves the
    # earlier output intact. Keep backups on the same filesystem for atomic restores.
    for name in filenames:
        previous = output / name
        if previous.exists() or previous.is_symlink():
            shutil.copy2(previous, backup / name, follow_symlinks=False)
    published: list[str] = []
    try:
        for name in filenames:
            os.replace(staged / name, output / name)
            published.append(name)
    except BaseException:
        try:
            for name in reversed(published):
                if (backup / name).exists() or (backup / name).is_symlink():
                    os.replace(backup / name, output / name)
                else:
                    (output / name).unlink()
        except BaseException as exc:
            raise _OutputRollbackError(
                "The output update failed and the earlier files could not be fully restored. "
                f"Backup files have been kept in {backup}. Close programs using the output "
                "files and restore the backups before running again."
            ) from exc
        raise
