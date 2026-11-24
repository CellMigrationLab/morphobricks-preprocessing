"""Write the copies of a conversion in its work folder, then move them into place.

Moved from :mod:`.conversion` (PP-11c). On any failure, every file and folder the
conversion wrote is removed, so the copy tree is left as it was.

The work folder (issue #448): every copy is first written below
``<copy tree>/.mbconv/files/``, at its own relative path (a short name, issue #482: every
copy's path is that much longer while it is written, and Windows limits paths to 259
characters; a work folder named ``.morphobricks-conversion.partial``, the name before #482,
is recognised as a leftover too). The
conversion makes that folder itself (it must not exist) and writes its owner file
(``owner.json``: what made it, and the lock holder with its token) into it before any copy,
so whatever is below it was written by a conversion. A conversion killed before its clean-up
leaves it; the next conversion removes it only when it holds nothing but a well-formed owner
file and ``files/``, and the holder named there is not known to be running
(:func:`work_folder_state`, issue #482). Anything else under that name is refused
(``conversion_work_folder_unowned``, or ``conversion_work_folder_in_use`` for a holder known
to be running) and kept. Only POSIX can tell whether a process runs, and only one on this
computer: on Windows, or for a holder on another computer, the holder counts as gone. The
real guard is the copy tree's lock, held throughout a conversion; removing it
(``remove_stale_lock``) means the user confirms that no conversion is running anywhere. A
conversion removes its own work folder only while the owner file still holds its own token.
Nothing else in the copy tree is ever removed but the copies this conversion moved into place
and the folders it made: a ``<name>.partial`` file or folder beside a copy is not the
conversion's (it was, before #448) and is never removed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import stat
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

from .content_hash import content_sha256
from .conversion_issues import failed_issue, links_issue, work_folder_not_removed_issue
from .conversion_record import CopyEntry
from .fingerprint import Fingerprint, current_fingerprint
from .links import is_link_stat
from .lock_file import LockHolder
from .mounts import MountCheck
from .ome_tiff_copy import CopyWriteError, write_named_copy, write_projected_copy
from .ome_zarr_copy import OME_ZARR, write_named_zarr, write_projected_zarr
from .planes import PlaneReadError
from .planned_copy import PlannedCopy
from .projection import ProjectionRefused
from .projection_issues import projection_failed_issue
from .run.lock import holder_running
from .source_content import source_content_sha256
from .validation import ValidationIssue

logger = logging.getLogger(__name__)

WORK_FOLDER = ".mbconv"
"""The conversion's work folder in the copy tree ("MorphoBricks conversion"); a hidden name,
never read as an image, kept short as it lengthens every copy's path while it is written."""
LEGACY_WORK_FOLDER = ".morphobricks-conversion.partial"
"""The work folder's name before issue #482: never made, but a leftover is recovered (or
refused) as one of :data:`WORK_FOLDER`."""
WORK_FOLDERS = (WORK_FOLDER, LEGACY_WORK_FOLDER)
"""Every name a conversion's work folder has had."""
OWNER_FILENAME = "owner.json"
"""The owner file of a work folder, written before any copy: it proves the folder is one."""
OWNER_KIND = "morphobricks-preprocessing conversion work folder"
FILES_FOLDER = "files"
"""The folder below the work folder holding the copies, each at its own relative path."""
OWNED, UNOWNED, IN_USE, UNFINISHED = "owned", "unowned", "in_use", "unfinished"
"""What :func:`work_folder_state` finds."""
_OWNER_START = b'{"holder": {"host": "'
"""How every owner file a conversion writes begins (``sort_keys``): a file cut short of
its end, by a kill before its ``fsync``, still begins with (part of) this."""
_OWNER_MAX = 4096
"""An owner file is far shorter; a longer one is never taken for a truncated one."""
_TOKEN = re.compile("[0-9a-f]{32}")
"""A lock holder's token (``uuid4().hex``)."""
_WRITING: set[str] = set()
"""The tokens of the work folders this process is writing now (a holder in this process
is live only while its token is here: a notebook's kernel outlives its conversions)."""


@dataclass(frozen=True)
class WorkFolder:
    """A work folder found in the copy tree (:func:`work_folder_state`).

    Attributes
    ----------
    path
        The folder.
    state
        :data:`OWNED` (left by a conversion that is gone: it may be removed),
        :data:`UNFINISHED` (empty, or holding only an empty or truncated owner file: a
        conversion was killed while making it; it is removed by an ``rmdir``),
        :data:`IN_USE` (its holder is a process known to be running on this computer: only
        POSIX can tell, see :func:`.run.lock.holder_running`) or
        :data:`UNOWNED` (not proven to be a conversion's: never removed).
    holder
        The holder its owner file names, when it is well formed.
    """

    path: Path
    state: str
    holder: LockHolder | None = None


class ConversionFailed(Exception):
    """A copy could not be written; carries the error for the user."""

    def __init__(self, issue: ValidationIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue


_FAILURES = (PlaneReadError, CopyWriteError, OSError, ValueError)
"""What reading a source or writing a copy raises when it fails (``tifffile`` raises
:class:`ValueError` for a file it cannot write); anything else is re-raised, after the same
clean-up."""


def write_all(
    root: Path,
    dest: Path,
    to_write: list[PlannedCopy],
    memory_limit: int,
    progress: Callable[[int, int, str], None] | None,
    holder: LockHolder,
) -> list[CopyEntry]:
    """Write every copy in the work folder, then move them all into place.

    The caller removed an earlier conversion's work folder (:func:`remove_leftover`, before
    the space check) and refused anything else under its name; one found here all the same
    (it appeared meanwhile) makes the conversion fail, removing nothing of it. On any
    failure, an interruption included, the work folder, every copy already moved into place
    (each where nothing was before) and every folder the conversion made are removed, so the
    copy tree is left as it was; a failure to read or write raises
    :class:`ConversionFailed`, anything else is re-raised.
    """
    work = dest / WORK_FOLDER
    if not to_write:
        return []
    made, route = _missing_folders(dest, to_write), _Route(dest)
    written: list[Path] = []
    contents: dict[str, str] = {}
    current, mine = to_write[0].relative_path, False
    _WRITING.add(holder.token)
    try:
        _make_work_folder(work, holder)
        mine = True
        for done, item in enumerate(to_write):
            current = item.relative_path
            if progress is not None:
                progress(done, len(to_write), current)
            route.check(partial_path(work, item))
            found = _write_partial(root, work, item, memory_limit)
            if found is not None:
                contents[item.relative_path] = found
        if progress is not None:
            progress(len(to_write), len(to_write), "")
        entries = []
        for item in to_write:
            current, target = item.relative_path, dest / item.relative_path
            route.check(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            route.check(target)
            if os.path.lexists(target):
                raise FileExistsError(f"{current} appeared during the conversion")
            os.replace(partial_path(work, item), target)
            written.append(target)
            copy, sha256 = Fingerprint.of(target), content_sha256(target)
            entry = replace(item.entry, copy=copy, copy_sha256=sha256)
            if item.relative_path in contents:
                done = entry.projection.with_content(contents[item.relative_path])
                entry = replace(entry, projection=done)
            entries.append(entry)
    except BaseException as exc:
        logger.debug("Conversion of %s failed", current, exc_info=True)
        _clean_up(route, (work, holder.token) if mine else None, written, made)
        if isinstance(exc, _Diverted):
            raise ConversionFailed(links_issue([exc.name])) from exc
        if isinstance(exc, ProjectionRefused):
            raise ConversionFailed(projection_failed_issue(current, exc)) from exc
        if isinstance(exc, (*_FAILURES, _SourceChanged)):
            raise ConversionFailed(failed_issue(current, exc)) from exc
        raise
    finally:
        _WRITING.discard(holder.token)
    _remove_work_folder(work, holder.token)
    return entries


def leftover_work_folders(dest: Path) -> list[WorkFolder]:
    """Return the work folders in the copy tree under any of :data:`WORK_FOLDERS`."""
    found = (work_folder_state(dest, name) for name in WORK_FOLDERS)
    return [work for work in found if work is not None]


def work_folder_state(dest: Path, name: str = WORK_FOLDER) -> WorkFolder | None:
    """Return the copy tree's work folder, and whether a conversion that is gone made it.

    ``name`` is one of :data:`WORK_FOLDERS`.

    Returns
    -------
    WorkFolder or None
        None when there is none. :data:`OWNED` only for a real folder (not a link) holding
        a well-formed owner file (:func:`read_owner`) and nothing else but ``files/``, a
        real folder, whose holder is not known to be running (the lock's liveness check,
        :func:`.run.lock.holder_running`; in this process, a holder is live while its
        conversion runs). :data:`IN_USE` for such a folder whose holder is live. That check
        tells only on POSIX, and only for a process on this computer: on Windows, and for a
        holder on another computer, the holder counts as gone and its folder as
        :data:`OWNED`. The copy tree's lock is the real guard: it is held throughout a
        conversion, and removing it means the user confirms no conversion runs anywhere.
        :data:`UNFINISHED` for a real folder that is empty or holds only an empty or
        truncated owner file (a kill between making the folder and writing that file,
        issue #482). Anything else is :data:`UNOWNED`: it is never removed.
    """
    work = dest / name
    if not os.path.lexists(work):
        return None
    if _unfinished(work):
        return WorkFolder(work, UNFINISHED)
    holder = read_owner(work)
    if holder is None or not _only_owner_and_files(work):
        return WorkFolder(work, UNOWNED, holder)
    return WorkFolder(work, IN_USE if _live(holder) else OWNED, holder)


def read_owner(work: Path) -> LockHolder | None:
    """Return the holder a work folder's owner file names, or None when it is not well formed.

    Well formed: a regular file (not a link) in a real folder, holding exactly ``kind``
    (:data:`OWNER_KIND`) and ``holder``, the lock holder: a host, a process ID above 0, a
    start time and a token (32 hex digits), nothing else.
    """
    owner = work / OWNER_FILENAME
    try:
        if not stat.S_ISDIR(os.lstat(work).st_mode) or not stat.S_ISREG(os.lstat(owner).st_mode):
            return None
        found = json.loads(owner.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(found, dict) or set(found) != {"kind", "holder"}:
        return None
    holder = found["holder"]
    names = [field.name for field in fields(LockHolder)]
    if found["kind"] != OWNER_KIND or not isinstance(holder, dict) or set(holder) != set(names):
        return None
    host, pid, started_at, token = (holder[name] for name in names)
    well_formed = (
        isinstance(host, str)
        and host
        and type(pid) is int
        and pid > 0
        and isinstance(started_at, str)
        and isinstance(token, str)
        and _TOKEN.fullmatch(token)
    )
    return LockHolder(host, pid, started_at, token) if well_formed else None


def remove_leftover(found: WorkFolder | None) -> ValidationIssue | None:
    """Remove a work folder left by a conversion that is gone; leave anything else.

    An :data:`OWNED` folder is removed whole: its ``files/`` first, then its owner file and
    the folder (``rmdir``), so one only partly removed is still owned; an :data:`UNFINISHED`
    one by unlinking its one owner file, if any, and an ``rmdir``, which removes only an
    empty folder. Each is checked again right before, so what changed since is left.

    Returns
    -------
    ValidationIssue or None
        ``conversion_work_folder_not_removed``, naming the folder, when the file system
        refused a removal (a file held open by an antivirus or a sync client, or a change
        meanwhile, issue #488); None otherwise.
    """
    if found is None or found.state not in (OWNED, UNFINISHED):
        return None
    again = work_folder_state(found.path.parent, found.path.name)
    if again is None or again.state != found.state:
        return None
    try:
        if os.path.lexists(found.path / FILES_FOLDER):
            shutil.rmtree(found.path / FILES_FOLDER)
        (found.path / OWNER_FILENAME).unlink(missing_ok=True)
        found.path.rmdir()
    except OSError as exc:
        logger.debug("Cannot remove the work folder %s", found.path, exc_info=True)
        return work_folder_not_removed_issue(found.path.parent, found.path.name, exc)
    return None


def _unfinished(work: Path) -> bool:
    """Return True for a real folder that is empty or holds only an empty or cut-short owner."""
    try:
        if not stat.S_ISDIR(os.lstat(work).st_mode):
            return False
        names = os.listdir(work)
        if not names:
            return True
        owner = work / OWNER_FILENAME
        found = os.lstat(owner)
        if names != [OWNER_FILENAME] or not stat.S_ISREG(found.st_mode):
            return False
        if found.st_size > _OWNER_MAX:
            return False
        data = owner.read_bytes()
    except OSError:
        return False
    if not _OWNER_START.startswith(data) and not data.startswith(_OWNER_START):
        return False
    try:
        json.loads(data)
    except ValueError:
        return True  # cut short: what a conversion writes, but not all of it
    return False


def _only_owner_and_files(work: Path) -> bool:
    """Return True when the work folder holds its owner file and at most a real ``files/``."""
    try:
        names = set(os.listdir(work))
        files = work / FILES_FOLDER
        return names <= {OWNER_FILENAME, FILES_FOLDER} and (
            FILES_FOLDER not in names or stat.S_ISDIR(os.lstat(files).st_mode)
        )
    except OSError:
        return False


def _live(holder: LockHolder) -> bool:
    """Return True when the holder may still be writing its work folder.

    In this process, while its conversion runs; elsewhere, only a process known to be running
    on this computer (POSIX only, :func:`.run.lock.holder_running`): on Windows, or on another
    computer, the holder counts as gone, and the copy tree's lock is the guard.
    """
    if holder.host == socket.gethostname() and holder.pid == os.getpid():
        return holder.token in _WRITING
    return holder_running(holder)


def _make_work_folder(work: Path, holder: LockHolder) -> None:
    """Make the work folder (it must not exist) and write its owner file before any copy."""
    work.mkdir()
    owner = {"kind": OWNER_KIND, "holder": asdict(holder)}
    with open(work / OWNER_FILENAME, "x", encoding="utf-8") as file:
        json.dump(owner, file, sort_keys=True)
        file.flush()
        os.fsync(file.fileno())


def _clean_up(
    route: _Route, work: tuple[Path, str] | None, written: list[Path], made: list[Path]
) -> None:
    """Remove what a failed conversion wrote: its work folder, its copies, its folders.

    ``work`` is the work folder this conversion made, with its token. Nothing is removed
    through a link or mount point that appeared meanwhile (#467): such a path is left, and
    logged.
    """
    for path in written:
        if route.clear(path):
            _remove(path)
    if work is not None and route.clear(work[0]):
        _remove_work_folder(*work)
    _remove_empty([folder for folder in made if route.clear(folder)])


class _Diverted(Exception):
    """A link or mount point appeared in the copy tree during the conversion (#467)."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


class _Route:
    """Check that a path below the copy tree goes through no link or mount point (#467).

    :func:`.conversion_reuse.links_below` checks the whole copy tree once, before anything
    is written; this checks again, right before each copy is written and moved into place
    and before the clean-up removes anything, only the folders on that copy's way: one
    :func:`os.lstat` per level, the mount table read once. A link swapped in between this
    check and the write or move is not caught: that needs writes relative to an open folder
    that follow no link (``dir_fd``, ``O_NOFOLLOW``, ``renameat``), which Windows does not
    have and the OME-Zarr writer, writing by path, could not use.
    """

    def __init__(self, dest: Path) -> None:
        self.dest = dest
        self.mounts = MountCheck(dest)

    def check(self, path: Path) -> None:
        """Raise :class:`_Diverted` naming the first link or mount point on the way to ``path``.

        ``path`` itself is checked too; a level that does not exist ends the check (nothing
        below it can be gone through).
        """
        current = self.dest
        for part in path.relative_to(self.dest).parts:
            current = current / part
            try:
                found = os.lstat(current)
            except FileNotFoundError:
                return
            name = current.relative_to(self.dest).as_posix()
            if is_link_stat(found):
                raise _Diverted(f"{name} -> {os.readlink(current)}")
            if self.mounts.is_mounted_folder(current, found):
                raise _Diverted(name)

    def clear(self, path: Path) -> bool:
        """Return True when ``path`` can be removed: no link or mount point on its way."""
        try:
            self.check(path)
        except (_Diverted, OSError):
            logger.warning("%s is not removed: a link or mount point is on its way", path)
            return False
        return True


def _remove_work_folder(work: Path, token: str) -> None:
    """Remove this conversion's work folder; one that cannot be removed is left for the next.

    Only while its owner file still holds this conversion's token (issue #482): after a lock
    removed by mistake, another conversion may have replaced the folder; it is then left.
    """
    holder = read_owner(work)
    if holder is None or holder.token != token:
        logger.warning("The work folder %s is not removed: it is no longer this conversion's", work)
        return
    try:
        shutil.rmtree(work)
    except OSError:
        logger.warning("Cannot remove the work folder %s; the next conversion will", work)


def _missing_folders(dest: Path, to_write: list[PlannedCopy]) -> list[Path]:
    """Return the folders of the copy tree that writing these copies will make."""
    missing = set()
    for item in to_write:
        folder = (dest / item.relative_path).parent
        while folder != dest and not folder.exists():
            missing.add(folder)
            folder = folder.parent
    return sorted(missing, key=lambda folder: len(folder.parts), reverse=True)


def _remove_empty(folders: list[Path]) -> None:
    """Remove the folders this conversion made, deepest first, when they are empty."""
    for folder in folders:
        try:
            folder.rmdir()
        except OSError:
            logger.debug("Folder %s kept: not empty or gone", folder, exc_info=True)


class _SourceChanged(Exception):
    """A source image changed since it was checked, or while it was copied."""


def _write_partial(root: Path, work: Path, item: PlannedCopy, memory_limit: int) -> str | None:
    """Write one copy in the work folder, refusing a source that changes meanwhile.

    Returns the source's content SHA-256 for a projected copy (taken before and after the
    projection, which must agree), else None.
    """
    source, partial = root / item.source_path, partial_path(work, item)
    partial.parent.mkdir(parents=True, exist_ok=True)
    if current_fingerprint(source) != item.entry.source:
        raise _SourceChanged("it changed since it was checked")
    content = None
    if item.entry.projection is not None:
        content = source_content_sha256(source)
        details = item.entry.projection.with_content(content)
        project = write_projected_zarr if item.store == OME_ZARR else write_projected_copy
        project(source, partial, item.layout, item.mapping, details, memory_limit=memory_limit)
        if source_content_sha256(source) != content:
            raise _SourceChanged("its content changed while it was projected")
    elif item.layout is not None:
        write = write_named_zarr if item.store == OME_ZARR else write_named_copy
        write(
            source, partial, item.layout, item.mapping, dtype=item.dtype, memory_limit=memory_limit
        )
    elif source.is_dir():
        shutil.copytree(source, partial, copy_function=shutil.copyfile)
    else:
        shutil.copyfile(source, partial)
    if current_fingerprint(source) != item.entry.source:
        raise _SourceChanged("it changed while it was copied")
    return content


def partial_path(work: Path, item: PlannedCopy) -> Path:
    """Return the path in the work folder where a copy is written before its move."""
    return work / FILES_FOLDER / item.relative_path


def _remove(path: Path) -> None:
    """Remove a copy this conversion moved into place (a file, or an OME-Zarr folder)."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)
