"""Read each file that needs new rows, or resume its staged rows (design v0.4 §1, PP-12).

Every file a run must compute rows for is either **resumed** (its rows staged by an
interrupted run, with the same row key: :mod:`.staging`) or **read**, and its rows staged
as soon as it is read. Whether a file stayed the file the run checked is decided by its
fingerprint (issue #90): its manifest entry records the fingerprint taken when its header
was read (between two fingerprints that must agree), and the file must still have it just
after its pixels are read. A file that changed is never staged or published: it is listed, and the
run stops with ``source_changed_during_run`` (:func:`changed_issue`). In a ``verify_content``
run the row key also holds the file's content SHA-256, so staged rows are resumed only for the
same content, and a file read must still have that SHA-256 after its pixels are read.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from morphobricks_normalization.records import PlaneRecord

from ..axes import AxisReview
from ..fingerprint import Fingerprint, RowKey, current_fingerprint
from ..manifest import FileEntry, NarrowRange, RunSettings
from ..planes import PlaneReadError
from ..source_content import current_content
from ..validation import ValidationIssue, error, plural
from .preflight import Preflight
from .rows import _read_file
from .staging import discard_staged, load_staged, stage_file, staging_key

logger = logging.getLogger(__name__)

SOURCE_CHANGED = "source_changed_during_run"


@dataclass
class NewRows:
    """The rows of the files a run computed or resumed.

    Attributes
    ----------
    records, narrow
        Their rows and narrow-range checks.
    read
        The files whose pixels were read.
    resumed
        The files whose staged rows were reused.
    failed
        The files whose pixels could not be read.
    changed
        The files that changed while the run checked or read them.
    discarded
        ``{file: why}`` for the staged rows not reused.
    """

    records: list[PlaneRecord] = field(default_factory=list)
    narrow: list[NarrowRange] = field(default_factory=list)
    read: list[str] = field(default_factory=list)
    resumed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    discarded: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FileJob:
    """What a run needs to compute the rows of its files.

    Attributes
    ----------
    pre, review
        The run's preflight and axis review.
    settings
        The run's settings.
    entries
        Every analysed file's manifest entry, as it is now.
    output
        The output folder (holding the staging folder).
    full
        A ``full`` run: no staged row is reused.
    """

    pre: Preflight
    review: AxisReview
    settings: RunSettings
    entries: Mapping[str, FileEntry]
    output: Path
    full: bool


def changed_during_check(job: FileJob) -> list[str]:
    """Return the files whose entry's fingerprint is not the one taken with their header.

    Parameters
    ----------
    job
        The run's files.

    Returns
    -------
    list of str
        The files whose header is not known to describe them as fingerprinted (they
        changed, or went missing, while the run checked them), sorted.
    """
    bound = job.pre.header_fingerprints
    return sorted(
        rel
        for rel, entry in job.entries.items()
        if bound and bound.get(rel) != Fingerprint.recorded(entry)
    )


def new_rows(
    job: FileJob,
    to_read: list[str],
    keep: set[str],
    progress: Callable[[int, int, str], None] | None,
) -> NewRows:
    """Resume or read every file of ``to_read``, staging the rows of each file read.

    Parameters
    ----------
    job
        The run's files.
    to_read
        The files that need new rows, sorted.
    keep
        The files whose rows the earlier table gives: staged rows of these are discarded.
    progress
        As :func:`.pipeline.run_experiment`.

    Returns
    -------
    NewRows
    """
    found = NewRows()
    staged, found.discarded = load_staged(job.output)
    for rel in sorted(set(staged) - set(to_read)):
        found.discarded[rel] = (
            "the table already holds its rows" if rel in keep else "the image is not analysed"
        )
        discard_staged(job.output, rel)
    dtype = job.pre.metadata.dtype.name
    root = job.pre.discovery.root
    for done, rel in enumerate(to_read):
        if progress is not None:
            progress(done, len(to_read), rel)
        key = staging_key(RowKey.of(job.entries[rel], job.settings, root.as_posix()), dtype)
        earlier = staged.get(rel)
        if earlier is not None:
            now = current_fingerprint(root / rel)
            unchanged = now == Fingerprint.recorded(job.entries[rel])
            if not job.full and earlier.key == key and unchanged:
                found.records.extend(earlier.records)
                found.narrow.extend(earlier.narrow)
                found.resumed.append(rel)
                continue
            found.discarded[rel] = _why_discarded(earlier.key, key, job.full, unchanged)
            discard_staged(job.output, rel)
        _read_one(job, rel, key, found)
    if progress is not None:
        progress(len(to_read), len(to_read), "")
    return found


def _read_one(job: FileJob, rel: str, key: dict, found: NewRows) -> None:
    """Read one file between two fingerprints, and stage its rows when it did not change."""
    root = job.pre.discovery.root
    # The entry's fingerprint is the one taken with the header: a file changed since then
    # (before or during the read) has another one after the read.
    expected = Fingerprint.recorded(job.entries[rel])
    labels = job.entries[rel].labels
    try:
        rows, ranges = _read_file(
            root / rel,
            rel,
            labels,
            job.review.file_mappings[rel],
            job.settings,
            job.entries[rel].position_id,
        )
    except PlaneReadError:
        logger.warning("Cannot read the planes of %s", rel, exc_info=True)
        found.failed.append(rel)
        return
    if current_fingerprint(root / rel) != expected or (
        job.settings.verify_content
        and current_content(root / rel) != job.entries[rel].content_sha256
    ):
        found.changed.append(rel)
        return
    stage_file(job.output, key, rows, ranges)
    found.records.extend(rows)
    found.narrow.extend(ranges)
    found.read.append(rel)


def changed_since(entries: Mapping[str, FileEntry], root: Path) -> list[str]:
    """Return the images whose fingerprint now is not their entry's (gone ones included).

    Parameters
    ----------
    entries
        The run's manifest entries, as fingerprinted when the run checked the images.
    root
        The experiment folder.

    Returns
    -------
    list of str
        The changed or missing images, sorted.
    """
    return sorted(
        rel
        for rel, entry in entries.items()
        if current_fingerprint(root / rel) != Fingerprint.recorded(entry)
    )


def _why_discarded(staged: dict, now: dict, full: bool, unchanged: bool) -> str:
    """Say why a file's staged rows are not reused (``unchanged``: its fingerprint now)."""
    if full:
        return "a full run reuses no staged row"
    if (
        not unchanged
        or staged.get("row_key", {}).get("fingerprint") != now["row_key"]["fingerprint"]
    ):
        return "the image changed since its rows were staged"
    content = (
        staged.get("row_key", {}).get("content_sha256"),
        now["row_key"].get("content_sha256"),
    )
    if content[0] != content[1]:
        if None in content:
            return "content verification (verify_content) was turned on or off since then"
        return "the image's content (SHA-256) changed since its rows were staged"
    if {k: staged.get(k) for k in ("versions", "schema_version")} != {
        k: now[k] for k in ("versions", "schema_version")
    }:
        return "its rows were staged by another version"
    if staged.get("dtype") != now["dtype"]:
        return "the experiment's pixel type changed since its rows were staged"
    return "its axis roles, labels, folder or row settings changed since its rows were staged"


def changed_issue(paths: list[str]) -> ValidationIssue:
    """Return the error naming the images that changed while the run checked or read them.

    Parameters
    ----------
    paths
        The images, sorted.

    Returns
    -------
    ValidationIssue
        ``source_changed_during_run``.
    """
    return error(
        SOURCE_CHANGED,
        f"{plural(len(paths), 'image')} changed or went missing while the run was checking "
        "or reading them (their size, time or files are no longer those the run started "
        "with), so their rows would not describe them. Nothing was published.",
        paths,
        "Wait until nothing is writing to these images (an acquisition or a copy still "
        "running), then run again: the images already read resume from their staged rows.",
    )
