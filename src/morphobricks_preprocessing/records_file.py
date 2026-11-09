"""The bytes and values of ``morphobricks.records.jsonl`` (experiment-v1 section 4.6, RF1-RF5).

A pure checker: the bytes of a records file in, every refusal out, nothing read from disk. A
refused file is ``R_RECORDS_FILE_INVALID`` (section 8); the ``E_RECORDS_*`` names below are its
reasons, the Spec's reference validator's. An accepted line equals ``encode_record`` of the
record it holds, which is also what the writer appends, so one function decides both sides.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date

CODE = "R_RECORDS_FILE_INVALID"
"""The catalogue code of a refused records file (section 8, owner decision RF3)."""
ENCODING = "E_RECORDS_ENCODING"
NO_FINAL_LF = "E_RECORDS_NO_FINAL_LF"
NOT_JSON_OBJECT = "E_RECORDS_NOT_JSON_OBJECT"
UNKNOWN_TYPE = "E_RECORDS_UNKNOWN_TYPE"
KEY_SET = "E_RECORDS_KEY_SET"
KEY_ORDER = "E_RECORDS_KEY_ORDER"
NON_ASCII_ESCAPED = "E_RECORDS_NON_ASCII_ESCAPED"
VALUE = "E_RECORDS_VALUE"
FORM = "E_RECORDS_FORM"

_BOM = b"\xef\xbb\xbf"
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_SHA256 = re.compile(r"[0-9a-f]{64}")  # fullmatch: a `$` would accept a final LF
_ESCAPE = re.compile(r"\\(?:u([0-9a-fA-F]{4})|.)", re.DOTALL)
ID_KINDS = ("conditions", "channels", "repeats", "technical_repeats")
ENTITIES = ("unit", "technical_repeat", "repeat", "condition")
_REMOVED_NAMES = (
    "condition_id",
    "condition_source_folder",
    "repeat_id",
    "source_repeat",
    "technical_repeat_id",
    "technical_repeat_source_folder",
)
RECORD_KEYS: dict[str, frozenset[str]] = {
    "reconfirmation": frozenset(
        {"type", "id_kind", "old_id", "new_id", "confirmed_on", "old_sha256", "new_sha256"}
    ),
    "image_count_changed": frozenset(
        {
            "type",
            "condition_id",
            "condition_source_folder",
            "repeat_id",
            "source_repeat",
            "technical_repeats",
            "old_image_count",
            "new_image_count",
            "recorded_on",
        }
    ),
    "removed": frozenset({"type", "entity", "confirmed_on", *_REMOVED_NAMES}),
}
"""The key set of each record type (Spec experiment-v1 4.6): a line carries exactly these keys."""
_NAMED = {
    "unit": _REMOVED_NAMES,
    "technical_repeat": ("technical_repeat_id", "technical_repeat_source_folder"),
    "repeat": ("repeat_id",),
    "condition": ("condition_id", "condition_source_folder"),
}
"""The keys a ``removed`` line names per entity; every other name key is null."""


@dataclass(frozen=True)
class RecordsIssue:
    """One refusal: the ``E_RECORDS_*`` reason, the 1-based line and a short detail."""

    reason: str
    line: int
    detail: str = ""


class _Duplicate(ValueError):
    """A key written twice in one object."""


def encode_record(record: dict) -> bytes:
    """Return the canonical bytes of one record: code point key order, unescaped non-ASCII.

    The separators are ``, `` and ``: `` and the line ends in one LF (section 4.6, RF1); this
    is what ``_append_records`` writes.
    """
    return (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _pairs(items: list[tuple[str, object]]) -> dict:
    out: dict = {}
    for key, value in items:
        if key in out:
            raise _Duplicate(key)
        out[key] = value
    return out


def _constant(name: str) -> object:
    raise ValueError(f"{name} is not JSON")


def _sorted_keys(value: object) -> bool:
    """Return whether every object nested in ``value`` lists its keys in code point order."""
    if isinstance(value, dict):
        keys = list(value)
        return keys == sorted(keys) and all(_sorted_keys(v) for v in value.values())
    if isinstance(value, list):
        return all(_sorted_keys(v) for v in value)
    return True


def _escaped_non_ascii(text: str) -> bool:
    return any(m.group(1) and int(m.group(1), 16) >= 0x80 for m in _ESCAPE.finditer(text))


def _text(value: object) -> bool:
    return type(value) is str and not any(0xD800 <= ord(c) <= 0xDFFF for c in value)


def _date(value: object) -> bool:
    if type(value) is not str or not _DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _sha(value: object) -> bool:
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _count(value: object) -> bool:
    return type(value) is int and value >= 1


def _bad_reconfirmation(rec: dict) -> list[str]:
    checks = {
        "id_kind": rec["id_kind"] in ID_KINDS,
        "old_id": _text(rec["old_id"]),
        "new_id": rec["new_id"] is None or _text(rec["new_id"]),
        "confirmed_on": _date(rec["confirmed_on"]),
        "old_sha256": rec["old_sha256"] == "absent" or _sha(rec["old_sha256"]),
        "new_sha256": _sha(rec["new_sha256"]),
    }
    return [name for name, ok in checks.items() if not ok]


def _bad_removed(rec: dict) -> list[str]:
    if rec["entity"] not in ENTITIES:
        return ["entity"]
    named = _NAMED[rec["entity"]]
    bad = [
        key
        for key in _REMOVED_NAMES
        if (not _text(rec[key]) if key in named else rec[key] is not None)
    ]
    return bad + ([] if _date(rec["confirmed_on"]) else ["confirmed_on"])


def _bad_units(units: object) -> bool:
    """Return whether ``units`` is not a non-empty array of ``{id, source_folder}`` sorted by id."""
    if type(units) is not list or not units:
        return True
    for unit in units:
        if type(unit) is not dict or set(unit) != {"id", "source_folder"}:
            return True
        if not (_text(unit["id"]) and _text(unit["source_folder"])):
            return True
    ids = [unit["id"] for unit in units]
    return ids != sorted(ids)


def _bad_image_count(rec: dict) -> list[str]:
    names = ("condition_id", "condition_source_folder", "repeat_id", "source_repeat")
    bad = [key for key in names if not _text(rec[key])]
    bad += ["technical_repeats"] if _bad_units(rec["technical_repeats"]) else []
    bad += [k for k in ("old_image_count", "new_image_count") if not _count(rec[k])]
    if not bad and rec["old_image_count"] == rec["new_image_count"]:
        bad.append("new_image_count")  # the record states a change (RF5)
    return bad + ([] if _date(rec["recorded_on"]) else ["recorded_on"])


_VALUE_CHECKS = {
    "reconfirmation": _bad_reconfirmation,
    "removed": _bad_removed,
    "image_count_changed": _bad_image_count,
}


def _parse(text: str, number: int) -> tuple[dict | None, RecordsIssue | None]:
    try:
        rec = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    except _Duplicate as error:
        return None, RecordsIssue(NOT_JSON_OBJECT, number, f"key {error} twice")
    except ValueError as error:
        return None, RecordsIssue(NOT_JSON_OBJECT, number, str(error))
    if type(rec) is not dict:
        return None, RecordsIssue(NOT_JSON_OBJECT, number, type(rec).__name__)
    return rec, None


def _line_issues(raw: bytes, number: int) -> tuple[dict | None, list[RecordsIssue]]:
    """Return the record of one line (None when it is refused) and every refusal of it."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, [RecordsIssue(ENCODING, number, "not UTF-8")]
    rec, issue = _parse(text, number)
    if rec is None:
        return None, [issue]
    kind = rec.get("type")
    if type(kind) is not str or kind not in RECORD_KEYS:
        return None, [RecordsIssue(UNKNOWN_TYPE, number, repr(kind))]
    if set(rec) != RECORD_KEYS[kind]:
        odd = sorted(set(rec) ^ RECORD_KEYS[kind])
        return None, [RecordsIssue(KEY_SET, number, f"{kind}: differs in {odd}")]
    issues = []
    bad = _VALUE_CHECKS[kind](rec)
    if bad:
        issues.append(RecordsIssue(VALUE, number, f"{kind}: {bad}"))
    if not _sorted_keys(rec):
        issues.append(RecordsIssue(KEY_ORDER, number))
    if _escaped_non_ascii(text):
        issues.append(RecordsIssue(NON_ASCII_ESCAPED, number))
    if issues:
        return None, issues
    if raw + b"\n" != encode_record(rec):
        form = "not the canonical bytes (whitespace, CR, optional escape)"
        return None, [RecordsIssue(FORM, number, form)]
    return rec, []


def check_records(data: bytes) -> tuple[tuple[dict, ...], tuple[RecordsIssue, ...]]:
    """Return the records of a records file's bytes and every refusal (none when accepted).

    Every line is checked, so one file may give several reasons. A file of zero bytes holds no
    record and is accepted (RF4).

    Parameters
    ----------
    data : bytes
        The exact bytes of ``morphobricks.records.jsonl``.

    Returns
    -------
    tuple
        The accepted lines' records in order, and the refusals; the file is accepted exactly
        when the second is empty.
    """
    if not data:
        return (), ()
    issues: list[RecordsIssue] = []
    bom = data.startswith(_BOM)
    if bom:
        issues.append(RecordsIssue(ENCODING, 1, "byte-order mark"))
    parts = data.split(b"\n")  # LF only: U+2028, U+0085 and CR do not end a line
    if parts[-1] != b"":
        issues.append(RecordsIssue(NO_FINAL_LF, len(parts), "last line lacks its LF"))
    else:
        parts = parts[:-1]
    records = []
    for number, raw in enumerate(parts, start=1):
        if number == 1 and bom:
            continue
        record, found = _line_issues(raw, number)
        issues += found
        if record is not None:
            records.append(record)
    return tuple(records), tuple(issues)
