"""Symbolic links in the experiment and masks folders (issue #81; owner, 2 November 2026).

The rule is **contain**: a link (to a file or a folder) is followed only when its real path
(every link resolved) stays inside the folder that is walked, the experiment folder or the
masks folder, itself resolved. Nothing outside the folder the user selected is read:

- a link whose real path leaves the folder is not followed, and is the error
  ``link_outside_folder`` naming each link and its target;
- a link to a folder on the walk's own path (the folder holding it or one above) is not
  followed, and is the error ``link_loop``;
- two paths of one file (a link and its target, two links to it, or two hard links: the
  same device and inode) would count one image or mask twice under two names: the error
  ``linked_twice`` names both (hard links: owner, 2 November 2026).

Inside an OME-Zarr store, which discovery never enters as a folder level, every link is
held to the same rule (:meth:`LinkGuard.check_store`), since reading and copying the store
follow its links.

A link is what Python reports as one: a symbolic link (``os.DirEntry.is_symlink``) and, on
Windows, also a directory junction (a mount-point reparse point), which Python 3.11 does
not report as a link. Real paths come from :func:`os.path.realpath`. One
:class:`LinkGuard` serves one walk of one folder.
"""

from __future__ import annotations

import logging
import os
import stat
from collections.abc import Callable, Iterator
from pathlib import Path

from .validation import ValidationIssue, agree, error, plural

logger = logging.getLogger(__name__)

LINK_OUTSIDE = "link_outside_folder"
LINK_LOOP = "link_loop"
LINKED_TWICE = "linked_twice"
LINK_CODES = frozenset({LINK_OUTSIDE, LINK_LOOP, LINKED_TWICE})
"""The codes of the errors about links (all errors)."""

_JUNCTION = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None)


class LinkRefusedError(ValueError):
    """A folder holds links that are refused; the message names them."""


def is_link(entry: os.DirEntry) -> bool:
    """Return True for a symbolic link, or a directory junction on Windows.

    Parameters
    ----------
    entry
        A folder entry from :func:`os.scandir`.

    Returns
    -------
    bool
    """
    if entry.is_symlink():
        return True
    if os.name != "nt" or _JUNCTION is None:
        return False
    try:
        return getattr(entry.stat(follow_symlinks=False), "st_reparse_tag", 0) == _JUNCTION
    except OSError:
        return False


def is_inside(path: Path, folder: Path) -> bool:
    """Return True when ``path`` is ``folder`` or below it (both resolved)."""
    return path == folder or folder in path.parents


def parts_below(target: Path, folder: Path) -> tuple[str, ...] | None:
    """Return the names of ``target`` (resolved) below ``folder``; None when it is not inside.

    Compared by resolved path, then, for each existing folder above ``target``, by device and
    inode, which catches another spelling of ``folder`` on a file system that ignores case.

    Parameters
    ----------
    target
        The path, resolved; it need not exist.
    folder
        The folder; resolved here (compared by device and inode only when it exists).

    Returns
    -------
    tuple of str or None
        The names from ``folder`` down to ``target`` (empty for ``folder`` itself), or None.
    """
    real = folder.resolve()
    if is_inside(target, real):
        return target.relative_to(real).parts
    try:
        wanted = os.stat(real)
    except OSError:  # a folder that does not exist holds nothing
        return None
    chain = [target, *target.parents]
    for depth, above in enumerate(chain):
        try:
            if os.path.samestat(os.stat(above), wanted):
                return tuple(path.name for path in reversed(chain[:depth]))
        except OSError:  # not there (yet): nothing to compare
            continue
    return None


def relative_inside(path: Path, folder: str | os.PathLike) -> str | None:
    """Return ``path`` (resolved) relative to ``folder`` when it is inside, ``"."`` for it.

    Parameters
    ----------
    path
        The path, resolved; it need not exist.
    folder
        The folder, compared as :func:`parts_below` does.

    Returns
    -------
    str or None
        The relative path in POSIX form, or None when ``path`` is not inside ``folder``.
    """
    parts = parts_below(path, Path(folder))
    return None if parts is None else Path(*parts).as_posix()


class LinkGuard:
    """Follow the links of one walk of ``root`` only when they stay inside it.

    Parameters
    ----------
    root
        The folder walked, as the walk names it; links are held to its real path.
    what
        What it is, for the messages (``"experiment folder"``, ``"masks folder"``).
    """

    def __init__(self, root: Path, what: str) -> None:
        self.root = root
        self.real_root = Path(os.path.realpath(root))
        self.what = what
        # The real paths of the folders above each folder entered, the folder's own last.
        self._stacks: dict[Path, tuple[Path, ...]] = {root: (self.real_root,)}
        self._owners: dict[Path | tuple[int, int], str] = {}
        self.outside: list[str] = []
        self.loops: list[str] = []
        self.twice: list[str] = []

    def relative(self, path: Path) -> str:
        """Return a path of the walk relative to the root, with ``/`` separators."""
        return path.relative_to(self.root).as_posix() or "."

    def real(self, entry: os.DirEntry, folder: Path) -> Path | None:
        """Return the real path of an entry of ``folder``, or None for a refused link.

        ``folder`` is the root or a folder this guard let through. A folder let through can
        be walked next.
        """
        stack = self._stacks[folder]
        if not is_link(entry):
            target = stack[-1] / entry.name
        else:
            target = Path(os.path.realpath(entry.path))
            shown = f"{self.relative(Path(entry.path))} -> {target}"
            if not is_inside(target, self.real_root):
                self.outside.append(shown)
                return None
            if target in stack:
                self.loops.append(shown)
                return None
        if entry.is_dir():
            self._stacks[Path(entry.path)] = (*stack, target)
        return target

    def claim(self, path: Path, real: Path) -> str:
        """Record that ``path``, an image or mask of the walk, is ``real``; return its name.

        A file is known by its device and inode (from the one ``stat`` this takes), so a
        hard link to an image already found is caught like a symbolic one; by its real
        path when it cannot be stat'ed (a broken link) or has no inode. An OME-Zarr store
        (a folder) also has its links checked (:meth:`check_store`).
        """
        try:
            found = os.stat(path)
        except OSError:  # a broken link: refused or reported unreadable by its reader
            found = None
        if found is not None and stat.S_ISDIR(found.st_mode):
            self.check_store(path)
        key = (found.st_dev, found.st_ino) if found is not None and found.st_ino else real
        name = self.relative(path)
        first = self._owners.setdefault(key, name)
        if first != name:
            self.twice.append(f"{name} (the same file as {first})")
        return name

    def files(self, folder: Path, skip: Callable[[str], bool]) -> Iterator[tuple[Path, Path]]:
        """Yield ``(path, real path)`` of every file below ``folder``, sorted by path.

        Folders and files whose name ``skip`` accepts are left out; links are followed under
        this guard's rule, and a folder that cannot be listed is left out (logged).
        """
        try:
            entries = sorted(os.scandir(folder), key=lambda entry: entry.name)
        except OSError:
            logger.debug("Cannot list %s", folder, exc_info=True)
            return
        for entry in entries:
            if skip(entry.name):
                continue
            real = self.real(entry, folder)
            if real is None:
                continue
            if entry.is_dir():
                yield from self.files(Path(entry.path), skip)
            else:
                yield Path(entry.path), real

    def check_store(self, store: Path) -> None:
        """Hold every link inside an OME-Zarr store (a folder let through) to the rule.

        A store can hold millions of chunk files: only its folders and its links are
        followed up, so a file costs no more than its listing.
        """
        pending = [store]
        while pending:
            folder = pending.pop()
            try:
                entries = list(os.scandir(folder))
            except OSError:
                logger.debug("Cannot list %s", folder, exc_info=True)
                continue
            for entry in entries:
                if is_link(entry):
                    if self.real(entry, folder) is not None and entry.is_dir():
                        pending.append(Path(entry.path))
                elif entry.is_dir(follow_symlinks=False):
                    stack = self._stacks[folder]
                    self._stacks[Path(entry.path)] = (*stack, stack[-1] / entry.name)
                    pending.append(Path(entry.path))

    def issues(self) -> list[ValidationIssue]:
        """Return the errors about the links found so far."""
        issues = []
        if self.outside:
            issues.append(
                error(
                    LINK_OUTSIDE,
                    f"{plural(len(self.outside), 'link')} in the {self.what} "
                    f"{agree(self.outside, 'leads', 'lead')} outside it (link -> target), "
                    "and nothing outside the selected folder is read: not followed.",
                    self.outside,
                    f"Copy or move the files into the {self.what}, or select a folder that "
                    "holds them, then remove the links.",
                )
            )
        if self.loops:
            issues.append(
                error(
                    LINK_LOOP,
                    f"{plural(len(self.loops), 'link')} in the {self.what} "
                    f"{agree(self.loops, 'leads', 'lead')} back to a folder that "
                    "holds it, so the folder would be read again and again; not followed.",
                    self.loops,
                    "Remove these links.",
                )
            )
        if self.twice:
            issues.append(
                error(
                    LINKED_TWICE,
                    f"{plural(len(self.twice), 'path')} in the {self.what} "
                    f"{agree(self.twice, 'is', 'are')} the same file as another path (through a "
                    "link, or a hard link), so one file would count twice, under two names.",
                    self.twice,
                    "Keep one path to each file: remove the link (or the hard link), or "
                    "replace it with a copy if both are really meant.",
                )
            )
        return issues


def refuse_links(issues: list[ValidationIssue] | tuple[ValidationIssue, ...]) -> None:
    """Raise :class:`LinkRefusedError` when the issues hold an error about links.

    Parameters
    ----------
    issues
        Issues of a walk (discovery's, or the masks folder's).

    Raises
    ------
    LinkRefusedError
        Naming every refused link.
    """
    found = [issue for issue in issues if issue.code in LINK_CODES]
    if found:
        raise LinkRefusedError("\n".join(str(issue) for issue in found))
