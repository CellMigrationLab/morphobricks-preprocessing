"""What the experiment-description reader reports: issues with code, table path and value.

Experiment-v1 section 6: every error of the first failing phase is reported, each with its
code, the table path (for example ``repeats.Day1.members.CTRL.technical_repeats.single.
channels[2]``) and the offending value. Warnings are reported only for a file without errors.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from .description_rules import Code

BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")


def key_path(base: str, key: str) -> str:
    """Return the table path of ``key`` under ``base``, quoting a key TOML would quote."""
    name = key if BARE_KEY.fullmatch(key) else f'"{key}"'
    return f"{base}.{name}" if base else name


@dataclass(frozen=True)
class DescriptionIssue:
    """One error or warning: its code, table path and the offending value (None if absent)."""

    code: Code
    path: str
    value: object = None


class Issues:
    """The issues of one validation phase, collected in the order they are found."""

    def __init__(self) -> None:
        self.items: list[DescriptionIssue] = []

    def add(self, code: Code, path: str, value: object = None) -> None:
        """Record one issue."""
        self.items.append(DescriptionIssue(code, path, value))

    def __bool__(self) -> bool:
        return bool(self.items)


@dataclass(frozen=True)
class DescriptionReport:
    """The outcome of reading one file.

    ``phase`` is the phase that reported ``errors`` (1 to 5), or, for a file without errors,
    the last phase that ran. ``document`` is the parsed TOML (never modified, never
    normalized) once phase 1 succeeded, else None. ``sha256`` is the digest of the exact
    bytes read: provenance, not a cache key (experiment-v1 section 2.6).
    """

    sha256: str
    phase: int
    errors: tuple[DescriptionIssue, ...]
    warnings: tuple[DescriptionIssue, ...]
    document: Mapping[str, object] | None

    @property
    def ok(self) -> bool:
        """Return True when the file has no error."""
        return not self.errors

    @property
    def codes(self) -> frozenset[Code]:
        """Return the set of error codes (what conformance compares)."""
        return frozenset(issue.code for issue in self.errors)

    @property
    def warning_codes(self) -> frozenset[Code]:
        """Return the set of warning codes."""
        return frozenset(issue.code for issue in self.warnings)
