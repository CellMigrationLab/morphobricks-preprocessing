"""Every records golden of the vendored Spec through ``read_records`` (experiment-v1 4.6, RF1-RF5).

``fixtures/records/`` holds byte-exact files and ``fixtures/README.md`` lists each one as
``accepted`` or ``refused`` with its ``E_RECORDS_*`` reasons. The reader must accept the first
and refuse the second with ``R_RECORDS_FILE_INVALID`` and exactly the listed reasons, and it
must agree with the vendored reference validator on every file.
"""

from __future__ import annotations

import importlib.util
import re
from functools import cache
from types import ModuleType

import pytest

from morphobricks_preprocessing.run.description_file import (
    RECORDS_FILENAME,
    RecordsFileError,
    read_records,
)
from spec_pin import spec_root

ROW = re.compile(r"\| \[`(r[vi]\d+[^`]*)`\]\(records/[^)]+\) \|([^|]*)\|")
REASON = re.compile(r"\b(E_RECORDS_[A-Z_]+)\b")


def _catalogue() -> dict[str, frozenset[str]]:
    """Return each golden's expected reasons from the README (empty set: accepted)."""
    text = (spec_root() / "fixtures" / "README.md").read_text(encoding="utf-8")
    rows = {m.group(1): m.group(2).strip() for m in map(ROW.match, text.splitlines()) if m}
    return {
        name: frozenset() if cell == "`accepted`" else frozenset(REASON.findall(cell))
        for name, cell in rows.items()
    }


@cache
def _oracle() -> ModuleType:
    path = spec_root() / "tools" / "audit" / "refrecords.py"
    spec = importlib.util.spec_from_file_location("spec_refrecords", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CATALOGUE = _catalogue()
FILES = {f.stem: f for f in sorted((spec_root() / "fixtures" / "records").glob("*.jsonl"))}
REFUSED_BY_TASK = {
    "ri09": "E_RECORDS_KEY_ORDER",
    "ri10": "E_RECORDS_KEY_ORDER",
    "ri11": "E_RECORDS_NON_ASCII_ESCAPED",
    "ri14": "E_RECORDS_NOT_JSON_OBJECT",
    **{f"ri{n}": "E_RECORDS_VALUE" for n in (15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 28, 29, 31)},
    **{f"ri{n}": "E_RECORDS_FORM" for n in (25, 26, 27, 30)},
}


def test_every_golden_has_a_catalogue_row_and_every_row_a_golden():
    assert set(CATALOGUE) == set(FILES)
    assert len(FILES) == 37


@pytest.mark.parametrize("name", sorted(FILES))
def test_read_records_follows_the_catalogue(tmp_path, name):
    (tmp_path / RECORDS_FILENAME).write_bytes(FILES[name].read_bytes())
    expected = CATALOGUE[name]
    if not expected:
        assert name.startswith("rv")
        read_records(tmp_path)
        return
    with pytest.raises(RecordsFileError) as refusal:
        read_records(tmp_path)
    assert refusal.value.code == "R_RECORDS_FILE_INVALID"
    assert refusal.value.reasons == expected


@pytest.mark.parametrize("name", sorted(FILES))
def test_read_records_agrees_with_the_reference_validator(tmp_path, name):
    data = FILES[name].read_bytes()
    (tmp_path / RECORDS_FILENAME).write_bytes(data)
    verdict = _oracle().validate_records(data)
    try:
        records = read_records(tmp_path)
    except RecordsFileError as refusal:
        assert refusal.reasons == verdict.codes
        assert refusal.issues[0].line == verdict.errors[0][1]
    else:
        assert not verdict.errors
        assert records == tuple(verdict.records)


def test_the_goldens_beyond_the_key_set_are_refused_with_their_reason():
    by_prefix = {name[:4]: reasons for name, reasons in CATALOGUE.items()}
    for prefix, reason in REFUSED_BY_TASK.items():
        assert by_prefix[prefix] == {reason}, prefix
    refused = {name[:4] for name, reasons in CATALOGUE.items() if reasons}
    assert {f"ri{n:02d}" for n in range(1, 32)} == refused
