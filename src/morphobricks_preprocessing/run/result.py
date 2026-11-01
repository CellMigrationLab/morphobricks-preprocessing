"""What a run did: :class:`RunResult` (moved from ``run.py``, PP-3)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from morphobricks_normalization.csv_io import (
    OUTPUT_FILENAME,
)

from ..design import (
    DESIGN_FILENAME,
    ExperimentDesign,
)
from ..intensity import IntensityReport
from ..manifest import (
    MANIFEST_FILENAME,
)
from ..validation import ValidationIssue, has_errors


@dataclass(frozen=True)
class RunResult:
    """What a run did.

    Attributes
    ----------
    output_folder
        Where the table and manifest are (or would have been) written.
    written
        True if the run succeeded and the output was written.
    issues
        Errors that stopped the run, and warnings, errors first.
    new, changed, unchanged, removed
        Files compared with the earlier run: not in it, modified since, as they
        were, and in it but no longer analysed (deleted, renamed or excluded).
        Without a usable earlier manifest, every file is new.
    read
        Files whose pixels were read in this run.
    excluded
        Images the user excluded.
    rebuild_reason
        Why every row was recomputed, or None when the earlier table was updated.
    plane_count
        Number of rows in the written table.
    design
        The experiment design written with the table, or None when the run was
        given none.
    image_type
        The image type recorded in the manifest, or None when the run was given
        none.
    intensity
        The intensity checks of the written table (warnings, never errors), or
        None when nothing was written.
    """

    output_folder: Path
    written: bool
    issues: tuple[ValidationIssue, ...] = ()
    new: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    read: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()
    rebuild_reason: str | None = None
    plane_count: int = 0
    design: ExperimentDesign | None = None
    image_type: str | None = None
    intensity: IntensityReport | None = None

    @property
    def ok(self) -> bool:
        """True if no issue blocks processing."""
        return not has_errors(self.issues)

    @property
    def design_path(self) -> Path | None:
        """Location of ``experiment_design.csv``, or None when the run had no design."""
        return self.output_folder / DESIGN_FILENAME if self.design is not None else None

    @property
    def csv_path(self) -> Path:
        """Location of ``plane_histograms.csv``."""
        return self.output_folder / OUTPUT_FILENAME

    @property
    def manifest_path(self) -> Path:
        """Location of ``run_manifest.json``."""
        return self.output_folder / MANIFEST_FILENAME
