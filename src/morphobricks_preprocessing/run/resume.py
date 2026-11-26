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

Files may be read in worker threads, one file per worker (issue #107, :mod:`.workers`): a
worker only reads its file and checks it after the read (:func:`_read_checked`); the calling
thread alone resumes, stages, aggregates (in the sorted order of the files, whatever order the
workers finish in) and reports progress.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from morphobricks_normalization.records import PlaneRecord

from ..axes import AxisReview
from ..fingerprint import Fingerprint, RowKey, current_fingerprint
from ..image_qc_table import QcRow
from ..manifest import FileEntry, NarrowRange, RunSettings
from ..planes import PlaneReadError
from ..source_content import current_content
from ..validation import ValidationIssue, error, plural
from .preflight import Preflight
from .rows import ChannelReadError, _read_file
from .staging import StagedFile, discard_staged, load_staged, stage_file, staging_key
from .workers import Workers, plan_workers

logger = logging.getLogger(__name__)

SOURCE_CHANGED = "source_changed_during_run"


@dataclass
class NewRows:
    """The rows of the files a run computed or resumed.

    Attributes
    ----------
    records, narrow
        Their rows and narrow-range checks.
    qc
        The image QC rows of their channel planes (contract §19).
    read
        The files whose pixels were read.
    resumed
        The files whose staged rows were reused.
    failed
        The files whose pixels could not be read.
    failures
        ``{file: what failed}`` for each failed file: every plane and channel that could not
        be measured, with the cause (Spec ADR 0003 HV3), or the file and the cause.
    changed
        The files that changed while the run checked or read them.
    discarded
        ``{file: why}`` for the staged rows not reused.
    workers
        The size of the thread pool the files were read with, ``min(planned, files to read)``
        (its capacity, not a count of the threads used; 1: in the calling thread).
    refused
        ``image_over_max_ram`` when no file was read because one cannot be read within
        ``max_ram`` (:func:`.workers.plan_workers`), else None.
    """

    records: list[PlaneRecord] = field(default_factory=list)
    narrow: list[NarrowRange] = field(default_factory=list)
    qc: list[QcRow] = field(default_factory=list)
    read: list[str] = field(default_factory=list)
    resumed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    failures: dict[str, tuple[str, ...]] = field(default_factory=dict)
    changed: list[str] = field(default_factory=list)
    discarded: dict[str, str] = field(default_factory=dict)
    workers: int = 1
    refused: ValidationIssue | None = None


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
    workers: Workers | None = None,
) -> NewRows:
    """Resume or read every file of ``to_read``, staging the rows of each file read.

    With one worker (the default) each file is resumed or read in turn in the calling
    thread. With more (issue #107), the staged rows are resumed first, then each file left
    is read in a worker thread (:func:`_read_checked`: its read and its post-read checks
    only), at most :func:`.workers.plan_workers` at once; this thread alone stages each
    file's rows as soon as its worker returns, aggregates them in the order of ``to_read``
    and calls ``progress``. A worker's exception stops the run: the files not yet read are
    cancelled, and the rows already staged are resumed by the next run.

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
    workers
        How many files may be read at once (:class:`.workers.Workers`); None: one.

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
    workers = workers or Workers()
    # The pixel type once: MetadataReport.dtype looks at every header (O(n) per call).
    dtype = job.pre.metadata.dtype.name
    keys = {rel: _staging_key(job, rel, dtype) for rel in to_read}
    if workers.count == 1:
        for done, rel in enumerate(to_read):
            if progress is not None:
                progress(done, len(to_read), rel)
            if not _resumed(job, rel, keys[rel], staged.get(rel), found):
                _take(job, keys[rel], _read_checked(job, rel), found)
    else:
        _read_in_workers(job, to_read, keys, staged, found, (workers, progress))
    if progress is not None:
        progress(len(to_read), len(to_read), "")
    return found


def _staging_key(job: FileJob, rel: str, dtype: str) -> dict:
    """Return the staging key of one file's rows in this run (``dtype``: the pixel type's name)."""
    root = job.pre.discovery.root.as_posix()
    return staging_key(RowKey.of(job.entries[rel], job.settings, root), dtype)


def _resumed(job: FileJob, rel: str, key: dict, earlier: StagedFile | None, found: NewRows) -> bool:
    """Resume a file's staged rows when they are still valid; discard them otherwise."""
    if earlier is None:
        return False
    now = current_fingerprint(job.pre.discovery.root / rel)
    unchanged = now == Fingerprint.recorded(job.entries[rel])
    if not job.full and earlier.key == key and unchanged:
        found.records.extend(earlier.records)
        found.narrow.extend(earlier.narrow)
        found.qc.extend(earlier.qc)
        found.resumed.append(rel)
        return True
    found.discarded[rel] = _why_discarded(earlier.key, key, job.full, unchanged)
    discard_staged(job.output, rel)
    return False


def _read_in_workers(
    job: FileJob,
    to_read: list[str],
    keys: Mapping[str, dict],
    staged: Mapping[str, StagedFile],
    found: NewRows,
    how: tuple[Workers, Callable[[int, int, str], None] | None],
) -> None:
    """Resume the staged files, then read the others in worker threads (issue #107).

    ``progress`` (in ``how``) is called in this thread as each file is done, resumed or
    read, with the number of files done before it. Nothing is read when the plan refuses.
    """
    workers, progress = how
    total, done = len(to_read), 0
    pending = []
    for rel in to_read:
        if _resumed(job, rel, keys[rel], staged.get(rel), found):
            if progress is not None:
                progress(done, total, rel)
            done += 1
        else:
            pending.append(rel)
    mappings = job.review.file_mappings
    plan = plan_workers(workers, job.pre.discovery.root, pending, mappings, job.settings)
    found.refused = plan.refused
    if plan.refused is not None or not pending:
        return  # no image read: found.workers stays 1
    # No larger pool than images to read: the number reported is the pool's capacity (the
    # most images read at once), not a count of the distinct threads the pool started.
    found.workers = min(plan.count, len(pending))
    logger.info("Reading %d images with %d workers", len(pending), found.workers)
    results: dict[str, FileRead] = {}
    pool = ThreadPoolExecutor(found.workers, thread_name_prefix="morphobricks-read")
    try:
        futures = {pool.submit(_read_checked, job, rel): rel for rel in pending}
        for future in as_completed(futures):
            result = future.result()  # a worker's exception is raised here, in this thread
            if result.status == READ:
                stage_file(
                    job.output, keys[result.relative_path], result.rows, result.ranges, result.qc
                )
            results[result.relative_path] = result
            if progress is not None:
                progress(done, total, result.relative_path)
            done += 1
    finally:
        # Files not yet started are cancelled; the ones being read are waited for.
        pool.shutdown(wait=True, cancel_futures=True)
    for rel in pending:  # the order of to_read, whatever order the workers finished in
        _add(results[rel], found)


READ, FAILED, CHANGED = "read", "failed", "changed"


@dataclass(frozen=True)
class FileRead:
    """What a worker returns for one file (issue #107).

    Attributes
    ----------
    relative_path
        The file.
    status
        ``"read"``, ``"failed"`` (its pixels could not be read) or ``"changed"`` (it changed
        while it was read: never staged or published).
    rows, ranges, qc
        Its rows, narrow-range checks and image QC rows; empty unless read.
    failures
        When failed: each plane and channel that could not be measured, with the cause, or
        the file and the cause when it could not be read plane by plane.
    """

    relative_path: str
    status: str
    rows: tuple[PlaneRecord, ...] = ()
    ranges: tuple[NarrowRange, ...] = ()
    failures: tuple[str, ...] = ()
    qc: tuple[QcRow, ...] = ()


def _read_checked(job: FileJob, rel: str) -> FileRead:
    """Read one file between two fingerprints; change no state of the run (a worker's job).

    An exception other than :class:`..planes.PlaneReadError` stops the run as it is, with a
    note naming the image: from a worker thread, the traceback alone does not say which.
    """
    try:
        return _read_between_fingerprints(job, rel)
    except Exception as exc:
        exc.add_note(f"While reading the image {rel}")
        raise


def _read_between_fingerprints(job: FileJob, rel: str) -> FileRead:
    """Read one file and check that it did not change while it was read."""
    root = job.pre.discovery.root
    # The entry's fingerprint is the one taken with the header: a file changed since then
    # (before or during the read) has another one after the read.
    expected = Fingerprint.recorded(job.entries[rel])
    labels = job.entries[rel].labels
    try:
        rows, ranges, qc = _read_file(
            root / rel,
            rel,
            labels,
            job.review.file_mappings[rel],
            job.settings,
            job.entries[rel].position_id,
            job.entries[rel].channel_ids,
        )
    except ChannelReadError as exc:
        logger.warning("Cannot read the planes of %s", rel, exc_info=True)
        return FileRead(rel, FAILED, failures=tuple(f.text(rel) for f in exc.failures))
    except PlaneReadError as exc:
        logger.warning("Cannot read the planes of %s", rel, exc_info=True)
        return FileRead(rel, FAILED, failures=(f"{rel}: {str(exc).replace(str(root / rel), rel)}",))
    if current_fingerprint(root / rel) != expected or (
        job.settings.verify_content
        and current_content(root / rel) != job.entries[rel].content_sha256
    ):
        return FileRead(rel, CHANGED)
    return FileRead(rel, READ, tuple(rows), tuple(ranges), qc=tuple(qc))


def _take(job: FileJob, key: dict, result: FileRead, found: NewRows) -> None:
    """Stage a file's rows when it was read, and add what happened to ``found``."""
    if result.status == READ:
        stage_file(job.output, key, result.rows, result.ranges, result.qc)
    _add(result, found)


def _add(result: FileRead, found: NewRows) -> None:
    """Add one file's result to ``found`` (its rows when read, else its name in a list)."""
    rel = result.relative_path
    if result.status == FAILED:
        found.failed.append(rel)
        found.failures[rel] = result.failures
    elif result.status == CHANGED:
        found.changed.append(rel)
    else:
        found.records.extend(result.rows)
        found.narrow.extend(result.ranges)
        found.qc.extend(result.qc)
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
    limits = (staged.get("row_key", {}).get("block_limit"), now["row_key"].get("block_limit"))
    if limits[0] != limits[1]:
        return "its planes' block layout (the memory limit) changed since its rows were staged"
    return "its axis roles, labels, folder or row settings changed since its rows were staged"


CHANGE_TEXTS = {
    "fingerprint": "their size, time or files are no longer those the run started with",
    "fingerprint or content": (
        "their size, time, files or content (SHA-256) are no longer those the run started with"
    ),
    "content": (
        "their content (SHA-256) is no longer the one the run checked, even if their size "
        "and time are"
    ),
}
"""What changed, by what the run compared (:func:`changed_issue`)."""


def changed_issue(paths: list[str], compared: str = "fingerprint") -> ValidationIssue:
    """Return the error naming the images that changed while the run checked or read them.

    Parameters
    ----------
    paths
        The images, sorted.
    compared
        What was compared, a key of :data:`CHANGE_TEXTS`: ``"fingerprint"`` (size, time,
        files), ``"fingerprint or content"`` (an image read in a ``verify_content`` run) or
        ``"content"`` (a ``verify_content`` run's last check before the commit).

    Returns
    -------
    ValidationIssue
        ``source_changed_during_run``.
    """
    return error(
        SOURCE_CHANGED,
        f"{plural(len(paths), 'image')} changed or went missing while the run was checking "
        f"or reading them ({CHANGE_TEXTS[compared]}), so their rows would not describe "
        "them. Nothing was published.",
        paths,
        "Wait until nothing is writing to these images (an acquisition or a copy still "
        "running), then run again: the images already read resume from their staged rows.",
    )
