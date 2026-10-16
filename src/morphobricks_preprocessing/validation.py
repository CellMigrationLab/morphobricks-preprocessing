"""Validation issues: expected problems reported to the user instead of raised.

Every check in the preflight returns :class:`ValidationIssue` objects. An error
blocks processing; a warning is reported and processing continues. The
severities follow ``docs/contract.md`` §8.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum


class Severity(str, Enum):
    """How serious an issue is."""

    ERROR = "error"
    """Processing is blocked until the issue is resolved."""

    WARNING = "warning"
    """Processing continues; the affected items are reported."""


@dataclass(frozen=True)
class ValidationIssue:
    """One problem found while checking an experiment.

    Attributes
    ----------
    severity
        :attr:`Severity.ERROR` blocks processing; :attr:`Severity.WARNING` does not.
    code
        Stable machine-readable identifier, e.g. ``"reserved_folder_name"``.
    message
        What happened, in plain language.
    paths
        Affected paths relative to the experiment root, with ``/`` separators.
        ``"."`` stands for the experiment root itself.
    action
        What the user can do about it.
    """

    severity: Severity
    code: str
    message: str
    paths: tuple[str, ...] = ()
    action: str = ""

    @property
    def is_error(self) -> bool:
        """True if this issue blocks processing."""
        return self.severity is Severity.ERROR

    def __str__(self) -> str:
        lines = [f"{self.severity.value.capitalize()}: {self.message}"]
        lines += [f"  - {path}" for path in self.paths]
        if self.action:
            lines.append(f"  Suggested action: {self.action}")
        return "\n".join(lines)


def error(code: str, message: str, paths: Iterable[str] = (), action: str = "") -> ValidationIssue:
    """Build an error issue; ``paths`` are stored sorted."""
    return ValidationIssue(Severity.ERROR, code, message, tuple(sorted(paths)), action)


def warning(
    code: str, message: str, paths: Iterable[str] = (), action: str = ""
) -> ValidationIssue:
    """Build a warning issue; ``paths`` are stored sorted."""
    return ValidationIssue(Severity.WARNING, code, message, tuple(sorted(paths)), action)


def has_errors(issues: Iterable[ValidationIssue]) -> bool:
    """Return True if any issue blocks processing."""
    return any(issue.is_error for issue in issues)


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """Return e.g. ``"1 file"`` or ``"3 files"`` for plain-language messages."""
    word = singular if count == 1 else (plural_form or f"{singular}s")
    return f"{count:,} {word}"
