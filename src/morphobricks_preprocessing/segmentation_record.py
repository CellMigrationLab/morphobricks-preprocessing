"""The provenance file of one mask type made by segmentation tools (contract §15.5).

A segmentation tool (Cellpose in the shipped notebook) writes its masks under
``<masks folder>/<mask type>/`` with :func:`.segmentation_handoff.write_mask`, and one
``segmentation_record.json`` beside them says how they were made. The file is an append-only
history of **batches**: each :class:`SegmentationBatch` holds the tool and its version, the
model (a name, and the weights file with its SHA-256), the parameters, the channel IDs read,
the device, the run read (folder and manifest SHA-256) and one entry per image (written,
skipped or failed, with the message and, for a written mask, the SHA-256 of the file). A
second batch with another model is added beside the first, never over it, so every mask keeps
the provenance of the batch that wrote it (the latest written entry of an image is the one
that holds). The file is provenance only: nothing in preprocessing reads it back to decide
anything, and a mask is a mask whether or not a record exists.

The record is versioned (:data:`RECORD_VERSION`). :func:`read_record` checks everything it
reads and refuses a version it does not know, an unknown key, a wrong type, a value outside
its allowed set, a batch ID twice and an image twice in one batch; nothing is repaired or
defaulted. A mask type is checked with the rules every brick applies to a name
(:func:`.discovery.check_identity_text`, NFC), before anything is written.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ._io import file_sha256, write_text_atomic
from .discovery import IdentityError, check_identity_text
from .lock_file import LockHeldError, exclusive_lock
from .name_issues import is_nfc

RECORD_VERSION = 1
"""The version of the record this module writes and reads."""
RECORD_FILENAME = "segmentation_record.json"
"""The record's name, directly inside the mask type's folder."""
RECORD_LOCK_FILENAME = ".segmentation_record.lock"
"""The lock a writer holds beside the record while it reads, changes and rewrites it."""
DEVICES = ("cuda", "cpu", "mps")
"""The device the tool ran on, always stated (no silent fallback)."""
STATUSES = ("written", "skipped", "failed")
"""What happened to one image."""
_SHA256 = re.compile(r"[0-9a-f]{64}")


class SegmentationError(ValueError):
    """A segmentation hand-off or record that is refused; the message says what to do."""


@dataclass(frozen=True)
class SegmentedImage:
    """What happened to one image in one batch.

    Attributes
    ----------
    image
        The image's path relative to the experiment, as the run's manifest records it.
    status
        ``written``, ``skipped`` (a mask was already there) or ``failed``.
    message
        Why, in plain words; required for ``failed`` (an error is never dropped).
    mask_sha256
        The SHA-256 of the mask file written (:func:`mask_sha256`): required for ``written``,
        None otherwise.
    """

    image: str
    status: str
    message: str = ""
    mask_sha256: str | None = None


@dataclass(frozen=True)
class SegmentationBatch:
    """One run of a segmentation tool over some images: how its masks were made.

    Attributes
    ----------
    batch_id
        The identifier of the batch, unique in the file. Make it with :func:`new_batch_id`; an
        identifier chosen by the caller (a start time to the second, say) must be unique too,
        and a repeated one is refused only when the record is written, after the masks.
    tool, tool_version
        The segmentation tool and its exact version (e.g. ``cellpose``, ``4.2.1.1``).
    model_name
        The model as the tool names it (e.g. ``cpsam_v2``), or the name the user gave a custom
        model.
    model_sha256
        The SHA-256 of the weights file, 64 lowercase hexadecimal characters.
    model_file
        The weights file's name or path, or None when the tool gives none.
    parameters
        The tool's parameters, a JSON object (diameter, thresholds, and so on).
    channel_ids
        The canonical channel IDs the tool read, in the order given to it.
    device
        ``cuda``, ``cpu`` or ``mps``: the device that ran, never inferred.
    run_folder
        The preprocessing run's output folder, as the user gave it.
    run_manifest_sha256
        The SHA-256 of that run's ``run_manifest.json`` when the images were read.
    images
        One entry per image handled, each image once.
    """

    batch_id: str
    tool: str
    tool_version: str
    model_name: str
    model_sha256: str
    parameters: dict[str, Any]
    channel_ids: tuple[str, ...]
    device: str
    run_folder: str
    run_manifest_sha256: str
    images: tuple[SegmentedImage, ...] = ()
    model_file: str | None = None

    def __post_init__(self) -> None:
        """Refuse a batch that could not be read back (the same checks as reading)."""
        _batch(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        """Return the batch as the JSON object kept in the file."""
        return {
            "batch_id": self.batch_id,
            "tool": self.tool,
            "tool_version": self.tool_version,
            "model": {
                "name": self.model_name,
                "file": self.model_file,
                "sha256": self.model_sha256,
            },
            "parameters": dict(self.parameters),
            "channel_ids": list(self.channel_ids),
            "device": self.device,
            "run_folder": self.run_folder,
            "run_manifest_sha256": self.run_manifest_sha256,
            "images": [
                {
                    "image": i.image,
                    "status": i.status,
                    "message": i.message,
                    "mask_sha256": i.mask_sha256,
                }
                for i in self.images
            ],
        }

    @classmethod
    def from_dict(cls, data: object) -> SegmentationBatch:
        """Build a batch from a JSON object, checking all of it (:class:`SegmentationError`)."""
        data = _batch(data)
        model = data["model"]
        return cls(
            batch_id=data["batch_id"],
            tool=data["tool"],
            tool_version=data["tool_version"],
            model_name=model["name"],
            model_sha256=model["sha256"],
            model_file=model["file"],
            parameters=dict(data["parameters"]),
            channel_ids=tuple(data["channel_ids"]),
            device=data["device"],
            run_folder=data["run_folder"],
            run_manifest_sha256=data["run_manifest_sha256"],
            images=tuple(
                SegmentedImage(i["image"], i["status"], i["message"], i["mask_sha256"])
                for i in data["images"]
            ),
        )

    def same_provenance(self, other: SegmentationBatch) -> bool:
        """Return True if ``other`` was made the same way (everything but its images)."""
        return replace(self, images=()) == replace(other, images=())


@dataclass(frozen=True)
class SegmentationRecord:
    """The history of one mask type: every batch that made or skipped its masks, in order.

    Attributes
    ----------
    mask_type
        The mask type (the name of the folder holding the masks and this record).
    batches
        The batches, oldest first; batch IDs are unique.
    """

    mask_type: str
    batches: tuple[SegmentationBatch, ...] = ()
    record_version: int = field(default=RECORD_VERSION, init=False)

    def __post_init__(self) -> None:
        """Refuse a record that could not be read back (the same checks as reading)."""
        _check(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        """Return the record as the JSON object written to the file."""
        return {
            "record_version": self.record_version,
            "mask_type": self.mask_type,
            "batches": [b.to_dict() for b in self.batches],
        }

    @classmethod
    def from_dict(cls, data: object) -> SegmentationRecord:
        """Build a record from a JSON object, checking all of it.

        Raises
        ------
        SegmentationError
            For an unknown ``record_version``, a missing or unknown key, a value of the wrong
            type or outside its allowed set, a batch ID twice, or an image twice in a batch.
        """
        data = _check(data)
        return cls(
            data["mask_type"], tuple(SegmentationBatch.from_dict(b) for b in data["batches"])
        )

    def latest(self, image: str) -> tuple[SegmentationBatch, SegmentedImage] | None:
        """Return the newest batch and entry that wrote the mask of ``image``, or None.

        A mask replaced by a later batch (``overwrite=True``) belongs to that later batch.
        """
        for batch in reversed(self.batches):
            for entry in batch.images:
                if entry.image == image and entry.status == "written":
                    return batch, entry
        return None


_KEYS = frozenset({"record_version", "mask_type", "batches"})
_BATCH_KEYS = frozenset(
    {
        "batch_id",
        "tool",
        "tool_version",
        "model",
        "parameters",
        "channel_ids",
        "device",
        "run_folder",
        "run_manifest_sha256",
        "images",
    }
)
_MODEL_KEYS = frozenset({"name", "file", "sha256"})
_IMAGE_KEYS = frozenset({"image", "status", "message", "mask_sha256"})


def _check(data: object) -> dict[str, Any]:
    """Return ``data`` if it is a version 1 record, else refuse (module docstring)."""
    if not isinstance(data, dict):
        raise SegmentationError("The segmentation record is not a JSON object.")
    version = data.get("record_version")
    if version != RECORD_VERSION or isinstance(version, bool):
        raise SegmentationError(
            f"The segmentation record has version {version!r}; this release reads version "
            f"{RECORD_VERSION} only. Segment again with this release, or use the release "
            "that wrote it."
        )
    _keys(data, _KEYS, "the record")
    check_mask_type(data["mask_type"])
    if not isinstance(data["batches"], (list, tuple)):
        raise SegmentationError("batches must be a list.")
    for batch in data["batches"]:
        _batch(batch)
    ids = [b["batch_id"] for b in data["batches"]]
    if len(set(ids)) != len(ids):
        raise SegmentationError("The record names a batch ID twice.")
    return data


def _batch(data: object) -> dict[str, Any]:
    """Return ``data`` if it is a valid batch, else refuse."""
    data = _keys(data, _BATCH_KEYS, "a batch")
    for key in ("batch_id", "tool", "tool_version", "run_folder"):
        _text(data[key], key)
    _hash(data["run_manifest_sha256"], "run_manifest_sha256")
    if data["device"] not in DEVICES:
        raise SegmentationError(
            f"device must be one of {', '.join(DEVICES)}, not {data['device']!r}."
        )
    if not isinstance(data["parameters"], dict):
        raise SegmentationError("parameters must be a JSON object.")
    try:
        json.dumps(data["parameters"], allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SegmentationError(f"parameters must be plain JSON values ({exc}).") from None
    _model(data["model"])
    ids = data["channel_ids"]
    if not isinstance(ids, (list, tuple)) or not ids:
        raise SegmentationError("channel_ids must list at least one channel ID.")
    for channel_id in ids:
        _text(channel_id, "a channel ID")
    if len(set(ids)) != len(ids):
        raise SegmentationError("channel_ids names a channel twice.")
    if not isinstance(data["images"], (list, tuple)):
        raise SegmentationError("images must be a list.")
    for entry in data["images"]:
        _image(entry)
    seen: set[str] = set()
    repeated: set[str] = set()
    for entry in data["images"]:
        (repeated if entry["image"] in seen else seen).add(entry["image"])
    twice = sorted(repeated)
    if twice:
        raise SegmentationError(
            f"The batch {data['batch_id']!r} has more than one entry for {', '.join(twice)}; "
            "an image has one status in a batch."
        )
    return data


def _keys(data: object, expected: frozenset[str], what: str) -> dict[str, Any]:
    """Return ``data`` if it is an object with exactly the ``expected`` keys, else refuse."""
    if not isinstance(data, dict):
        raise SegmentationError(f"{what} must be a JSON object.")
    missing, extra = sorted(expected - data.keys()), sorted(data.keys() - expected)
    if missing or extra:
        found = [f"missing {', '.join(missing)}"] * bool(missing)
        found += [f"unknown {', '.join(map(str, extra))}"] * bool(extra)
        raise SegmentationError(f"{what} has the wrong keys: {'; '.join(found)}.")
    return data


def _text(value: object, what: str) -> None:
    """Refuse anything but a non-empty string."""
    if not isinstance(value, str) or not value.strip():
        raise SegmentationError(f"{what} must be a non-empty text, not {value!r}.")


def _hash(value: object, what: str) -> None:
    """Refuse anything but 64 lowercase hexadecimal characters."""
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise SegmentationError(f"{what} must be a SHA-256 (64 lowercase hexadecimal digits).")


def _model(model: object) -> None:
    """Refuse a model entry without a name and a SHA-256 (a file or None)."""
    model = _keys(model, _MODEL_KEYS, "model")
    _text(model["name"], "the model name")
    _hash(model["sha256"], "the model sha256")
    if model["file"] is not None:
        _text(model["file"], "the model file")


def _image(entry: object) -> None:
    """Refuse an entry with an unknown status, a failure without a message, or a bad hash."""
    entry = _keys(entry, _IMAGE_KEYS, "an image entry")
    _text(entry["image"], "the image path")
    if entry["status"] not in STATUSES:
        raise SegmentationError(
            f"status must be one of {', '.join(STATUSES)}, not {entry['status']!r}."
        )
    if not isinstance(entry["message"], str):
        raise SegmentationError("an image entry's message must be a text.")
    if entry["status"] == "failed" and not entry["message"].strip():
        raise SegmentationError(f"{entry['image']} failed without a message; say why.")
    if entry["status"] == "written":
        _hash(entry["mask_sha256"], f"the mask_sha256 of {entry['image']}")
    elif entry["mask_sha256"] is not None:
        raise SegmentationError(
            f"{entry['image']} is {entry['status']}, so it has no mask_sha256 of its own."
        )


def check_mask_type(mask_type: str) -> None:
    """Refuse a mask type that preprocessing would refuse, or that is not one folder name.

    The rules are the ones the masks step applies to a discovered type: text with no leading
    or trailing whitespace that does not read as a missing value in tables (``NA``, ``nan``,
    ...) (:func:`.discovery.check_identity_text`), in Unicode NFC (never converted), and the
    name of one folder (no separator, not ``.`` or ``..``).

    Raises
    ------
    SegmentationError
        Naming what is wrong.
    """
    try:
        check_identity_text(mask_type, "mask type")
    except IdentityError as exc:
        raise SegmentationError(f"{exc}. Choose another mask type.") from None
    if not is_nfc(mask_type):
        raise SegmentationError(
            f"The mask type {mask_type!r} is not in Unicode NFC form (an accent stored as a "
            "separate character). Retype it, or choose another mask type."
        )
    if mask_type in {".", ".."} or Path(mask_type).name != mask_type or "\\" in mask_type:
        raise SegmentationError(f"{mask_type!r} is not a mask type: it names one folder.")


def mask_sha256(path: str | Path) -> str:
    """Return the SHA-256 of a mask file's bytes, for the entry of a written image."""
    return file_sha256(path)


def new_batch_id() -> str:
    """Return a batch ID that no other batch has: the UTC start time and a random suffix.

    Two batches started in the same second still differ (``2026-11-21T10:00:00Z-3fa9c2d1``),
    so a second batch is never refused for the ID after its masks were written.
    """
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


@contextmanager
def hold_lock(lock: Path, what: str) -> Iterator[None]:
    """Hold the exclusive lock file ``lock`` for the block; refuse at once if it is held.

    The run's own lock (:func:`.lock_file.exclusive_lock`): created with ``O_CREAT | O_EXCL``,
    removed at the end (also on an error) while it still names this writer, never waited on.
    It protects the writers of one computer; a file system shared by several computers (a
    Drive folder open in two Colab sessions) does not make the exclusive create reliable.
    ``what`` names what is being written, for the refusal that names the holder.
    """
    lock.parent.mkdir(parents=True, exist_ok=True)
    stack = ExitStack()
    try:
        stack.enter_context(exclusive_lock(lock))
    except LockHeldError as held:
        raise SegmentationError(
            f"{what} is being written by {held.holder.describe()} "
            f"({held.path.name} exists). Wait for it to finish and write again; if no writer "
            f"is running (it was killed), delete {held.path} yourself."
        ) from None
    with stack:
        yield


def _record_lock(path: Path) -> AbstractContextManager[None]:
    """Hold the lock beside the record ``path`` for the block (:func:`hold_lock`)."""
    return hold_lock(path.with_name(RECORD_LOCK_FILENAME), f"The record {path}")


def record_path(masks_root: str | Path, mask_type: str) -> Path:
    """Return where the record of ``mask_type`` is, ``<masks>/<type>/segmentation_record.json``."""
    check_mask_type(mask_type)
    return Path(masks_root) / mask_type / RECORD_FILENAME


def write_record(masks_root: str | Path, mask_type: str, batch: SegmentationBatch) -> Path:
    """Add ``batch`` to the record of ``mask_type`` (atomically); earlier batches are kept.

    A batch ID not yet in the file is appended. The same batch ID again (a batch written as it
    progresses) replaces that batch only if it was made the same way (everything but the
    images is equal) and keeps every entry it already had unchanged: provenance is only ever
    added. Anything else is refused and the file is left as it was.

    Parameters
    ----------
    masks_root
        The masks folder.
    mask_type
        The mask type, checked as :func:`check_mask_type` does.
    batch
        The batch.

    Returns
    -------
    Path
        The file written.

    Raises
    ------
    SegmentationError
        For a mask type preprocessing would refuse, an existing record that is not valid, a
        batch ID already used by a batch made another way or whose earlier entries the new
        one drops or changes.
    """
    path = record_path(masks_root, mask_type)
    with _record_lock(path):
        earlier = (
            read_record(masks_root, mask_type) if path.exists() else SegmentationRecord(mask_type)
        )
        record = _with_batch(earlier, batch)
        text = json.dumps(record.to_dict(), indent=2, ensure_ascii=False, allow_nan=False)
        return write_text_atomic(path, text + "\n", parents=True)


def _with_batch(earlier: SegmentationRecord, batch: SegmentationBatch) -> SegmentationRecord:
    """Return ``earlier`` with ``batch`` added or, for its own ID again, extended (or refuse)."""
    batches = list(earlier.batches)
    for index, old in enumerate(batches):
        if old.batch_id != batch.batch_id:
            continue
        kept = {(e.image, e.status, e.message, e.mask_sha256) for e in batch.images}
        lost = [
            e.image for e in old.images if (e.image, e.status, e.message, e.mask_sha256) not in kept
        ]
        if not old.same_provenance(batch):
            problem = "was made another way (tool, model, parameters, channels, device or run)"
        elif lost:
            problem = f"would lose or change its entries for {', '.join(lost)}"
        else:
            problem = ""
        if problem:
            raise SegmentationError(
                f"The batch {batch.batch_id!r} is already in the record of {earlier.mask_type} "
                f"and {problem}. Give a new batch its own batch_id: earlier provenance is never "
                "replaced."
            )
        batches[index] = batch
        break
    else:
        batches.append(batch)
    return SegmentationRecord(earlier.mask_type, tuple(batches))


def read_record(masks_root: str | Path, mask_type: str) -> SegmentationRecord:
    """Read and check the record of ``mask_type``.

    Raises
    ------
    SegmentationError
        If there is no record, it is not JSON, it is of another version, or it is not valid
        (:meth:`SegmentationRecord.from_dict`), or it names another mask type.
    """
    path = record_path(masks_root, mask_type)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SegmentationError(f"There is no segmentation record at {path}.") from None
    except (OSError, ValueError) as exc:
        raise SegmentationError(f"The segmentation record {path} cannot be read ({exc}).") from None
    record = SegmentationRecord.from_dict(data)
    if record.mask_type != mask_type:
        raise SegmentationError(
            f"The record at {path} is of mask type {record.mask_type!r}, not {mask_type!r}."
        )
    return record
