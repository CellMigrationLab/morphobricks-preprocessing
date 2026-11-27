"""The built-in Cellpose weights: pinned, downloaded on first use, checked by their SHA-256.

Part of :mod:`.segmentation_cellpose_model`. Owner decision (issue #574, 26 November 2026):
choosing a built-in model and pressing **Preview** or **Segment** (or **Load the model**) is the
consent to download that model's weights; there is no separate tick. What is downloaded, and
how, is fixed here rather than left to Cellpose 4.2.1.1, which fetches
``.../resolve/main/<name>`` (a moving branch) with TLS verification turned off and checks
nothing:

- **Only the chosen model's file.** One file of :data:`PINNED_WEIGHTS`, by its name, from the
  Hugging Face repository :data:`WEIGHTS_REPOSITORY` at the commit :data:`WEIGHTS_REVISION`
  (:func:`weights_url`), with the default TLS verification. Nothing else is fetched; another
  model is never used instead.
- **Checked.** The bytes downloaded must have the pinned size and SHA-256 (the hashes are those
  Hugging Face lists for the files at that commit, its LFS object IDs); they go to a temporary
  file next to the target and replace it only once checked. A copy already in Cellpose's model
  folder is hashed and must match too: a copy that differs is refused, never used or replaced.
- **A refusal, never a fallback.** A failed or blocked download, or bytes that do not match,
  stop the load with a message naming the model and the source; no partial file is left.
- **Seen.** ``progress(label, done, total)`` is told the bytes received (``total`` is 0 when the
  server gives no size) so the notebook can show "Downloading <model> weights…" with a bar.
"""

from __future__ import annotations

import hashlib
import http.client
import os
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from ._io import file_sha256
from .segmentation_record import SegmentationError

WEIGHTS_HOST = "https://huggingface.co"
WEIGHTS_REPOSITORY = "mouseland/cellpose-sam"
"""The Hugging Face repository Cellpose 4.2.1.1 downloads its built-in weights from."""
WEIGHTS_REVISION = "7c61431b5fbb078f3296754bd15d9f51b320f837"
"""The commit of :data:`WEIGHTS_REPOSITORY` downloaded from: the head of ``main`` on
26 November 2026 (its last change, 17 May 2026, renamed ``cpdino_vitb`` to ``cpdino-vitb``)."""
CHUNK = 1 << 20
"""Bytes read from the server at a time."""
TIMEOUT = 60
"""Seconds without an answer from the server before the download is refused."""


@dataclass(frozen=True)
class PinnedFile:
    """The SHA-256 and the size in bytes of one weights file at :data:`WEIGHTS_REVISION`."""

    sha256: str
    size: int


PINNED_WEIGHTS = MappingProxyType(
    {
        "cpsam_v2": PinnedFile(
            "0f1cc3f7ecdd8a037a57c6c48d9d8921391be4cbce3fa9f13c3e3a2e1253c667", 1233586851
        ),
        "cpsam": PinnedFile(
            "e1440429eb384f95afe32bcba6510f90d518eaedc917ede549bed6804004abe2", 1233587898
        ),
        "cpdino": PinnedFile(
            "fad0dda262554922da1c5ab0e0004b00c1b14b8a48bea6781b89222567577573", 1213987717
        ),
        "cpdino-vitb": PinnedFile(
            "3ed4c06a3963ab13ff377d4e2957174aaaf637434eb031ae9931fcbfaf9a217f", 343599997
        ),
    }
)
"""Every built-in model's file at :data:`WEIGHTS_REVISION`, as Hugging Face lists it."""

Progress = Callable[[str, int, int], None]
"""``progress(label, done, total)``: what is being fetched, bytes received, bytes in all (0 for
unknown)."""


@dataclass(frozen=True)
class WeightsFile:
    """A built-in model's weights file on disk, checked against its pin.

    Attributes
    ----------
    name
        The built-in model's name (also the file's name).
    path
        The file in Cellpose's model folder.
    sha256
        Its SHA-256, equal to the pinned one.
    url
        Where it is downloaded from (:func:`weights_url`).
    repository, revision
        :data:`WEIGHTS_REPOSITORY` and :data:`WEIGHTS_REVISION`.
    downloaded
        True when this call downloaded it; False when the copy on disk was used.
    identity
        The file's :func:`file_identity` when it was hashed (checked again after loading).
    """

    name: str
    path: Path
    sha256: str
    url: str
    repository: str = WEIGHTS_REPOSITORY
    revision: str = WEIGHTS_REVISION
    downloaded: bool = False
    identity: tuple[int, ...] | None = None


def weights_url(name: str) -> str:
    """Return the URL of built-in model ``name``'s file at :data:`WEIGHTS_REVISION`."""
    return f"{WEIGHTS_HOST}/{WEIGHTS_REPOSITORY}/resolve/{WEIGHTS_REVISION}/{name}"


def download_label(name: str) -> str:
    """Return what the notebook shows while ``name``'s weights download."""
    return f"Downloading {name} weights…"


def _pinned(name: str) -> PinnedFile:
    """Return the pin of built-in model ``name``, or refuse a name without one."""
    pin = PINNED_WEIGHTS.get(name)
    if pin is None:
        raise SegmentationError(
            f"There are no pinned weights for {name!r}; only {', '.join(PINNED_WEIGHTS)} are "
            "downloaded. Give the path of a model file instead."
        )
    return pin


def _open_url(url: str, timeout: float) -> object:
    """Open ``url`` for reading (a seam for the tests: they never download)."""
    return urllib.request.urlopen(url, timeout=timeout)


def _refusal(name: str, url: str, why: str) -> SegmentationError:
    """Return the refusal of a download that did not give the pinned file."""
    return SegmentationError(
        f"The weights of {name} could not be downloaded from {url} ({why}). Nothing was loaded, "
        "no other model is used and no partial file is kept. Check the internet access (a "
        "proxy or firewall may block huggingface.co), then press the button again."
    )


def file_identity(path: str | os.PathLike) -> tuple[int, ...]:
    """Return a file's identity now: device, inode, size, modification and status-change times.

    Taken when the file is hashed and compared after Cellpose loads it
    (:func:`.segmentation_cellpose_model.load_model`): a file replaced, removed or written to
    in between is another file than the one hashed. The status-change time (ns) is part of it
    since v0.9.1 (review bc B6): a rewrite in place with the size and modification time set
    back moves it on POSIX. What Cellpose loads is a private copy hashed as it is made
    (:mod:`.segmentation_cellpose_guard`), so the identity is not the only evidence.

    Raises
    ------
    OSError
        When the file cannot be read (it is gone, say).
    """
    found = os.stat(path)
    return (found.st_dev, found.st_ino, found.st_size, found.st_mtime_ns, found.st_ctime_ns)


def fetch_weights(
    name: str, model_dir: str | os.PathLike, progress: Progress | None = None
) -> WeightsFile:
    """Return built-in model ``name``'s pinned weights file, downloading it when missing.

    Parameters
    ----------
    name
        One of :data:`PINNED_WEIGHTS`.
    model_dir
        Cellpose's model folder (``cellpose.models.MODEL_DIR``), where the file is kept.
    progress
        Told ``(label, bytes received, bytes in all)`` while downloading; not called for a
        copy already on disk.

    Raises
    ------
    SegmentationError
        For a copy on disk that is not the pinned file (or changed while it was hashed), a
        download that fails or is blocked, bytes that are not the pinned size and SHA-256 (each
        names the model and the source), or a file that cannot be written (it names the folder).
    """
    pin, url = _pinned(name), weights_url(name)
    target = Path(model_dir) / name
    if target.is_file():
        before = file_identity(target)
        found = file_sha256(target)
        if found != pin.sha256:
            raise SegmentationError(
                f"The weights of {name} on this computer ({target}) are not the pinned file of "
                f"{url}: SHA-256 {found}, expected {pin.sha256}. They are not used, and not "
                "replaced. Delete that file (or move it away), then press the button again to "
                "download the pinned weights."
            )
        if file_identity(target) != before:
            raise changed_refusal(target)
        return WeightsFile(name, target, found, url, identity=before)
    sha256 = _download(name, url, target, pin, progress)
    return WeightsFile(name, target, sha256, url, downloaded=True, identity=file_identity(target))


def changed_refusal(path: Path) -> SegmentationError:
    """Return the refusal of a weights file that changed after it was hashed."""
    return SegmentationError(
        f"The weights file {path} changed after it was checked (it was replaced, removed or "
        "written to), so the model loaded may not be the file whose SHA-256 is recorded. "
        "Nothing was segmented and that model is not used. Make sure nothing else writes to "
        "Cellpose's model folder, then press the button again."
    )


def _write_refusal(name: str, folder: Path, exc: OSError) -> SegmentationError:
    """Return the refusal of weights that could not be written on this computer."""
    return SegmentationError(
        f"The weights of {name} could not be written to {folder} ({type(exc).__name__}: {exc}). "
        "The disk may be full or the folder not writable; this is not a network problem. "
        "Nothing was loaded and no partial file is kept. Free some space (the file is up to "
        "1.2 GB) or make the folder writable, then press the button again."
    )


def _download(name: str, url: str, target: Path, pin: PinnedFile, progress: Progress | None) -> str:
    """Download ``url`` to ``target`` through a checked temporary file; return its SHA-256."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=f".{name}.", suffix=".part", dir=target.parent)
    except OSError as exc:
        raise _write_refusal(name, target.parent, exc) from exc
    try:
        done, found = _stream(name, url, handle, target.parent, progress)
        if (done, found) != (pin.size, pin.sha256):
            raise _refusal(
                name,
                url,
                f"received {done:,} bytes with SHA-256 {found}; the pinned file has "
                f"{pin.size:,} bytes and SHA-256 {pin.sha256}",
            )
        try:
            os.replace(temporary, target)
        except OSError as exc:
            raise SegmentationError(
                f"The weights of {name} were downloaded and checked but could not be moved to "
                f"{target} ({type(exc).__name__}: {exc}). Nothing was loaded and no partial "
                "file is kept. Check that the folder is writable, then press the button again."
            ) from exc
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    return found


def _stream(
    name: str, url: str, handle: int, folder: Path, progress: Progress | None
) -> tuple[int, str]:
    """Write ``url``'s bytes to the open file ``handle``, synced; return their count and hash.

    A network error is a download refusal; a write error (a full disk) is a write refusal.
    """
    label, digest, done = download_label(name), hashlib.sha256(), 0
    network = (urllib.error.URLError, OSError, ValueError, http.client.HTTPException)
    out = os.fdopen(handle, "wb")
    try:
        try:
            response = _open_url(url, TIMEOUT)
        except urllib.error.HTTPError as exc:
            raise _refusal(name, url, f"the server answered {exc.code} {exc.reason}") from exc
        except network as exc:
            why = getattr(exc, "reason", None) or exc
            raise _refusal(name, url, f"{type(exc).__name__}: {why}") from exc
        with response:
            size = response.headers.get("Content-Length")
            total = int(size) if size and size.isdigit() else 0
            if progress is not None:
                progress(label, 0, total)
            while True:
                try:
                    block = response.read(CHUNK)
                except network as exc:
                    why = getattr(exc, "reason", None) or exc
                    raise _refusal(name, url, f"{type(exc).__name__}: {why}") from exc
                if not block:
                    break
                try:
                    out.write(block)
                except OSError as exc:
                    raise _write_refusal(name, folder, exc) from exc
                digest.update(block)
                done += len(block)
                if progress is not None:
                    progress(label, done, total)
        try:
            out.flush()
            os.fsync(out.fileno())  # on disk before the rename makes it the weights file
            out.close()
        except OSError as exc:
            raise _write_refusal(name, folder, exc) from exc
    finally:
        out.close()
    return done, digest.hexdigest()


__all__ = [
    "CHUNK",
    "PINNED_WEIGHTS",
    "TIMEOUT",
    "WEIGHTS_HOST",
    "WEIGHTS_REPOSITORY",
    "WEIGHTS_REVISION",
    "PinnedFile",
    "Progress",
    "WeightsFile",
    "changed_refusal",
    "download_label",
    "fetch_weights",
    "file_identity",
    "weights_url",
]
