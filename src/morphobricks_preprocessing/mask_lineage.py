"""The mask conversion helpers' lineage: records, digests, publication (Spec ADR 0005 BM12).

Every mask file written by ``RunInputs.binary_to_instances`` or ``RunInputs.classes_to_binary``
(:mod:`.mask_conversion`) has one line in ``mask_lineage.jsonl`` in its mask-type folder of the
masks folder. A line holds exactly the keys of :data:`LINE_KEYS`, in that order; its ``source``
and ``output`` hold :data:`FILE_KEYS`, with ``root`` always ``"masks"`` (paths relative to the
masks folder, with ``/``). :func:`content_sha256` is the digest of a canonical header (sorted-key
compact JSON of ``axes``, ``dtype`` and ``shape``, then one newline byte) followed by the pixel
bytes in stored axis order, C order and little-endian dtype: never the file's bytes and never
the run's fingerprint.

:func:`publish_output` publishes an output file and its line in three steps; the publication is
reader-consistent with rollback, not atomic: (1) the pixels go to a temporary file in the target
folder, synced and re-read to check their digest; (2) ``mask_lineage.jsonl`` is replaced by a
synced temporary file holding its previous lines plus the new one; (3) the temporary output is
renamed to its final name. A failure in (1) or (2) leaves no output, no line and no temporary
file; a failure raised in (3) restores the previous lines (the rollback). A process that dies
between (2) and (3) leaves a line whose output file does not exist: it is **pending**, and
readers stay consistent because :func:`published_lines` leaves it out and
:func:`roll_back_pending` removes it before the next publication of that output. Mask discovery
skips the lineage file and the temporary files (:func:`is_conversion_file`).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tifffile

from ._io import give_output_mode, write_text_atomic

LINEAGE_FILENAME = "mask_lineage.jsonl"
"""The lineage file of one mask-type folder: one JSON line per output file."""
LINEAGE_ROOT = "masks"
"""The only ``root`` of this version: the run's standalone masks folder."""
TEMP_SUFFIX = ".conversion.tmp"
"""The ending of the helpers' temporary files (their names also start with a dot)."""
LINE_KEYS = (
    "source",
    "output",
    "operation",
    "connectivity",
    "class_value",
    "helper",
    "helper_version",
)
FILE_KEYS = ("root", "mask_id", "path", "axes", "shape", "dtype", "content_sha256")


@dataclass(frozen=True)
class LineageLine:
    """One line of a lineage file: its text as stored and its parsed record."""

    text: str
    record: Mapping[str, object]

    @property
    def output_path(self) -> str:
        """The output file's path, relative to the masks folder."""
        return str(self.record["output"]["path"])


def is_conversion_file(name: str) -> bool:
    """Return True for the lineage file and the helpers' temporary files (never masks)."""
    return name == LINEAGE_FILENAME or (name.startswith(".") and name.endswith(TEMP_SUFFIX))


def little_endian(dtype: object) -> str:
    """Return a dtype's NumPy string in little-endian form (``"|u1"``, ``"<i4"``, ``"|b1"``)."""
    return np.dtype(dtype).newbyteorder("<").str


def content_sha256(pixels: np.ndarray, *, axes: str) -> str:
    """Return the lineage digest of a file's pixels, as stored with ``axes``.

    Parameters
    ----------
    pixels
        Every plane of the file as the reader returns it, in the stored axis order.
    axes
        The stored axis letters, one per dimension of ``pixels`` (``"YX"``, ``"TYX"``).

    Returns
    -------
    str
        The lower-case hex SHA-256 of the canonical header and the pixel bytes, so equal pixel
        bytes with another shape, axes or dtype give another digest.

    Raises
    ------
    ValueError
        When ``axes`` does not have one letter per dimension.
    """
    if len(axes) != pixels.ndim:
        raise ValueError(f"the axes {axes!r} do not name the {pixels.ndim} dimensions")
    dtype = little_endian(pixels.dtype)
    header = json.dumps(
        {"axes": axes, "dtype": dtype, "shape": list(pixels.shape)},
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(header.encode("utf-8") + b"\n")
    digest.update(np.ascontiguousarray(pixels, dtype=np.dtype(dtype)).tobytes())
    return digest.hexdigest()


def file_record(mask_id: str, path: str, axes: str, pixels: np.ndarray) -> dict:
    """Return the ``source`` or ``output`` object of a lineage line, keys in order."""
    return {
        "root": LINEAGE_ROOT,
        "mask_id": mask_id,
        "path": path,
        "axes": axes,
        "shape": [int(n) for n in pixels.shape],
        "dtype": little_endian(pixels.dtype),
        "content_sha256": content_sha256(pixels, axes=axes),
    }


def line_text(record: Mapping[str, object]) -> str:
    """Return a lineage record as its one line of JSON, with the newline."""
    return json.dumps(dict(record), ensure_ascii=False) + "\n"


def read_lineage(folder: Path) -> tuple[LineageLine, ...]:
    """Return every line of a mask-type folder's lineage file, pending ones included.

    Raises
    ------
    ValueError
        For a line that is not a lineage record (other keys, another key order), or whose
        ``source`` or ``output`` has a ``root`` other than ``"masks"``: it is named, never
        skipped.
    """
    path = Path(folder) / LINEAGE_FILENAME
    if not path.is_file():
        return ()
    lines = []
    texts = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for number, text in enumerate(texts, start=1):
        try:
            record = json.loads(text)
        except ValueError:
            record = None
        if not isinstance(record, dict) or tuple(record) != LINE_KEYS:
            raise ValueError(f"{path} line {number} is not a mask lineage record")
        for side in ("source", "output"):
            item = record[side]
            if not isinstance(item, dict) or tuple(item) != FILE_KEYS:
                raise ValueError(f"{path} line {number} has no complete {side} record")
            if item["root"] != LINEAGE_ROOT:
                raise ValueError(
                    f"{path} line {number} has the {side} root {item['root']!r}; a lineage "
                    f"path is relative to the masks folder ({LINEAGE_ROOT!r}) only"
                )
        lines.append(LineageLine(text, record))
    return tuple(lines)


def published_lines(masks_root: Path, folder: Path) -> tuple[LineageLine, ...]:
    """Return a folder's lineage lines whose output file exists (pending lines left out)."""
    return tuple(line for line in read_lineage(folder) if (masks_root / line.output_path).is_file())


def conversion_outputs(
    masks_root: Path | None, paths: Iterable[str], recorded_types: Collection[str]
) -> set[str]:
    """Return the masks-folder paths that are published outputs of a mask type not recorded.

    A path counts when its mask-type folder (its first part), case-folded, is not in
    ``recorded_types`` and that folder's lineage file has a published line naming it as its
    output. A lineage file that cannot be read counts for nothing (its files stay listed).
    """
    if masks_root is None:
        return set()
    found: set[str] = set()
    by_type: dict[str, set[str]] = {}
    for path in paths:
        folder = path.split("/", 1)[0]
        if "/" not in path or folder.casefold() in recorded_types:
            continue
        if folder not in by_type:
            try:
                lines = published_lines(masks_root, masks_root / folder)
            except (OSError, ValueError):
                lines = ()
            by_type[folder] = {line.output_path for line in lines}
        if path in by_type[folder]:
            found.add(path)
    return found


def roll_back_pending(masks_root: Path, relative: str) -> None:
    """Remove a pending line of one output and its leftover temporary files.

    The line was published but the process stopped before its output was renamed into place;
    removing it gives the same files and lines as a publication that never started.
    """
    target = masks_root / relative
    folder = masks_root / relative.split("/", 1)[0]
    if target.is_file():
        return
    lines = read_lineage(folder)
    kept = [line.text for line in lines if line.output_path != relative]
    if len(kept) != len(lines):
        _write_lines(folder / LINEAGE_FILENAME, "".join(kept) if kept else None)
    if target.parent.is_dir():
        for leftover in target.parent.glob(f".{target.name}.*{TEMP_SUFFIX}"):
            leftover.unlink(missing_ok=True)


def read_written(path: Path, axes: str) -> np.ndarray:
    """Return the pixels of a file the helpers wrote, as stored (every plane).

    Raises
    ------
    ValueError
        When the file does not hold one array with one dimension per axis.
    """
    pixels = tifffile.imread(path)
    if pixels.ndim != len(axes):
        raise ValueError(f"{path} does not hold the {len(axes)}-D {axes} array it was given")
    return pixels


def publish_output(
    masks_root: Path, relative: str, pixels: np.ndarray, record: Mapping[str, object]
) -> None:
    """Publish one output file and its lineage line (steps (1)-(3) above).

    The publication is reader-consistent with rollback, not atomic: a failure raised in any
    step leaves nothing published, and a line left by a process that died between (2) and (3)
    is pending (ignored by readers, removed by the next publication of that output).

    Parameters
    ----------
    masks_root
        The masks folder.
    relative
        The output's path relative to it, ``<mask ID>/<folders>/<name>.tif``.
    pixels
        The array to write, in the stored axis order ``record["output"]["axes"]``.
    record
        The lineage record; its ``output.content_sha256`` must be the digest of ``pixels``.

    Raises
    ------
    ValueError
        When the written file does not re-read to the record's digest, or the target appeared
        meanwhile; nothing is left published.
    OSError
        From the file system; nothing is left published.
    """
    target = masks_root / relative
    lineage = masks_root / relative.split("/", 1)[0] / LINEAGE_FILENAME
    axes = str(record["output"]["axes"])
    created = _make_folders(target.parent, masks_root)
    previous = lineage.read_text(encoding="utf-8") if lineage.is_file() else None
    temporary = None
    try:
        temporary = _write_temporary(target, pixels, axes)  # (1)
        if (
            content_sha256(read_written(temporary, axes), axes=axes)
            != (record["output"]["content_sha256"])
        ):
            raise ValueError(f"{relative} did not re-read as written; nothing was published")
        _write_lines(lineage, (previous or "") + line_text(record))  # (2)
        try:
            if target.exists():
                raise ValueError(f"{relative} appeared while it was written; nothing is replaced")
            os.replace(temporary, target)  # (3)
        except BaseException:
            _write_lines(lineage, previous)
            raise
    except BaseException:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
        for folder in reversed(created):
            _remove_empty(folder)
        raise


def _write_temporary(target: Path, pixels: np.ndarray, axes: str) -> Path:
    """Write the pixels to a synced temporary file next to ``target``; return its path."""
    handle, name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=TEMP_SUFFIX, dir=target.parent
    )
    os.close(handle)
    try:
        tifffile.imwrite(name, pixels, photometric="minisblack", metadata={"axes": axes})
        with open(name, "r+b") as stream:
            os.fsync(stream.fileno())
        give_output_mode(name, target)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name)


def _write_lines(lineage: Path, text: str | None) -> None:
    """Replace the lineage file by ``text`` (synced), or remove it for None."""
    if text is None:
        lineage.unlink(missing_ok=True)
    else:
        write_text_atomic(lineage, text, sync=True)


def _make_folders(folder: Path, masks_root: Path) -> list[Path]:
    """Create ``folder`` and its missing parents below the masks folder; return those created."""
    missing = []
    current = folder
    while current != masks_root and not current.exists():
        missing.append(current)
        current = current.parent
    for item in reversed(missing):
        item.mkdir()
    return list(reversed(missing))


def _remove_empty(folder: Path) -> None:
    """Remove a folder this publication created, if it is still empty."""
    try:
        folder.rmdir()
    except OSError:
        pass
