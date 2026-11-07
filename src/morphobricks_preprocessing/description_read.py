"""The reference reader of ``morphobricks.toml``: bytes in, phases and issues out.

Experiment-v1 sections 2 and 6. The reader is given raw bytes (never decoded text, so a byte
order mark or invalid UTF-8 reaches the encoding check) and runs the validation phases in
order, stopping after the first phase that reports an error. Nothing is converted, trimmed,
normalized or defaulted, and the only rules are those of ``description_rules`` and the phase
modules. This module holds phases 1 (decode) and 2 (header) and the driver.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from collections.abc import Callable, Mapping

from .description_report import DescriptionReport, Issues
from .description_rules import SCHEMA_ID, SUPPORTED_VERSIONS, Code
from .description_structure import check_structure

BYTE_ORDER_MARK = b"\xef\xbb\xbf"
DUPLICATE = re.compile(
    r"Cannot (?:declare .* twice|overwrite a value|mutate immutable namespace|redefine namespace)"
    r"|Duplicate inline table key"
)
"""The ``tomllib`` messages for a key or table declared twice, in any spelling TOML treats as
the same key (bare or quoted, table, dotted or inline). The Python 3.11 and 3.13 parsers share
them; ``tests/test_spec_reader_phases_1_3.py`` covers each form."""


def decode(data: bytes) -> tuple[dict[str, object] | None, Issues]:
    """Phase 1: the bytes must be UTF-8 without a byte-order mark and valid TOML 1.0.

    Returns
    -------
    tuple
        The parsed document (None when phase 1 failed) and the phase's issues:
        ``E_FILE_ENCODING``, ``E_DUPLICATE_KEY`` or ``E_TOML_SYNTAX``.
    """
    out = Issues()
    if data.startswith(BYTE_ORDER_MARK):
        out.add(Code.E_FILE_ENCODING, "<file>", "byte-order mark")
        return None, out
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        out.add(Code.E_FILE_ENCODING, "<file>", str(error))
        return None, out
    try:
        return tomllib.loads(text), out
    except tomllib.TOMLDecodeError as error:
        message = str(error)
        out.add(
            Code.E_DUPLICATE_KEY if DUPLICATE.match(message) else Code.E_TOML_SYNTAX,
            "<file>",
            message,
        )
    except RecursionError:
        out.add(Code.E_TOML_SYNTAX, "<file>", "nested too deeply to read")
    return None, out


def check_header(doc: Mapping[str, object]) -> Issues:
    """Phase 2: ``schema`` and ``schema_version``, exact types, the supported version only.

    A missing, unsupported or mistyped version stops the validation: no field of a version the
    reader does not implement is interpreted (experiment-v1 section 6).
    """
    out = Issues()
    if "schema" not in doc:
        out.add(Code.E_SCHEMA_ID, "schema")
    elif type(doc["schema"]) is not str:
        out.add(Code.E_TYPE, "schema", doc["schema"])
    elif doc["schema"] != SCHEMA_ID:
        out.add(Code.E_SCHEMA_ID, "schema", doc["schema"])
    if "schema_version" not in doc:
        out.add(Code.E_SCHEMA_VERSION_MISSING, "schema_version")
    elif type(doc["schema_version"]) is not int:
        out.add(Code.E_TYPE, "schema_version", doc["schema_version"])
    elif doc["schema_version"] not in SUPPORTED_VERSIONS:
        out.add(Code.E_SCHEMA_VERSION_UNSUPPORTED, "schema_version", doc["schema_version"])
    return out


PHASES: tuple[tuple[int, Callable[[Mapping[str, object]], Issues]], ...] = (
    (2, check_header),
    (3, check_structure),
)
"""The document phases after decoding, in order, as (number, check)."""


def read_structure(data: bytes) -> DescriptionReport:
    """Run phases 1 to 3 on the raw bytes of a file and report the first failing phase.

    This is the structural reading only: a file it accepts is not yet a validated
    description (identities and semantics are phases 4 and 5).
    """
    if not isinstance(data, bytes):
        raise TypeError("the reader is given the raw bytes of the file, not decoded text")
    digest = hashlib.sha256(data).hexdigest()
    doc, issues = decode(data)
    if doc is None:
        return DescriptionReport(digest, 1, tuple(issues.items), (), None)
    for phase, check in PHASES:
        issues = check(doc)
        if issues:
            return DescriptionReport(digest, phase, tuple(issues.items), (), doc)
    return DescriptionReport(digest, PHASES[-1][0], (), (), doc)
