"""The provenance file of one mask type made by a segmentation tool (contract §15.5).

A segmentation tool (Cellpose in the shipped notebook) writes its masks under
``<masks folder>/<mask type>/`` with :func:`.segmentation_handoff.write_mask`, and one
``segmentation_record.json`` beside them says how they were made: the tool and its version,
the model (a name, and the weights file with its SHA-256), the parameters, the channel IDs
that were read, the device that ran it, the run it read (folder and manifest SHA-256) and one
entry per image (written, skipped or failed, with the message). The file is provenance only:
nothing in preprocessing reads it back to decide anything, and a mask is a mask whether or not
a record exists.

The record is versioned (:data:`RECORD_VERSION`). :func:`read_record` checks everything it
reads and refuses a version it does not know, an unknown key, a wrong type or a value outside
the allowed set; nothing is repaired or defaulted. The module needs only the standard library.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._io import write_text_atomic

RECORD_VERSION = 1
"""The version of the record this module writes and reads."""
RECORD_FILENAME = "segmentation_record.json"
"""The record's name, directly inside the mask type's folder."""
DEVICES = ("cuda", "cpu", "mps")
"""The device the tool ran on, always stated (no silent fallback)."""
STATUSES = ("written", "skipped", "failed")
"""What happened to one image."""
_SHA256 = re.compile(r"[0-9a-f]{64}")


class SegmentationError(ValueError):
    """A segmentation hand-off or record that is refused; the message says what to do."""


@dataclass(frozen=True)
class SegmentedImage:
    """What happened to one image of the run.

    Attributes
    ----------
    image
        The image's path relative to the experiment, as the run's manifest records it.
    status
        ``written``, ``skipped`` (a mask was already there) or ``failed``.
    message
        Why, in plain words; required for ``failed`` (an error is never dropped).
    """

    image: str
    status: str
    message: str = ""


@dataclass(frozen=True)
class SegmentationRecord:
    """How the masks of one mask type were made.

    Attributes
    ----------
    mask_type
        The mask type (the name of the folder holding the masks and this record).
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
        One entry per image handled, in the order handled.
    """

    mask_type: str
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
    record_version: int = field(default=RECORD_VERSION, init=False)

    def __post_init__(self) -> None:
        """Refuse a record that could not be read back (the same checks as reading)."""
        _check(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        """Return the record as the JSON object written to the file."""
        return {
            "record_version": self.record_version,
            "mask_type": self.mask_type,
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
                {"image": i.image, "status": i.status, "message": i.message} for i in self.images
            ],
        }

    @classmethod
    def from_dict(cls, data: object) -> SegmentationRecord:
        """Build a record from a JSON object, checking all of it.

        Raises
        ------
        SegmentationError
            For an unknown ``record_version``, a missing or unknown key, a value of the wrong
            type or outside its allowed set.
        """
        _check(data)
        model = data["model"]
        return cls(
            mask_type=data["mask_type"],
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
                SegmentedImage(i["image"], i["status"], i["message"]) for i in data["images"]
            ),
        )


_KEYS = frozenset(
    {
        "record_version",
        "mask_type",
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
_IMAGE_KEYS = frozenset({"image", "status", "message"})


def _check(data: object) -> None:
    """Refuse an object that is not a version 1 record (module docstring)."""
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
    for key in ("mask_type", "tool", "tool_version", "run_folder"):
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


def _keys(data: object, expected: frozenset[str], what: str) -> None:
    """Refuse an object without exactly the ``expected`` keys."""
    if not isinstance(data, dict):
        raise SegmentationError(f"{what} must be a JSON object.")
    missing, extra = sorted(expected - data.keys()), sorted(data.keys() - expected)
    if missing or extra:
        found = [f"missing {', '.join(missing)}"] * bool(missing)
        found += [f"unknown {', '.join(map(str, extra))}"] * bool(extra)
        raise SegmentationError(f"{what} has the wrong keys: {'; '.join(found)}.")


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
    _keys(model, _MODEL_KEYS, "model")
    _text(model["name"], "the model name")
    _hash(model["sha256"], "the model sha256")
    if model["file"] is not None:
        _text(model["file"], "the model file")


def _image(entry: object) -> None:
    """Refuse an image entry with an unknown status, or a failure without a message."""
    _keys(entry, _IMAGE_KEYS, "an image entry")
    _text(entry["image"], "the image path")
    if entry["status"] not in STATUSES:
        raise SegmentationError(
            f"status must be one of {', '.join(STATUSES)}, not {entry['status']!r}."
        )
    if not isinstance(entry["message"], str):
        raise SegmentationError("an image entry's message must be a text.")
    if entry["status"] == "failed" and not entry["message"].strip():
        raise SegmentationError(f"{entry['image']} failed without a message; say why.")


def check_mask_type(mask_type: str) -> None:
    """Refuse a mask type that is not the name of one folder."""
    if not mask_type or mask_type in {".", ".."} or Path(mask_type).name != mask_type:
        raise SegmentationError(f"{mask_type!r} is not a mask type: it names one folder.")


def record_path(masks_root: str | Path, mask_type: str) -> Path:
    """Return where the record of ``mask_type`` is, ``<masks>/<type>/segmentation_record.json``."""
    check_mask_type(mask_type)
    return Path(masks_root) / mask_type / RECORD_FILENAME


def write_record(masks_root: str | Path, mask_type: str, record: SegmentationRecord) -> Path:
    """Write the record of ``mask_type`` atomically, replacing an earlier one.

    Parameters
    ----------
    masks_root
        The masks folder.
    mask_type
        The mask type; it must be the record's own.
    record
        The record.

    Returns
    -------
    Path
        The file written.

    Raises
    ------
    SegmentationError
        If ``mask_type`` is not one folder name or is not the record's.
    """
    path = record_path(masks_root, mask_type)
    if record.mask_type != mask_type:
        raise SegmentationError(
            f"The record is of mask type {record.mask_type!r}, not {mask_type!r}."
        )
    text = json.dumps(record.to_dict(), indent=2, ensure_ascii=False, allow_nan=False)
    return write_text_atomic(path, text + "\n", parents=True)


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
