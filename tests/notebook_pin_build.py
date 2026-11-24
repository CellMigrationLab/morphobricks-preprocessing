"""The build half of the pin check (:mod:`notebook_pin`): what ``pip`` builds from a commit.

Moved from ``notebook_pin.py`` unchanged (600-line rule, #441) and re-exported there.
:func:`install_table` keeps the part of ``pyproject.toml`` that changes what is installed;
:func:`tree_blobs` and :func:`_blob_texts` read a commit with Git.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

PACKAGE = "morphobricks_preprocessing"
SOURCE_ROOT = "src"
HATCH_KEYS = frozenset(
    {"metadata.allow-direct-references", "build.targets.wheel.packages", "build.exclude"}
)
"""The ``[tool.hatch]`` settings the check knows to add no file outside the package. They are
compared, so a change of one is reported as ``pyproject.toml``: ``build.exclude`` applies to
every target, the wheel included, but only leaves files out. Any other setting
(``force-include``, ``sources``, build hooks, a version source) or a ``hatch.toml`` can put
other files in the wheel and is refused."""
HATCH_IGNORED = frozenset({"build.targets.sdist", "envs"})
"""The ``[tool.hatch]`` tables that do not change the wheel pip builds from Git, left out of
the comparison: pip builds the wheel from the source tree, not from an sdist, and ``envs``
are Hatch's development environments."""
BACKEND_REQUIRES = {"hatchling.build": "hatchling"}
"""The one requirement of each known backend. A further one (a hatch plugin) may read any
``[tool]`` table, so then the whole ``pyproject.toml`` is compared."""
BACKEND_TABLES = {"hatchling.build": "hatch"}
"""The ``[tool]`` table each known build backend reads. Every other ``[tool]`` table (Ruff,
pytest, the code rules) is read by no build and by no code of the package, so a change there
installs the same thing. An unknown backend is refused: what it puts in the wheel is unknown."""
WHEEL_PACKAGES = [f"{SOURCE_ROOT}/{PACKAGE}"]
"""The wheel's ``packages``: imports are resolved in this one package, so a second top-level
package in the wheel is refused."""


class PinScopeError(Exception):
    """Raised when what a notebook or the package uses cannot be derived from the source."""


def install_table(text: str) -> dict:
    """Return ``pyproject.toml`` without the ``[tool]`` tables no build reads.

    ``[project]`` (dependencies, extras, version, scripts) and ``[build-system]`` are kept
    whole; of ``[tool]`` only the build backend's own table is kept, without
    :data:`HATCH_IGNORED` (all of it with a build plugin). Raises ``PinScopeError`` for a
    backend not in :data:`BACKEND_TABLES` or a wheel whose packages are not
    :data:`WHEEL_PACKAGES`.
    """
    data = tomllib.loads(text)
    backend = data.get("build-system", {}).get("build-backend")
    if backend not in BACKEND_TABLES:
        raise PinScopeError(f"pyproject.toml has an unknown build backend: {backend!r}")
    own = _without(data.get("tool", {}).get(BACKEND_TABLES[backend], {}), HATCH_IGNORED)
    unknown = _leaf_keys(own) - HATCH_KEYS
    if unknown:
        raise PinScopeError(f"[tool.hatch] has settings the check does not know: {unknown}")
    wheel = own.get("build", {}).get("targets", {}).get("wheel", {})
    if wheel.get("packages") != WHEEL_PACKAGES:
        raise PinScopeError(f"the wheel's packages are not {WHEEL_PACKAGES}: {wheel}")
    requires = data.get("build-system", {}).get("requires", [])
    names = {re.match(r"[A-Za-z0-9._-]*", r).group().lower() for r in requires}
    if names == {BACKEND_REQUIRES[backend]}:
        data["tool"] = {BACKEND_TABLES[backend]: own}
    return data


def _without(table: dict, dropped: frozenset[str], prefix: str = "") -> dict:
    """Return a copy of ``table`` without the subtables whose dotted key is in ``dropped``."""
    return {
        key: _without(value, dropped, f"{prefix}{key}.") if isinstance(value, dict) else value
        for key, value in table.items()
        if f"{prefix}{key}" not in dropped
    }


def _leaf_keys(table: dict, prefix: str = "") -> set[str]:
    """Return the dotted key of every value in ``table`` that is not itself a table."""
    keys = set()
    for key, value in table.items():
        name = f"{prefix}{key}"
        keys |= _leaf_keys(value, f"{name}.") if isinstance(value, dict) else {name}
    return keys


def _git(repo: Path, *arguments: str, data: bytes | None = None) -> bytes:
    """Return the output of ``git -C repo arguments`` (raises when git fails)."""
    command = ["git", "-C", str(repo), *arguments]
    return subprocess.run(command, input=data, capture_output=True, check=True).stdout


def tree_blobs(repo: Path, rev: str) -> dict[str, str]:
    """Return every file of commit ``rev`` with its blob ID."""
    listing = _git(repo, "ls-tree", "-r", "-z", rev).decode("utf-8").split("\0")
    entries = (line.split("\t", 1) for line in listing if line)
    return {path: meta.split()[2] for meta, path in entries}


def _blob_texts(repo: Path, blobs: list[str]) -> list[str]:
    """Return the text of each blob ID in ``blobs``, read by one ``git cat-file --batch``."""
    out = _git(repo, "cat-file", "--batch", data="".join(f"{b}\n" for b in blobs).encode())
    texts, at = [], 0
    for _ in blobs:
        header_end = out.index(b"\n", at)
        size = int(out[at:header_end].split()[2])
        texts.append(out[header_end + 1 : header_end + 1 + size].decode("utf-8"))
        at = header_end + 2 + size  # the content is followed by one newline
    return texts


def _install_table_at(repo: Path, blobs: dict[str, str]) -> dict:
    """Return :func:`install_table` of a commit's ``pyproject.toml``."""
    if "pyproject.toml" not in blobs:
        raise PinScopeError("a commit has no pyproject.toml: what it installs is unknown")
    if "hatch.toml" in blobs:
        raise PinScopeError("a commit has a hatch.toml: its build settings are not checked")
    return install_table(_blob_texts(repo, [blobs["pyproject.toml"]])[0])
