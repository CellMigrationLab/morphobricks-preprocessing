"""Lab-tool links: provenance recorded only in the run manifest (Spec experiment-v1 §9.3).

A lab tool such as LabChronicle keeps the experiment in a repository of its own. The user may
give the run a small JSON file (``run --lab-links FILE``, ``run_experiment(lab_links=...)``)
with exactly these keys (owner decision of 9 November 2026, Stage 2 plan PP-2):

- ``repository_url``, ``commit`` and ``experiment_path``: required, as text;
- ``repeat_codes``: optional, a mapping from each lab-tool repeat code (such as ``R001``) to a
  canonical biological repeat ID of the published description.

The links are recorded verbatim in the run manifest under :data:`KEY` and nowhere else: never
in ``morphobricks.toml`` (``labchronicle``, ``provenance`` and ``source_database`` are
``E_FORBIDDEN_FIELD`` there), never in a section digest, row key, cache key or any other output,
and they are not an input taken over by the next run. A mapping of repeat codes is used only
when the user confirmed it (:attr:`LabLinks.repeat_codes_confirmed`): equal strings are not
evidence, because the namespaces are unrelated. It must name every described repeat once and no
other (:func:`repeat_code_issues`). Nothing is trimmed, guessed or filled in: a value that does
not fit is refused with :class:`LabLinksError`. Pure: no I/O.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from .validation import ValidationIssue, agree, error, plural

if TYPE_CHECKING:
    from .description import Description

KEY = "lab_links"
"""The run manifest's key; left out of a manifest whose run was given no links."""
REQUIRED_KEYS = ("repository_url", "commit", "experiment_path")
REPEAT_CODES = "repeat_codes"
KEYS = (*REQUIRED_KEYS, REPEAT_CODES)
"""Every key the file and the manifest entry may have, in the order the manifest writes them."""
INVALID = "lab_links_invalid"
"""The command line's refusal of a links file that cannot be read or is not the fixed keys."""
UNCONFIRMED = "lab_links_repeat_codes_not_confirmed"
UNKNOWN_REPEAT = "lab_links_repeat_unknown"
MISSING_REPEAT = "lab_links_repeat_missing"
REPEAT_TWICE = "lab_links_repeat_twice"
_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_CONFIRM = "check each pair and give --confirm-repeat-codes (repeat_codes_confirmed=True)"


class LabLinksError(ValueError):
    """Lab-tool links that are not the fixed keys with values of the right type."""


@dataclass(frozen=True)
class LabLinks:
    """The lab-tool links the user gave a run, checked; recorded only in the run manifest.

    Attributes
    ----------
    repository_url
        The lab tool's repository, as given (any non-empty text: a URL or an SSH remote).
    commit
        The repository's commit: a full lowercase SHA-1 (40) or SHA-256 (64) in hexadecimal.
    experiment_path
        The experiment's path in that repository, as given.
    repeat_codes
        Each lab-tool repeat code and the canonical repeat ID it names; None when not given.
    repeat_codes_confirmed
        True only when the user confirmed the mapping for this run (never read from the
        file). An unconfirmed mapping is refused by the run (:func:`repeat_code_issues`).

    Raises
    ------
    LabLinksError
        If a value is not of its type, is empty, or has outer whitespace or control
        characters; if the commit is not a full lowercase SHA; if ``repeat_codes`` is empty
        or its codes or IDs are not such text.
    """

    repository_url: str
    commit: str
    experiment_path: str
    repeat_codes: Mapping[str, str] | None = None
    repeat_codes_confirmed: bool = False

    def __post_init__(self) -> None:
        for key in REQUIRED_KEYS:
            _check_text(getattr(self, key), key)
        if not _COMMIT.fullmatch(self.commit):
            raise LabLinksError(
                f"commit {self.commit!r} is not a full commit SHA: give the 40 (or 64) "
                "lowercase hexadecimal characters of the lab tool's commit, never a short one"
            )
        if not isinstance(self.repeat_codes_confirmed, bool):
            raise LabLinksError("repeat_codes_confirmed must be True or False")
        if self.repeat_codes is not None:
            codes = _checked_codes(self.repeat_codes)
            object.__setattr__(self, "repeat_codes", MappingProxyType(codes))

    def __reduce__(self) -> tuple:
        # A mapping proxy does not pickle: rebuild from a plain copy (checked again).
        codes = None if self.repeat_codes is None else dict(self.repeat_codes)
        fields = (self.repository_url, self.commit, self.experiment_path, codes)
        return (LabLinks, (*fields, self.repeat_codes_confirmed))

    @classmethod
    def from_data(cls, data: object, *, confirmed: bool = False) -> LabLinks:
        """Build the links from the file's JSON data, refusing any key not of :data:`KEYS`.

        Parameters
        ----------
        data
            The decoded JSON value: an object with the three required keys and optionally
            ``repeat_codes``.
        confirmed
            Whether the user confirmed the repeat codes for this run.

        Raises
        ------
        LabLinksError
            If ``data`` is not such an object, or a value is refused (:class:`LabLinks`).
        """
        if not isinstance(data, dict):
            raise LabLinksError("the lab-tool links must be one JSON object")
        unknown = sorted(set(data) - set(KEYS))
        if unknown:
            raise LabLinksError(
                f"unknown {agree(unknown, 'key', 'keys')} {', '.join(map(repr, unknown))}: "
                f"the lab-tool links have only {', '.join(KEYS)}"
            )
        missing = [key for key in REQUIRED_KEYS if key not in data]
        if missing:
            raise LabLinksError(
                f"{agree(missing, 'the key', 'the keys')} {', '.join(missing)} "
                f"{agree(missing, 'is', 'are')} missing: give each, as text"
            )
        if REPEAT_CODES in data and data[REPEAT_CODES] is None:
            raise LabLinksError("repeat_codes is null: leave the key out when there are none")
        return cls(*(data[key] for key in REQUIRED_KEYS), data.get(REPEAT_CODES), confirmed)

    def to_dict(self) -> dict:
        """Return the manifest's entry: the keys given, verbatim, repeat codes sorted by code."""
        data = {key: getattr(self, key) for key in REQUIRED_KEYS}
        if self.repeat_codes is not None:
            data[REPEAT_CODES] = dict(sorted(self.repeat_codes.items()))
        return data


def parse_lab_links(data: bytes, *, confirmed: bool = False) -> LabLinks:
    """Read lab-tool links from the bytes of a UTF-8 JSON file.

    A key given twice in one object is refused, never resolved to one of its values.

    Raises
    ------
    LabLinksError
        If the bytes are not UTF-8 JSON, or :meth:`LabLinks.from_data` refuses them.
    """
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_keys)
    except UnicodeDecodeError as exc:
        raise LabLinksError(f"the file is not UTF-8 text: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LabLinksError(f"the file is not valid JSON: {exc}") from exc
    return LabLinks.from_data(value, confirmed=confirmed)


def repeat_code_issues(
    links: LabLinks | None, description: Description | None
) -> list[ValidationIssue]:
    """Check the repeat codes against the published description's canonical repeat IDs.

    The mapping must be confirmed by the user, name only described repeats, and name each of
    them exactly once (IDs compared exactly: a display name or a source folder is not an ID).

    Parameters
    ----------
    links
        The run's links, or None.
    description
        The published description, whose ``repeats`` are the canonical biological repeat
        IDs; None when the run has none it can use (it is refused for that already).

    Returns
    -------
    list of ValidationIssue
        Every refusal, together; empty without links, repeat codes or a description.
    """
    if links is None or links.repeat_codes is None or description is None:
        return []
    repeat_ids = frozenset(description.repeats)
    codes = links.repeat_codes
    pairs = [f"{code} -> {repeat}" for code, repeat in sorted(codes.items())]
    action = f"Correct the repeat codes in the lab-tool links file and {_CONFIRM}."
    issues = []
    if not links.repeat_codes_confirmed:
        issues.append(
            error(
                UNCONFIRMED,
                f"The lab-tool links map {plural(len(codes), 'repeat code')} to biological "
                "repeats, but the mapping is not confirmed: equal names are not evidence, "
                "because the lab tool's codes and the description's IDs are separate.",
                pairs,
                f"If every pair is right, {_CONFIRM}.",
            )
        )
    unknown = sorted({repeat for repeat in codes.values() if repeat not in repeat_ids})
    if unknown:
        text = agree(unknown, "a biological repeat", "biological repeats")
        issues.append(
            error(
                UNKNOWN_REPEAT,
                f"The lab-tool repeat codes name {text} the experiment description does not "
                "declare (canonical repeat IDs are compared exactly).",
                unknown,
                action,
            )
        )
    named = list(codes.values())
    twice = sorted({repeat for repeat in named if named.count(repeat) > 1})
    if twice:
        issues.append(
            error(
                REPEAT_TWICE,
                f"The lab-tool repeat codes give {agree(twice, 'a repeat', 'repeats')} more than "
                "one code: each biological repeat has exactly one.",
                [f"{code} -> {codes[code]}" for code in sorted(codes) if codes[code] in twice],
                action,
            )
        )
    missing = sorted(set(repeat_ids) - set(named))
    if missing:
        issues.append(
            error(
                MISSING_REPEAT,
                f"The lab-tool repeat codes give no code for "
                f"{plural(len(missing), 'described biological repeat')}: a mapping names "
                "every repeat of the description.",
                missing,
                action,
            )
        )
    return issues


def recorded_lab_links(data: object) -> dict | None:
    """Check a manifest's ``lab_links`` entry as :class:`LabLinks` does; None when absent.

    The entry was confirmed when the run wrote it; reading it confirms nothing new.

    Raises
    ------
    LabLinksError
        If the entry is not one :meth:`LabLinks.to_dict` writes.
    """
    return None if data is None else LabLinks.from_data(data).to_dict()


def _check_text(value: object, key: str) -> None:
    """Refuse a value that is not non-empty text kept exactly as typed."""
    if not isinstance(value, str):
        raise LabLinksError(f"{key} must be text, found {type(value).__name__} {value!r}")
    if not value:
        raise LabLinksError(f"{key} is empty: give its value, or leave the file out")
    if value != value.strip() or any(unicodedata.category(c) == "Cc" for c in value):
        raise LabLinksError(
            f"{key} {value!r} has spaces around it or control characters: nothing is trimmed, "
            "so correct it in the file"
        )


def _checked_codes(codes: object) -> dict[str, str]:
    """Return the repeat codes as a plain dict, refusing anything but text to text."""
    if not isinstance(codes, Mapping):
        raise LabLinksError(
            f"repeat_codes must map each lab-tool repeat code to a repeat ID, found "
            f"{type(codes).__name__}"
        )
    if not codes:
        raise LabLinksError("repeat_codes is empty: leave the key out when there are none")
    for code, repeat in codes.items():
        _check_text(code, "a repeat code")
        _check_text(repeat, f"the repeat ID of code {code!r}")
    return dict(codes)


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    """Build one JSON object, refusing a key it holds twice."""
    found: dict = {}
    for key, value in pairs:
        if key in found:
            raise LabLinksError(f"the key {key!r} is given twice in one object")
        found[key] = value
    return found
