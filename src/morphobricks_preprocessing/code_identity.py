"""The verified code identity of an installed distribution (Spec ADR 0005 BM15 "Code identity").

A derived region mask records, for each distribution whose code computed it, an object with the
keys of :data:`.lineage_schema.IDENTITY_KEYS`: ``distribution``, ``version`` (the installed
metadata's), ``verified``, ``record_sha256`` (the SHA-256 of the exact bytes of the installed
``RECORD``, read as bytes and never parsed first), ``vcs_commit`` (PEP 610 ``direct_url.json``
``vcs_info.commit_id``, extra provenance only) and ``reason`` (the first failed check, or None).

The identity is verified only when, in this order: (1) the install is not editable (PEP 610
``dir_info.editable``); (2) ``RECORD`` is present and readable; (3) every file ``RECORD`` lists
with a hash still has it; (4) every ``.py`` file and extension module (``.so``, ``.pyd``) on disk
under the distribution's import packages, ``__pycache__`` excepted, is in ``RECORD`` with a hash;
(5) every imported module of those packages has a ``__file__`` whose real path is that of a
hashed ``RECORD`` entry, so a source tree or another installation earlier on ``sys.path`` is
never taken for this one. A ``RECORD`` that places no import package at all (an install whose
code is reached through a ``.pth`` path, as an editable install whose installer wrote no PEP 610
``dir_info``) is never verified, reason ``"no import package in RECORD"``: checks (4) and (5)
would otherwise hold vacuously for code run from a source tree. A version or a commit alone is
never an identity: two builds of one commit with different ``RECORD`` bytes are two identities.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from importlib import metadata
from pathlib import Path
from types import ModuleType

from .lineage_schema import IDENTITY_KEYS

EXECUTABLE_SUFFIXES = (".py", ".so", ".pyd")
"""The files of an import package that run as code (Python sources and extension modules)."""


def code_identity(
    distribution: metadata.Distribution | str,
    modules: Mapping[str, ModuleType | None] | None = None,
) -> dict[str, object]:
    """Return the code identity of one installed distribution, verified or with its reason.

    Parameters
    ----------
    distribution
        The installed distribution, or its name (looked up on ``sys.path``).
    modules
        The imported modules to check (check 5); ``sys.modules`` by default.

    Returns
    -------
    dict
        The keys of :data:`.lineage_schema.IDENTITY_KEYS`, in order, all present.
    """
    dist = metadata.distribution(distribution) if isinstance(distribution, str) else distribution
    record_bytes = _record_bytes(dist)
    reason = _first_failure(dist, record_bytes, sys.modules if modules is None else modules)
    values = (
        dist.metadata["Name"],
        dist.version,
        reason is None,
        None if record_bytes is None else hashlib.sha256(record_bytes).hexdigest(),
        _vcs_commit(dist),
        reason,
    )
    return dict(zip(IDENTITY_KEYS, values, strict=True))


def _direct_url(dist: metadata.Distribution) -> dict:
    """Return PEP 610 ``direct_url.json`` as a dict, or an empty one."""
    try:
        text = dist.read_text("direct_url.json")
        found = json.loads(text) if text else {}
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _vcs_commit(dist: metadata.Distribution) -> str | None:
    """Return the commit PEP 610 records for a VCS install, else None."""
    info = _direct_url(dist).get("vcs_info")
    commit = info.get("commit_id") if isinstance(info, dict) else None
    return commit if isinstance(commit, str) else None


def _record_path(dist: metadata.Distribution) -> Path | None:
    """Return the path of the distribution's ``RECORD`` file, or None when it has no folder."""
    folder = getattr(dist, "_path", None)  # PathDistribution: the .dist-info folder
    return None if folder is None else Path(folder) / "RECORD"


def _record_bytes(dist: metadata.Distribution) -> bytes | None:
    """Return the exact bytes of ``RECORD``, or None when it is missing or unreadable."""
    path = _record_path(dist)
    try:
        return None if path is None else path.read_bytes()
    except OSError:
        return None


def _first_failure(
    dist: metadata.Distribution,
    record_bytes: bytes | None,
    modules: Mapping[str, ModuleType | None],
) -> str | None:
    """Return the reason of the first failed check (1)-(5), or None when all pass."""
    info = _direct_url(dist).get("dir_info")
    if isinstance(info, dict) and info.get("editable") is True:
        return "editable install"
    files = dist.files if record_bytes is not None else None
    if files is None:
        return "RECORD missing"
    hashed: dict[str, str] = {}
    for item in files:
        if item.hash is None:
            continue
        location = Path(dist.locate_file(item))
        if not _hash_matches(location, item.hash.mode, item.hash.value):
            return f"hash differs: {item.as_posix()}"
        hashed[os.path.realpath(location)] = item.as_posix()
    listed = {os.path.realpath(dist.locate_file(item)) for item in files}
    packages = _import_packages(dist, files)
    if not packages:  # checks (4) and (5) would hold for no code at all (a .pth install)
        return "no import package in RECORD"
    for folder in packages:
        for path in sorted(folder.rglob("*")):
            if "__pycache__" in path.parts or path.suffix not in EXECUTABLE_SUFFIXES:
                continue
            real = os.path.realpath(path)
            if real not in listed:
                return f"not in RECORD: {_shown(dist, path)}"
            if real not in hashed:
                return f"no hash in RECORD: {_shown(dist, path)}"
    return _module_failure(packages, hashed, modules)


def _hash_matches(path: Path, mode: str, value: str) -> bool:
    """Return True when a file still has the hash ``RECORD`` lists for it."""
    try:
        digest = hashlib.new(mode, path.read_bytes()).digest()
    except (OSError, ValueError):
        return False
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii") == value


def _import_packages(dist: metadata.Distribution, files: list) -> list[Path]:
    """Return the import package folders ``RECORD`` places files in, sorted.

    A top-level folder of ``RECORD`` holding an ``__init__.py`` entry is an import package;
    the ``.dist-info`` and ``.data`` folders and paths outside the installation are not.
    """
    names = sorted(
        {
            item.parts[0]
            for item in files
            if len(item.parts) == 2 and item.parts[1] == "__init__.py" and item.parts[0] != ".."
        }
    )
    return [Path(dist.locate_file(name)) for name in names]


def _module_failure(
    packages: list[Path], hashed: Mapping[str, str], modules: Mapping[str, ModuleType | None]
) -> str | None:
    """Return the first imported module of the packages not run from a hashed entry."""
    names = {folder.name for folder in packages}
    for name in sorted(modules):
        if name.split(".", 1)[0] not in names or modules[name] is None:
            continue
        where = getattr(modules[name], "__file__", None)
        if not where or os.path.realpath(where) not in hashed:
            return f"module not from this installation: {name}"
    return None


def _shown(dist: metadata.Distribution, path: Path) -> str:
    """Return a file's path relative to the installation folder, with ``/``."""
    base = Path(dist.locate_file(""))
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()
