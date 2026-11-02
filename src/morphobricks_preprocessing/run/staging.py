"""Per-file staging of a run's rows, so an interrupted run resumes (design v0.4 §1).

As soon as a run has read one file, its rows are staged in the output folder's staging
folder ``.staging``: the rows as a small ``plane_histograms.csv`` written with the
normalization brick's writer (the same writer and reader as the table, so a resumed row is
the row the run would compute), then a JSON record naming the file, its row key
(:class:`.fingerprint.RowKey`: fingerprint, labels, axes, roles, ``axes_from``, read path,
bins and histogram settings), the pixel type, the table's schema version, the versions of
the two packages that compute rows, the planes whose range check failed, and the SHA-256 of
the rows file. The record is written last, so a staged entry without one is incomplete and
never used.

The next run (after a crash, a kill or a lost Colab session) reuses a staged file's rows
only when everything its record names is what that run would read now; anything else is
discarded and reported, never reused. The staging folder is removed once a run publishes;
a run that stops keeps it, so the next one resumes. Publication itself stays atomic
(:mod:`.generation`), and a ``full`` run reuses no staged row.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from morphobricks_normalization.csv_io import (
    SCHEMA_VERSION,
    CsvFormatError,
    read_plane_histograms,
    write_plane_histograms,
)
from morphobricks_normalization.records import PlaneRecord

from .._io import write_text_atomic
from ..fingerprint import RowKey
from ..manifest import NarrowRange, file_sha256, package_versions

logger = logging.getLogger(__name__)

STAGING_FOLDER = ".staging"
"""The staging folder in the output folder (removed once a run publishes)."""
STAGING_VERSION = "1"
_ROW_PACKAGES = ("morphobricks-preprocessing", "morphobricks-normalization")
"""The packages whose versions a staged file's rows depend on."""


@dataclass(frozen=True)
class StagedFile:
    """One file's staged rows.

    Attributes
    ----------
    relative_path
        The image, relative to the experiment folder.
    key
        What the rows depend on (:func:`staging_key`), as JSON data.
    records
        Its rows, one per XYC plane.
    narrow
        Its channel planes whose range check found the central range too narrow.
    """

    relative_path: str
    key: dict
    records: tuple[PlaneRecord, ...]
    narrow: tuple[NarrowRange, ...]


def staging_key(key: RowKey, dtype: str) -> dict:
    """Return everything a file's staged rows depend on, as JSON data.

    Parameters
    ----------
    key
        The file's row key with the run's settings.
    dtype
        The experiment's pixel type.

    Returns
    -------
    dict
        The row key, the pixel type, the table's schema version and the versions of the
        packages that compute rows, normalised through JSON so it compares with a record.
    """
    data = {
        "row_key": asdict(key),
        "dtype": dtype,
        "schema_version": SCHEMA_VERSION,
        "versions": dict(_row_versions()),
    }
    return json.loads(json.dumps(data, sort_keys=True))


def stage_file(
    output: Path,
    key: dict,
    records: list[PlaneRecord],
    narrow: list[NarrowRange],
) -> None:
    """Stage one file's rows: its rows file first, then the record naming them.

    Parameters
    ----------
    output
        The output folder (its lock held).
    key
        :func:`staging_key` of the file as it was read.
    records, narrow
        Its rows and its narrow-range checks.
    """
    folder = output / STAGING_FOLDER
    folder.mkdir(exist_ok=True)
    relative = records[0].source_relative_path
    stem = _stem(relative)
    rows = write_plane_histograms(records, folder / f"{stem}.csv")
    record = {
        "staging_version": STAGING_VERSION,
        "relative_path": relative,
        "key": key,
        "rows_sha256": file_sha256(rows),
        "narrow_histogram_ranges": [entry.to_dict() for entry in narrow],
    }
    write_text_atomic(folder / f"{stem}.json", json.dumps(record, sort_keys=True) + "\n")


def load_staged(output: Path) -> tuple[dict[str, StagedFile], dict[str, str]]:
    """Read every staged file of an earlier, interrupted run.

    Parameters
    ----------
    output
        The output folder.

    Returns
    -------
    tuple
        ``({relative path: StagedFile}, {image: why it was discarded})`` (the record's
        file name when it does not name its image). A record that
        cannot be read, or whose rows file is missing or not the one it names, is
        discarded (and removed); a rows file without its record (a run killed between the
        two writes) is removed without a report: it was never staged.
    """
    folder = output / STAGING_FOLDER
    staged: dict[str, StagedFile] = {}
    discarded: dict[str, str] = {}
    if not folder.is_dir():
        return staged, discarded
    for path in sorted(folder.glob("*.json")):
        try:
            found = _read_staged(path)
        except (OSError, ValueError, KeyError, TypeError, CsvFormatError) as exc:
            logger.warning("Cannot read the staged rows %s", path, exc_info=True)
            discarded[_staged_name(path)] = f"its staged rows could not be read ({exc})"
            _remove(path)
            continue
        staged[found.relative_path] = found
    named = {_stem(rel) for rel in staged}
    for rows in folder.glob("*.csv"):
        if rows.stem not in named:
            _remove(rows.with_suffix(".json"))
    return staged, discarded


def discard_staged(output: Path, relative_path: str) -> None:
    """Remove one file's staged rows (record first, so nothing half-staged is left).

    Parameters
    ----------
    output
        The output folder.
    relative_path
        The image.
    """
    stem = output / STAGING_FOLDER / _stem(relative_path)
    stem.with_suffix(".json").unlink(missing_ok=True)
    stem.with_suffix(".csv").unlink(missing_ok=True)


def clear_staging(output: Path) -> None:
    """Remove the staging folder, once a run has published.

    Parameters
    ----------
    output
        The output folder.
    """
    folder = output / STAGING_FOLDER
    if not folder.exists():
        return
    try:
        shutil.rmtree(folder)
    except OSError:
        # Its rows are in the published table; the next run discards what is left.
        logger.warning("Cannot remove the staging folder %s", folder, exc_info=True)


@lru_cache(maxsize=1)
def _row_versions() -> tuple[tuple[str, str], ...]:
    """Return the versions of the packages that compute rows (read once per process)."""
    versions = package_versions()
    return tuple((name, versions[name]) for name in _ROW_PACKAGES)


def _read_staged(path: Path) -> StagedFile:
    """Read one staged record and its rows, checking the rows are the ones it names."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data["staging_version"] != STAGING_VERSION:
        raise ValueError(f"staging version {data['staging_version']!r}")
    relative = str(data["relative_path"])
    rows = path.with_suffix(".csv")
    if path.stem != _stem(relative) or file_sha256(rows) != data["rows_sha256"]:
        raise ValueError("its rows file is not the one it names")
    records = tuple(read_plane_histograms(rows))
    if not records or any(r.source_relative_path != relative for r in records):
        raise ValueError("its rows are not the file's")
    narrow = tuple(NarrowRange.from_dict(e) for e in data["narrow_histogram_ranges"])
    return StagedFile(relative, dict(data["key"]), records, narrow)


def _staged_name(record: Path) -> str:
    """Return the image a staged record names, or the record's file name when unreadable."""
    try:
        relative = json.loads(record.read_text(encoding="utf-8"))["relative_path"]
    except (OSError, ValueError, KeyError, TypeError):
        return record.name
    if isinstance(relative, str) and record.stem == _stem(relative):
        return relative
    return record.name


def _remove(record: Path) -> None:
    """Remove a staged record and its rows file."""
    record.unlink(missing_ok=True)
    record.with_suffix(".csv").unlink(missing_ok=True)


def _stem(relative_path: str) -> str:
    """Return the file name of a staged file (its path may hold folders and any letters)."""
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:32]
