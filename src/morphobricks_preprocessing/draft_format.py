"""The bytes of ``morphobricks.draft.json``: written canonically, read strictly (contract 18).

A draft file is one JSON object: ``format``, ``version``, ``base_sha256``, ``spec_commit`` and
six tables of the user's answers (``conditions``, ``technical_repeats``, ``repeats``,
``units``, ``channels``, ``masks``), every key required. Reading never repairs: bytes that are
not UTF-8 JSON, a duplicate key, ``NaN``, an unknown ``version``, a key the format does not
have, a missing key, a value of the wrong type or a date that is not a real ``YYYY-MM-DD`` day
are refused with :class:`DraftFileError`, whose ``code`` says which. Writing is deterministic
(sorted keys, two-space indent, LF, a final newline), so saving the same answers twice gives the
same bytes.

The file is not a description and is never read as one: it is not named ``morphobricks.toml``
and holds no ``schema`` key (experiment-v1 section 12).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, NoReturn

from .draft_answers import (
    ChannelAnswer,
    ConditionAnswer,
    DraftAnswers,
    MaskAnswer,
    RepeatAnswer,
    UnitAnswer,
    valid_day,
)
from .run.description_file import ABSENT

DRAFT_FORMAT = "morphobricks-description-draft"
DRAFT_VERSION = 1
SPEC_COMMIT = "c8f68526c08633526395ca0180fd6bd1a182fc84"
"""The MorphoBricks-Spec commit the rules of this brick are pinned to (``tests/spec_fixtures``)."""
BASE_MISSING = "R_DRAFT_BASE_MISSING"
SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")

TOP = (
    "format",
    "version",
    "base_sha256",
    "spec_commit",
    "conditions",
    "technical_repeats",
    "repeats",
    "units",
    "channels",
    "masks",
)
CONDITION_KEYS = ("id", "display_name", "reference", "target", "treatment_key")
REPEAT_KEYS = ("id", "confirmed_on", "display_name")
UNIT_KEYS = ("channels", "image_type", "display_name")
CHANNEL_KEYS = ("display_name", "header_names")
MASK_KEYS = ("source_type", "representation", "source_channels", "display_name")


class DraftFileError(ValueError):
    """Raised when draft bytes (or the draft file) are refused; ``code`` says why.

    ``code`` is ``malformed``, ``unknown_version``, ``unknown_key`` (a key the format does not
    have), ``R_DRAFT_BASE_MISSING`` (no usable ``base_sha256``), ``R_FILE_SYMLINK``,
    ``no_draft`` or ``spec_commit_mismatch`` (raised by :mod:`.run.draft_file`).
    """

    def __init__(self, message: str, code: str = "malformed") -> None:
        self.code = code
        super().__init__(message)


def _bad(where: str, what: str) -> DraftFileError:
    return DraftFileError(f"{where}: {what}")


def _table(
    value: object, where: str, allowed: tuple[str, ...], required: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Return ``value`` as a dict whose keys are all in ``allowed`` and include ``required``."""
    if not isinstance(value, dict):
        raise _bad(where, "an object is expected")
    odd = sorted(set(value) - set(allowed))
    if odd:
        raise DraftFileError(f"{where}: unknown key(s) {odd}", "unknown_key")
    lacking = [key for key in required if key not in value]
    if lacking:
        raise _bad(where, f"missing key(s) {lacking}")
    return value


def _text(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise _bad(where, "text is expected")
    return value


def _optional(table: dict, key: str, where: str) -> str | None:
    return _text(table[key], f"{where}.{key}") if key in table else None


def _texts(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _bad(where, "a list of text is expected")
    return tuple(_text(item, where) for item in value)


def _optional_texts(table: dict, key: str, where: str) -> tuple[str, ...] | None:
    return _texts(table[key], f"{where}.{key}") if key in table else None


def _entries(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _bad(where, "an object is expected")
    return value


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise _bad("the file", f"duplicate key {sorted(k for k in keys if keys.count(k) > 1)[0]!r}")
    return dict(pairs)


def _constant(name: str) -> NoReturn:
    raise _bad("the file", f"{name} is not JSON")


def _units(value: object) -> dict[tuple[str, str, str], UnitAnswer]:
    """Read the three nested levels of ``units`` into answers keyed by the unit's folders."""
    out = {}
    for condition, repeats in _entries(value, "units").items():
        for repeat, technical in _entries(repeats, f"units.{condition}").items():
            for folder, raw in _entries(technical, f"units.{condition}.{repeat}").items():
                where = f"units.{condition}.{repeat}.{folder}"
                item = _table(raw, where, UNIT_KEYS, ("channels",))
                out[(condition, repeat, folder)] = UnitAnswer(
                    _texts(item["channels"], f"{where}.channels"),
                    _optional(item, "image_type", where),
                    _optional(item, "display_name", where),
                )
    return out


def _repeat(raw: object, where: str) -> RepeatAnswer:
    item = _table(raw, where, REPEAT_KEYS)
    day = _optional(item, "confirmed_on", where)
    if day is not None and not valid_day(day):
        raise _bad(f"{where}.confirmed_on", f"a real day written YYYY-MM-DD, not {day!r}")
    return RepeatAnswer(_optional(item, "id", where), day, _optional(item, "display_name", where))


def _condition(raw: object, where: str) -> ConditionAnswer:
    item = _table(raw, where, CONDITION_KEYS)
    return ConditionAnswer(*(_optional(item, key, where) for key in CONDITION_KEYS))


def _channel(raw: object, where: str) -> ChannelAnswer:
    item = _table(raw, where, CHANNEL_KEYS)
    return ChannelAnswer(
        _optional(item, "display_name", where), _optional_texts(item, "header_names", where)
    )


def _mask(raw: object, where: str) -> MaskAnswer:
    item = _table(raw, where, MASK_KEYS, ("source_type", "representation"))
    return MaskAnswer(
        _text(item["source_type"], f"{where}.source_type"),
        _text(item["representation"], f"{where}.representation"),
        _optional_texts(item, "source_channels", where),
        _optional(item, "display_name", where),
    )


def _base(doc: dict) -> str:
    base = doc.get("base_sha256")
    if not isinstance(base, str) or not (base == ABSENT or SHA256.fullmatch(base)):
        raise DraftFileError(
            f"base_sha256 is the hex SHA-256 of the published file or {ABSENT!r}, not {base!r}",
            BASE_MISSING,
        )
    return base


def _header(doc: object) -> dict:
    """Check the object, its ``format`` and ``version`` before any other key is read."""
    if not isinstance(doc, dict):
        raise _bad("the file", "a JSON object is expected")
    if doc.get("format") != DRAFT_FORMAT:
        raise _bad("format", f"{DRAFT_FORMAT!r} is expected, not {doc.get('format')!r}")
    version = doc.get("version")
    if type(version) is not int or version != DRAFT_VERSION:
        raise DraftFileError(
            f"version {version!r} is not supported: this brick reads version {DRAFT_VERSION}",
            "unknown_version",
        )
    _table(doc, "the file", TOP)
    if "base_sha256" not in doc:
        raise DraftFileError("base_sha256 is missing", BASE_MISSING)
    return doc


def answers_from_bytes(data: bytes) -> DraftAnswers:
    """Read the bytes of a draft file strictly and return its answers; never repairs.

    Raises
    ------
    DraftFileError
        With ``code`` ``malformed``, ``unknown_version``, ``unknown_key`` or
        ``R_DRAFT_BASE_MISSING``.
    """
    try:
        raw = json.loads(
            data.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_constant
        )
    except DraftFileError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise DraftFileError(f"the file is not UTF-8 JSON ({error})") from error
    doc = _header(raw)
    _table(doc, "the file", TOP, TOP)
    base = _base(doc)
    commit = _text(doc["spec_commit"], "spec_commit")
    if not COMMIT.fullmatch(commit):
        raise _bad("spec_commit", f"a 40-digit lower-case hex commit is expected, not {commit!r}")
    return DraftAnswers(
        base,
        commit,
        {
            k: _condition(v, f"conditions.{k}")
            for k, v in _entries(doc["conditions"], "conditions").items()
        },
        {
            k: _text(v, f"technical_repeats.{k}")
            for k, v in _entries(doc["technical_repeats"], "technical_repeats").items()
        },
        {k: _repeat(v, f"repeats.{k}") for k, v in _entries(doc["repeats"], "repeats").items()},
        _units(doc["units"]),
        {k: _channel(v, f"channels.{k}") for k, v in _entries(doc["channels"], "channels").items()},
        {k: _mask(v, f"masks.{k}") for k, v in _entries(doc["masks"], "masks").items()},
    )


def _given(pairs: Mapping[str, object]) -> dict[str, object]:
    """Return the entries that were answered (a ``None`` is no answer and is not written)."""
    return {key: value for key, value in pairs.items() if value is not None}


def _lists(pairs: Mapping[str, object]) -> dict[str, object]:
    return {k: list(v) if isinstance(v, tuple) else v for k, v in _given(pairs).items()}


def answers_to_bytes(answers: DraftAnswers) -> bytes:
    """Return the canonical bytes of the answers: sorted keys, two-space indent, LF, final LF.

    Raises
    ------
    DraftFileError
        When the answers cannot be written as UTF-8 JSON (for example a lone surrogate).
    """
    units: dict[str, dict[str, dict[str, object]]] = {}
    for (condition, repeat, folder), unit in sorted(answers.units.items()):
        units.setdefault(condition, {}).setdefault(repeat, {})[folder] = _lists(
            {
                "channels": unit.channels,
                "image_type": unit.image_type,
                "display_name": unit.display_name,
            }
        )
    doc = {
        "format": DRAFT_FORMAT,
        "version": DRAFT_VERSION,
        "base_sha256": answers.base_sha256,
        "spec_commit": answers.spec_commit,
        "conditions": {
            k: _given({f: getattr(v, f) for f in CONDITION_KEYS})
            for k, v in sorted(answers.conditions.items())
        },
        "technical_repeats": dict(sorted(answers.technical_repeats.items())),
        "repeats": {
            k: _given({f: getattr(v, f) for f in REPEAT_KEYS})
            for k, v in sorted(answers.repeats.items())
        },
        "units": units,
        "channels": {
            k: _lists({f: getattr(v, f) for f in CHANNEL_KEYS})
            for k, v in sorted(answers.channels.items())
        },
        "masks": {
            k: _lists({f: getattr(v, f) for f in MASK_KEYS})
            for k, v in sorted(answers.masks.items())
        },
    }
    try:
        return (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    except UnicodeEncodeError as error:
        raise DraftFileError(f"the answers cannot be written as UTF-8 ({error})") from error


__all__ = [
    "ABSENT",
    "BASE_MISSING",
    "DRAFT_FORMAT",
    "DRAFT_VERSION",
    "SPEC_COMMIT",
    "DraftFileError",
    "answers_from_bytes",
    "answers_to_bytes",
]
