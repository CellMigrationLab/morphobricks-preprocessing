"""Whether a file the user names may be written there (contract §8; owner, 3 November 2026).

The shared rule behind the command line's ``--log`` (:mod:`.log_target`) and any other
brick's file named by the user, such as MorphoFeature's ``--to-segment``: a file a command
writes beside its real outputs must never become a source, change one, or write over an
output. :func:`write_target_refusal` refuses a path

- inside a protected folder (the experiment, masks or output folder, ...);
- that is a protected file (an input such as a design file);
- as, or inside, a protected name of an output folder (compared ignoring case), or as that
  output folder itself;
- that is an existing folder, or whose folder does not exist;
- that is an existing file with another name too (a hard link: it may be a source or an
  output under that name).

Paths are compared resolved, so a link to a protected file or folder is refused as it; a
folder that exists also by device and inode (another spelling of it on a file system that
ignores case, macOS and Windows); a protected file by :func:`os.path.samefile`. The rule
reads the file system (``stat``) and writes nothing. It is a check made before the caller
opens the file, not a lock: what changes in between is not seen (a file or hard link
created at the path, the path or a folder above it replaced by a link into a protected
folder, a protected folder created).
"""

from __future__ import annotations

import os
from collections.abc import Collection, Mapping
from pathlib import Path
from types import MappingProxyType

from .links import parts_below

_NONE: Mapping[str, str | os.PathLike | None] = MappingProxyType({})


def write_target_refusal(
    path: str | os.PathLike,
    *,
    protected_folders: Mapping[str, str | os.PathLike | None],
    protected_files: Mapping[str, str | os.PathLike | None] = _NONE,
    output: str | os.PathLike | None = None,
    output_names: Collection[str] = (),
) -> str | None:
    """Return why a file may not be written at ``path``, or None when it may.

    The checks run in this order and the first that refuses gives the reason: protected
    files, protected folders, the output folder's names, an existing folder, a missing
    folder, a hard link.

    Parameters
    ----------
    path
        The file asked for; it need not exist.
    protected_folders
        Folders the file may not be in (or be), by what they are, for example
        ``{"experiment folder": root}``; None values are left out. A folder that does not
        exist is compared by its resolved path only.
    protected_files
        Files the path may not be, by what they are, for example
        ``{"design file given with --design": design}``; None values are left out.
    output
        An output folder in which only ``output_names`` (and the folder itself) are
        refused; None when there is none. Any other name in it is allowed.
    output_names
        The names in ``output`` the file may not be, or be inside (compared ignoring case).

    Returns
    -------
    str or None
        The reason as a clause for the caller's own message, for example ``"it is inside the
        masks folder (/data/masks)"``; None when the file may be written. Folders and files
        are named resolved.

    Raises
    ------
    TypeError
        If ``output_names`` is a single string rather than a collection of names.
    """
    if isinstance(output_names, str):  # a lone name would be compared letter by letter
        raise TypeError("output_names is a collection of names, not one name")
    for what, file in protected_files.items():
        if file is not None and same_file(path, file):
            return f"it is the {what} ({Path(file).resolve()})"
    target = Path(path).resolve()
    for what, folder in protected_folders.items():
        if folder is not None and parts_below(target, Path(folder)) is not None:
            return f"it is inside the {what} ({Path(folder).resolve()})"
    parts = None if output is None else parts_below(target, Path(output))
    if parts is not None:
        folded = {name.casefold() for name in output_names}
        if not parts or parts[0].casefold() in folded:
            return f"it would write over the run's {parts[0] if parts else 'folder'}"
    if target.is_dir():
        return "it is a folder"
    if not target.parent.is_dir():
        return f"its folder {target.parent} does not exist"
    if target.is_file() and target.stat().st_nlink > 1:
        return "it is a file that has another name too (a hard link)"
    return None


def same_file(one: str | os.PathLike, other: str | os.PathLike) -> bool:
    """Return True when two paths name one file: resolved, or by device and inode.

    Parameters
    ----------
    one, other
        The paths; they need not exist.

    Returns
    -------
    bool
        True when they resolve to one path, or both exist and :func:`os.path.samefile`.
    """
    one, other = Path(one), Path(other)
    if one.resolve() == other.resolve():
        return True
    try:
        return os.path.samefile(one, other)
    except OSError:  # one of them does not exist: not the same file
        return False
