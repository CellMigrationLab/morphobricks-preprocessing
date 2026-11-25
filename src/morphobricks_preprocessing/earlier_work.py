"""What an output folder holds of earlier work, and whether its run is finished (v0.9.0).

Owner decision (25 November 2026): when the notebook's output folder is set and it holds
earlier work, the notebook restores it with a message. :func:`earlier_work` says what is
there (the recorded check, the saved answers, the saved description, the run manifest) and
when each was written; nothing is changed or written. :func:`run_mismatch` says whether the
run the manifest describes is finished and current: of this experiment folder, of the saved
description as it is now, with exactly the images analysed now, each with its recorded
fingerprint, and it opens as a brick opens it (:meth:`.run_inputs.RunInputs.open`: the
manifest version, the table, design, ``calibration.csv``, conversion record, images and masks
as recorded).
Anything else is named, with ``RunInputs``' own refusal; nothing is guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .check_record import CHECK_RECORD_FILENAME, CheckRecord, CheckRecordError, read_check_record
from .description_file import DESCRIPTION_FILENAME, DRAFT_FILENAME
from .fingerprint import Fingerprint
from .manifest import MANIFEST_FILENAME, ManifestFormatError, RunManifest
from .run.preflight import Preflight
from .run.reuse import read_previous
from .run_inputs import RunInputs, RunInputsError

WHAT = {
    CHECK_RECORD_FILENAME: "the folder check",
    DRAFT_FILENAME: "your saved answers",
    DESCRIPTION_FILENAME: "the saved experiment description",
    MANIFEST_FILENAME: "a run",
}
"""What each file of earlier work is, in the notebook's words."""


@dataclass(frozen=True)
class EarlierWork:
    """The earlier work an output folder holds.

    Attributes
    ----------
    output
        The output folder, resolved.
    found
        ``(file name, when it was last written)`` of each file of :data:`WHAT` there, in
        that order; the time is local, to the minute.
    record
        The recorded check (:mod:`.check_record`), or None.
    record_problem
        Why the recorded check cannot be read ("" when it can, or there is none).
    manifest
        The run manifest, or None.
    manifest_problem
        Why there is no usable manifest ("" when there is one, or no file at all).
    """

    output: Path
    found: tuple[tuple[str, str], ...] = ()
    record: CheckRecord | None = None
    record_problem: str = ""
    manifest: RunManifest | None = None
    manifest_problem: str = ""

    @property
    def exists(self) -> bool:
        """True when the folder holds any earlier work."""
        return bool(self.found)

    @property
    def latest(self) -> str:
        """Return when the latest of the files was written ("" without any)."""
        return max((when for _, when in self.found), default="")

    @property
    def roots(self) -> tuple[Path, ...]:
        """Return the experiment folders the check and the run recorded, each once."""
        found = []
        if self.record is not None:
            found.append(Path(self.record.experiment_root))
        if self.manifest is not None:
            found.append(Path(self.manifest.experiment_root).resolve())
        return tuple(dict.fromkeys(found))

    @property
    def experiment_root(self) -> Path | None:
        """Return the one experiment folder recorded; None for none, or two that disagree."""
        roots = self.roots
        return roots[0] if len(roots) == 1 else None

    def has(self, name: str) -> bool:
        """Return True when the file ``name`` is there."""
        return any(found == name for found, _ in self.found)


def earlier_work(output: Path) -> EarlierWork:
    """Return the earlier work in ``output``; read only, nothing is written.

    Raises
    ------
    OSError
        When the output folder cannot be listed (a storage problem).
    """
    output = Path(output).resolve()
    if not output.is_dir():
        return EarlierWork(output)
    found = []
    for name in WHAT:
        path = output / name
        if path.is_file():
            stamp = datetime.fromtimestamp(path.stat().st_mtime)
            found.append((name, stamp.strftime("%Y-%m-%d %H:%M")))
    record, record_problem = None, ""
    try:
        record = read_check_record(output)
    except CheckRecordError as exc:
        record_problem = str(exc)
    manifest, why = read_previous(output) if (output / MANIFEST_FILENAME).is_file() else (None, "")
    return EarlierWork(output, tuple(found), record, record_problem, manifest, why or "")


def run_mismatch(
    output: Path, manifest: RunManifest, pre: Preflight, published_sha256: str | None
) -> str:
    """Return why the run in ``output`` is not finished and current ("" when it is).

    Parameters
    ----------
    output
        The output folder.
    manifest
        Its run manifest.
    pre
        The folder check now, with its headers bound to their fingerprints.
    published_sha256
        The SHA-256 of the saved description now, None when there is none (or it has
        changed since the folders were checked).
    """
    recorded = (manifest.experiment_description or {}).get("sha256")
    if published_sha256 is None:
        return "the experiment description is not saved as the run used it"
    if recorded != published_sha256:
        return "the experiment description was saved again after the run"
    if Path(manifest.experiment_root).resolve() != Path(pre.discovery.root).resolve():
        return f"it is of another experiment folder ({manifest.experiment_root})"
    entries = {entry.relative_path: entry for entry in manifest.files}
    now = {meta.relative_path for meta in pre.metadata.files}
    if set(entries) != now:
        return "images were added, removed or excluded since the run"
    for rel in sorted(entries):
        if pre.header_fingerprints.get(rel) != Fingerprint.recorded(entries[rel]):
            return f"{rel} changed since the run"
    try:  # everything else is what a brick opening the run checks, and only that
        RunInputs.open(output)
    except (RunInputsError, ManifestFormatError, OSError) as exc:
        return str(exc)
    return ""


__all__ = ["WHAT", "EarlierWork", "earlier_work", "run_mismatch"]
