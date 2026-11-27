"""What Cellpose is handed to load, and what it may not do while it loads (review bc, B6).

Part of :mod:`.segmentation_cellpose_model`. Two holes remained after the weights were pinned
and checked (:mod:`.segmentation_cellpose_weights`), both read in Cellpose 4.2.1.1's
``CellposeModel.__init__`` (``cellpose/models.py``):

- **Its own fallback download.** For a path that does not exist when it looks,
  ``CellposeModel`` calls ``cache_model_path("cpsam_v2")``, which downloads ``cpsam_v2`` from
  the moving ``resolve/main`` branch with ``cellpose.utils.download_url_to_file``. A file removed
  after this brick's last check and before Cellpose's would start that download; a check after
  loading can refuse the model, not undo the download. :func:`no_cellpose_downloads` replaces
  both functions with a refusal while the model is built, under a lock (two loads never
  overlap, so neither can see the other's originals), and puts them back whatever happens.
  Any call is a refusal of the load, even if Cellpose caught the error. The only weights
  download is the brick's own, of the chosen model, pinned and checked.
- **The bytes loaded.** A file can be rewritten in place with its size and times kept between
  the hash and the load. :func:`private_copy` copies the weights into a new private folder,
  hashing the bytes it copies; the hash must be the pinned one (or, for a model file of one's
  own, it is the one recorded), the copy is made read-only, and its identity (device, inode,
  size, modification and status-change times) must be unchanged after Cellpose loaded it.
  Cellpose loads that copy, so the bytes recorded are the bytes loaded. The copy is deleted
  when the load ends (a full extra read and write of the weights per load: up to 1.2 GB).

The owner's consent rule is unchanged: choosing a model and pressing Preview, Segment or Load
the model is the consent to download that model's weights only (#574); these guards make sure
nothing else is fetched.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from .segmentation_cellpose_weights import CHUNK, changed_refusal, file_identity
from .segmentation_record import SegmentationError

GUARDED = (("cellpose.models", "cache_model_path"), ("cellpose.utils", "download_url_to_file"))
"""The Cellpose 4.2.1.1 functions that fetch weights, replaced by a refusal during a load."""
_LOADING = threading.Lock()
"""Held while a model is built: one load at a time, so a guard is never bypassed."""
DOWNLOAD_REFUSED = (
    "Cellpose tried to fetch weights by itself while loading {request} ({what}): its own "
    "fallback or download, which is never allowed. Nothing was downloaded and nothing was "
    "segmented. The weights file may have been removed or replaced while the model loaded: "
    "press the button again."
)


class CellposeDownloadError(SegmentationError):
    """Cellpose tried to fetch weights itself while a model was built (never allowed)."""


@dataclass(frozen=True)
class PrivateCopy:
    """The copy of a weights file Cellpose loads.

    Attributes
    ----------
    path
        The copy, in a private folder, with the source's file name.
    sha256
        The SHA-256 of the bytes copied.
    identity
        The copy's identity once made read-only (checked again after the load).
    """

    path: Path
    sha256: str
    identity: tuple[int, ...]


@contextmanager
def private_copy(
    source: Path, identity: tuple[int, ...], sha256: str | None
) -> Iterator[PrivateCopy]:
    """Yield a private, read-only copy of ``source``, hashed while it is copied.

    Parameters
    ----------
    source
        The weights file (checked against its pin, or a model file of one's own).
    identity
        The source's identity when it was checked; it must be the same before and after the
        copy, else the copy may hold other bytes than those checked.
    sha256
        The SHA-256 the bytes must have (the pinned one); None for a model file of one's own,
        whose SHA-256 is the one of the bytes copied.

    Raises
    ------
    SegmentationError
        For a source that changed or cannot be read, bytes that are not the pinned ones, or
        a copy that cannot be written (it names the folder).
    """
    folder = Path(tempfile.mkdtemp(prefix="morphobricks-weights-"))  # mode 0700
    try:
        target = folder / source.name
        try:
            found = _copy_hashed(source, target)
        except OSError as exc:
            raise SegmentationError(
                f"The weights file {source} could not be copied to {folder} for loading "
                f"({type(exc).__name__}: {exc}). Nothing was loaded. Free some space in the "
                "temporary folder (the file is up to 1.2 GB), then press the button again."
            ) from exc
        if _identity(source) != identity:
            raise changed_refusal(source)
        if sha256 is not None and found != sha256:
            raise SegmentationError(
                f"The weights file {source} does not have the SHA-256 checked a moment ago "
                f"({found}, expected {sha256}): it changed while it was read. Nothing was "
                "loaded. Make sure nothing else writes to Cellpose's model folder, then press "
                "the button again."
            )
        os.chmod(target, stat.S_IRUSR)
        copied = _identity(target)
        if copied is None:
            raise changed_refusal(target)
        yield PrivateCopy(target, found, copied)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def unchanged(copy: PrivateCopy) -> None:
    """Refuse a private copy whose identity is not the one it had when it was made."""
    if _identity(copy.path) != copy.identity:
        raise changed_refusal(copy.path)


def _copy_hashed(source: Path, target: Path) -> str:
    """Copy ``source`` to a new file ``target`` (never an existing one); return its SHA-256."""
    digest = hashlib.sha256()
    with open(source, "rb") as reading, open(target, "xb") as writing:
        while block := reading.read(CHUNK):
            digest.update(block)
            writing.write(block)
        writing.flush()
        os.fsync(writing.fileno())
    return digest.hexdigest()


def _identity(path: Path) -> tuple[int, ...] | None:
    """Return :func:`.segmentation_cellpose_weights.file_identity`, or None when it is gone."""
    try:
        return file_identity(path)
    except OSError:
        return None


@contextmanager
def no_cellpose_downloads(request: str) -> Iterator[None]:
    """Refuse every weights fetch of Cellpose itself while the block runs (one load at a time).

    The functions of :data:`GUARDED` are replaced by a refusal and put back when the block
    ends, whatever ends it. A Cellpose whose modules lack one of them is not the pinned release
    and is refused: it could not be guarded.

    Raises
    ------
    CellposeDownloadError
        When Cellpose called one of them (even if it caught the refusal).
    SegmentationError
        When a function to guard is missing.
    """
    attempts: list[str] = []

    def refuse(what: str) -> object:
        def refused(*args: object, **kwargs: object) -> None:
            attempts.append(f"{what}({', '.join(map(str, args))})")
            raise CellposeDownloadError(DOWNLOAD_REFUSED.format(request=request, what=what))

        return refused

    with _LOADING:
        saved: list[tuple[ModuleType, str, object]] = []
        try:
            for module_name, name in GUARDED:
                module = sys.modules.get(module_name)
                if module is None or not callable(getattr(module, name, None)):
                    raise SegmentationError(
                        f"{module_name}.{name} is not there: this is not the Cellpose release "
                        "this brick guards, and its downloads cannot be refused. Nothing was "
                        "loaded."
                    )
                saved.append((module, name, getattr(module, name)))
                setattr(module, name, refuse(f"{module_name}.{name}"))
            yield
        finally:
            for module, name, original in reversed(saved):
                setattr(module, name, original)
        if attempts:
            raise CellposeDownloadError(
                DOWNLOAD_REFUSED.format(request=request, what="; ".join(attempts))
            )


def weight_devices(loaded: object) -> tuple[int, set[str]]:
    """Return how many tensors the network holds and the device types of all of them.

    Every parameter and every buffer of ``loaded.net`` counts. A model without a network that
    lists its tensors, or one with none, proves nothing and is refused.
    """
    net = getattr(loaded, "net", None)
    tensors = []
    for listing in ("parameters", "buffers"):
        method = getattr(net, listing, None)
        if not callable(method):
            raise SegmentationError(
                "The loaded model's network does not list its weights, so the device they sit "
                "on cannot be checked; refused."
            )
        tensors.extend(method())
    if not tensors:
        raise SegmentationError(
            "The loaded model's network holds no weights, so the device cannot be checked; refused."
        )
    return len(tensors), {str(t.device.type) for t in tensors}


__all__ = [
    "DOWNLOAD_REFUSED",
    "GUARDED",
    "CellposeDownloadError",
    "PrivateCopy",
    "no_cellpose_downloads",
    "private_copy",
    "unchanged",
    "weight_devices",
]
