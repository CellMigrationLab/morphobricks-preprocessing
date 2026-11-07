"""The vendored MorphoBricks-Spec files: where they are, what they must hash to, what they hold.

Conformance plan sections 1 and 2: the fixtures are pinned by the full Spec commit SHA and the
vendored folder must hash to ``git rev-parse <sha>:fixtures``. Nothing here reads a branch or
the network. ``MORPHOBRICKS_SPEC_ROOT`` points the tests at another folder laid out like
``tests/spec_fixtures`` (``tests/test_spec_vendored_missing.py`` uses an empty one to prove
that a missing tree fails the suite instead of passing on defaults).
"""

from __future__ import annotations

import hashlib
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

VENDORED = Path(__file__).parent / "spec_fixtures"
ROOT_ENV = "MORPHOBRICKS_SPEC_ROOT"
KINDS = ("valid", "invalid", "transitions")
CODE = re.compile(r"\b([EWRA]_[A-Z0-9_]+)\b")
ROW = re.compile(r"\| \[`([vit]\d+[^`]*)`\]\((?:valid|invalid|transitions)/[^)]+\) \|(.*)")


class SpecPinError(Exception):
    """Raised when the vendored Spec files are missing, unreadable or malformed."""


def spec_root() -> Path:
    """Return the folder holding ``fixtures/``, ``docs/`` and ``PIN.toml``."""
    override = os.environ.get(ROOT_ENV)
    return Path(override) if override else VENDORED


def read_pin(root: Path | None = None) -> dict:
    """Return the parsed ``PIN.toml``, or raise ``SpecPinError`` when it is absent."""
    path = (root or spec_root()) / "PIN.toml"
    if not path.is_file():
        raise SpecPinError(f"{path} is missing: the Spec fixtures are not vendored here")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def git_blob_id(data: bytes) -> str:
    """Return the git blob-object ID of ``data`` (``git hash-object``)."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def git_tree_id(folder: Path) -> str:
    """Return the git tree-object ID of a folder, as ``git write-tree`` would give it.

    Every file counts as mode 100644 (the vendored copy has no executable files, and a
    file system without mode bits could not tell). A symbolic link, a missing folder or
    anything that is neither a file nor a folder raises ``SpecPinError``.
    """
    if not folder.is_dir() or folder.is_symlink():
        raise SpecPinError(f"{folder} is not a folder")
    entries = []
    for entry in os.scandir(folder):
        name = entry.name.encode("utf-8")
        if entry.is_symlink():
            raise SpecPinError(f"{entry.path} is a symbolic link")
        if entry.is_dir():
            entries.append((name + b"/", b"40000", name, bytes.fromhex(git_tree_id(Path(entry)))))
        elif entry.is_file():
            blob = git_blob_id(Path(entry).read_bytes())
            entries.append((name, b"100644", name, bytes.fromhex(blob)))
        else:
            raise SpecPinError(f"{entry.path} is neither a file nor a folder")
    body = b"".join(mode + b" " + name + b"\0" + sha for _, mode, name, sha in sorted(entries))
    return hashlib.sha1(b"tree %d\0" % len(body) + body).hexdigest()


def pin_problems(root: Path | None = None) -> list[str]:
    """Return what differs between the vendored files and the pin (empty when they match)."""
    root = root or spec_root()
    pin = read_pin(root)
    problems = []
    if not re.fullmatch(r"[0-9a-f]{40}", str(pin.get("commit", ""))):
        problems.append("the pin is not a full 40-digit commit SHA")
    tree = git_tree_id(root / "fixtures")
    if tree != pin.get("fixtures_tree"):
        problems.append(f"fixtures/ hashes to {tree}, the pin says {pin.get('fixtures_tree')}")
    for relative, blob in pin.get("files", {}).items():
        path = root / relative
        if not path.is_file():
            problems.append(f"{relative} is missing")
        elif git_blob_id(path.read_bytes()) != blob:
            problems.append(f"{relative} does not hash to its pin {blob}")
    return problems


def codes_in(text: str) -> frozenset[str]:
    """Return every ``E_``/``W_``/``R_``/``A_`` code named in a piece of text."""
    return frozenset(CODE.findall(text))


@dataclass(frozen=True)
class Fixture:
    """One fixture file of the Spec: ``valid/``, ``invalid/`` or ``transitions/``."""

    name: str
    kind: str
    path: Path

    @property
    def data(self) -> bytes:
        """Return the raw bytes, never decoded text (``i01_bom`` must reach the reader)."""
        return self.path.read_bytes()

    @property
    def header(self) -> dict[str, str]:
        """Return the ``# Fixture``, ``# Expected`` and ``# Source`` comment lines by name."""
        text = self.data.decode("utf-8", errors="replace").lstrip("﻿")
        fields = {}
        for line in text.splitlines():
            match = re.fullmatch(r"# (Fixture|Expected|Source)(?::| )\s*(.*)", line)
            if match and match.group(1) not in fields:
                fields[match.group(1)] = match.group(2)
        return fields

    @property
    def expected_codes(self) -> frozenset[str]:
        """Return the codes of the ``Expected`` header (the whole set, errors or warnings)."""
        return codes_in(self.header.get("Expected", ""))

    @property
    def expected_warnings(self) -> frozenset[str]:
        """Return the warning codes a valid fixture is expected to give."""
        return frozenset(c for c in self.expected_codes if c.startswith("W_"))

    @property
    def phase(self) -> int:
        """Return the validation phase of an invalid fixture, from the catalogue."""
        return int(catalogue()[self.name][2])


def _number(path: Path) -> int:
    match = re.match(r"\D(\d+)", path.stem)
    if not match:
        raise SpecPinError(f"{path.name} is not named like a fixture")
    return int(match.group(1))


def fixtures(kind: str, root: Path | None = None) -> list[Fixture]:
    """Return the fixtures of one kind in number order, discovered from the directory.

    Raises
    ------
    SpecPinError
        When the folder is missing or holds no fixture: a missing tree is a failure, never
        an empty parametrisation that passes.
    """
    folder = (root or spec_root()) / "fixtures" / kind
    found = sorted(folder.glob("*.toml"), key=_number) if folder.is_dir() else []
    if not found:
        raise SpecPinError(f"no fixtures in {folder}: the Spec fixtures are not vendored here")
    return [Fixture(path.stem, kind, path) for path in found]


def catalogue(root: Path | None = None) -> dict[str, list[str]]:
    """Return the cells of each table row of ``fixtures/README.md`` by fixture name.

    The cells after the name: for ``valid/`` source, expected, homogeneous, case; for
    ``invalid/`` source, codes, phase, case; for ``transitions/`` predecessor, expected, case.
    """
    path = (root or spec_root()) / "fixtures" / "README.md"
    if not path.is_file():
        raise SpecPinError(f"{path} is missing: the Spec fixtures are not vendored here")
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = ROW.match(line)
        if match:
            rows[match.group(1)] = [cell.strip() for cell in match.group(2).split("|")]
    return rows


def all_fixtures(root: Path | None = None) -> list[Fixture]:
    """Return every fixture of every kind."""
    return [item for kind in KINDS for item in fixtures(kind, root)]
