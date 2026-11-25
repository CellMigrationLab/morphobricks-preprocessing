"""The headers a folder check read, kept so the notebook can restore the check (v0.9.0).

The notebook's folder check binds every image header to the file's fingerprint
(:attr:`.run.Preflight.header_fingerprints`). It keeps them in ``check_headers.json`` in the
output folder, next to its report, so that when the output folder is chosen again the check
is restored without reading again the header of an image that did not change
(:func:`.run.preflight.preflight` with ``recorded``; owner decision of 25 November 2026).

A recorded header is used only when everything that made it is known to be the same:

- the experiment folder (the record's ``experiment_root``, resolved);
- the code that read it: the versions of :func:`.versions.package_versions` and zarr, and
  this file's format; the fields of :class:`.metadata.TiffMetadata` and
  :class:`.physical_sizes.PhysicalSizes` must be exactly those written;
- the file: its fingerprint now equals the one it had while its header was read (an image
  whose fingerprint changed during the read was never recorded).

Anything else, or a record that cannot be read, means no header is reused: the check reads
every header, and the notebook says why. It is a cache for the notebook only: bricks never
read it, and the run reads every header itself.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import MappingProxyType

import numpy as np

from ._io import write_text_atomic
from .fingerprint import Fingerprint
from .metadata import TiffMetadata
from .physical_sizes import PhysicalSizes
from .versions import package_versions

CHECK_RECORD_FILENAME = "check_headers.json"
"""The record of the headers the notebook's last folder check read."""
RECORD_KIND = "morphobricks-preprocessing check headers"
RECORD_FORMAT = 1

_HEADER_FIELDS = tuple(f.name for f in fields(TiffMetadata))
_SIZE_FIELDS = tuple(f.name for f in fields(PhysicalSizes))


class CheckRecordError(ValueError):
    """A ``check_headers.json`` that cannot be used; the message says why, in plain words."""


@dataclass(frozen=True)
class CheckRecord:
    """The headers a folder check read, each with the fingerprint it was read at.

    Attributes
    ----------
    experiment_root
        The experiment folder checked, resolved.
    written_at
        When the record was written (UTC, ISO 8601).
    versions
        The versions of the code that read the headers (:func:`reader_versions`).
    headers
        ``{relative path: (fingerprint, header)}``.
    """

    experiment_root: str
    written_at: str
    versions: Mapping[str, str]
    headers: Mapping[str, tuple[Fingerprint, TiffMetadata]]

    def problem(self, root: Path) -> str:
        """Return why the record cannot be used for the experiment at ``root`` ("" if it can)."""
        if self.experiment_root != str(Path(root).resolve()):
            return f"it is of another experiment folder ({self.experiment_root})"
        if dict(self.versions) != reader_versions():
            return "it was written by another version of the software"
        return ""


def reader_versions() -> dict[str, str]:
    """Return the versions of the code that reads the headers, with zarr's (or "absent")."""
    found = package_versions()
    try:
        found["zarr"] = version("zarr")
    except PackageNotFoundError:
        found["zarr"] = "absent"
    return found


def write_check_record(
    output: Path, root: Path, fingerprints: Mapping[str, Fingerprint | None], files: tuple
) -> Path:
    """Write the headers of ``files`` bound to their fingerprint to ``output`` (atomically).

    An image whose fingerprint is None (it changed while its header was read) is left out,
    and so is one whose header cannot be written exactly (a size that is not finite).

    Parameters
    ----------
    output
        The output folder.
    root
        The experiment folder checked.
    fingerprints
        :attr:`.run.Preflight.header_fingerprints`.
    files
        The headers read (:attr:`.metadata.MetadataReport.files`).

    Returns
    -------
    Path
        The record written.
    """
    headers = {}
    for meta in files:
        bound = fingerprints.get(meta.relative_path)
        data = _header_data(meta)
        if bound is not None and data is not None:
            headers[meta.relative_path] = {"fingerprint": bound.to_data(), "header": data}
    record = {
        "record": RECORD_KIND,
        "format": RECORD_FORMAT,
        "experiment_root": str(Path(root).resolve()),
        "written_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "versions": reader_versions(),
        "headers": dict(sorted(headers.items())),
    }
    text = json.dumps(record, indent=1, sort_keys=False, allow_nan=False) + "\n"
    return write_text_atomic(output / CHECK_RECORD_FILENAME, text)


def read_check_record(output: Path) -> CheckRecord | None:
    """Return the record in ``output``, None when there is none.

    Raises
    ------
    CheckRecordError
        When the file cannot be read or is not a record this version writes.
    """
    path = output / CHECK_RECORD_FILENAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as exc:
        raise CheckRecordError(f"{CHECK_RECORD_FILENAME} cannot be read ({exc})") from exc
    try:
        data = json.loads(text)
        return _record(data)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise CheckRecordError(f"{CHECK_RECORD_FILENAME} is not a usable record ({exc})") from exc


def _record(data: dict) -> CheckRecord:
    """Return the record of the JSON ``data``; raise ValueError for anything unexpected."""
    if data.get("record") != RECORD_KIND or data.get("format") != RECORD_FORMAT:
        raise ValueError("another kind or format of file")
    headers = {}
    for rel, entry in data["headers"].items():
        header = _header(entry["header"])
        if header.relative_path != rel:
            raise ValueError(f"the header of {rel} names {header.relative_path}")
        headers[rel] = (Fingerprint.recorded(entry["fingerprint"]), header)
    versions = {str(k): str(v) for k, v in data["versions"].items()}
    return CheckRecord(
        str(data["experiment_root"]),
        str(data["written_at"]),
        MappingProxyType(versions),
        MappingProxyType(headers),
    )


def _header_data(meta: TiffMetadata) -> dict | None:
    """Return ``meta`` as JSON data, every field by name; None when a size is not finite."""
    data = {name: getattr(meta, name) for name in _HEADER_FIELDS}
    data["shape"] = list(meta.shape)
    data["dtype"] = None if meta.dtype is None else meta.dtype.str
    data["channel_names"] = list(meta.channel_names)
    data["raw_channel_names"] = list(meta.raw_channel_names)
    sizes = {name: getattr(meta.physical_sizes, name) for name in _SIZE_FIELDS}
    sizes["unsupported_units"] = [list(pair) for pair in meta.physical_sizes.unsupported_units]
    if any(isinstance(v, float) and not math.isfinite(v) for v in sizes.values()):
        return None
    data["physical_sizes"] = sizes
    return data


def _header(data: dict) -> TiffMetadata:
    """Return the header written by :func:`_header_data`; raise ValueError if not exactly it."""
    if tuple(data) != _HEADER_FIELDS or tuple(data["physical_sizes"]) != _SIZE_FIELDS:
        raise ValueError("the header fields are not those of this version")
    sizes = dict(data["physical_sizes"])
    sizes["unsupported_units"] = tuple(tuple(pair) for pair in sizes["unsupported_units"])
    values = dict(data)
    values["shape"] = tuple(int(n) for n in data["shape"])
    values["dtype"] = None if data["dtype"] is None else np.dtype(data["dtype"])
    values["channel_names"] = tuple(data["channel_names"])
    values["raw_channel_names"] = tuple(data["raw_channel_names"])
    values["physical_sizes"] = PhysicalSizes(**sizes)
    return TiffMetadata(**values)


__all__ = [
    "CHECK_RECORD_FILENAME",
    "CheckRecord",
    "CheckRecordError",
    "read_check_record",
    "reader_versions",
    "write_check_record",
]
