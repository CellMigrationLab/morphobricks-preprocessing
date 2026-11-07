"""Reader phases 1 to 3 on the Spec fixtures (conformance plan section 2).

Every ``invalid/`` fixture of phases 1 to 3 is refused with exactly the codes of its
``Expected`` header, from its phase, each error with a table path and the offending value;
every other fixture passes these phases. The reader is given the raw bytes.
"""

from __future__ import annotations

import socket

import pytest

from morphobricks_preprocessing.description_read import read_structure
from morphobricks_preprocessing.description_rules import Code
from spec_pin import fixtures

INVALID = fixtures("invalid")
EARLY = [item for item in INVALID if item.phase <= 3]
LATER = [item for item in INVALID if item.phase > 3]
PASSING = fixtures("valid") + fixtures("transitions")
ABSENT = {Code.E_MISSING_FIELD, Code.E_SCHEMA_ID, Code.E_SCHEMA_VERSION_MISSING}


def _name(item):
    return item.name


def test_the_first_three_phases_have_their_fixtures():
    assert len(EARLY) == 65
    assert {item.phase for item in EARLY} == {1, 2, 3}


@pytest.mark.parametrize("item", EARLY, ids=_name)
def test_an_invalid_fixture_gives_exactly_its_codes_from_its_phase(item):
    report = read_structure(item.data)
    assert {code.value for code in report.codes} == item.expected_codes
    assert report.phase == item.phase
    assert report.warnings == ()


@pytest.mark.parametrize("item", EARLY, ids=_name)
def test_each_error_carries_a_path_and_the_offending_value(item):
    for issue in read_structure(item.data).errors:
        assert issue.path
        # A value is absent only where the field is: a missing field or header entry.
        assert issue.value is not None or issue.code in ABSENT


@pytest.mark.parametrize("item", LATER + PASSING, ids=_name)
def test_a_file_without_a_phase_1_to_3_fault_passes_these_phases(item):
    report = read_structure(item.data)
    assert report.errors == ()
    assert report.document is not None


def test_a_phase_1_failure_has_no_document_and_later_phases_never_run():
    by_name = {item.name: item for item in INVALID}
    for name in (
        "i01_bom",
        "i02_invalid_utf8",
        "i03_duplicate_condition_table",
        "i07_unquoted_bareword",
    ):
        report = read_structure(by_name[name].data)
        assert report.phase == 1 and report.document is None


def test_the_reader_is_given_bytes_never_decoded_text():
    with pytest.raises(TypeError, match="raw bytes"):
        read_structure("schema = 'morphobricks-experiment'\n")


def test_the_digest_is_that_of_the_exact_bytes():
    import hashlib

    item = fixtures("valid")[0]
    assert read_structure(item.data).sha256 == hashlib.sha256(item.data).hexdigest()


def test_the_version_check_needs_no_network_and_has_no_default(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a socket was opened")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    by_name = {item.name: item for item in INVALID}
    for name, code in (
        ("i09_schema_version_2", Code.E_SCHEMA_VERSION_UNSUPPORTED),
        ("i10_schema_version_0", Code.E_SCHEMA_VERSION_UNSUPPORTED),
        ("i11_schema_version_missing", Code.E_SCHEMA_VERSION_MISSING),
    ):
        report = read_structure(by_name[name].data)
        assert report.codes == {code}
        # Nothing of a version the reader does not implement is interpreted.
        assert report.phase == 2


def test_exact_types_a_boolean_is_not_the_integer_one():
    by_name = {item.name: item for item in INVALID}
    report = read_structure(by_name["i14_schema_version_bool"].data)
    assert report.codes == {Code.E_TYPE}
    assert report.errors[0].value is True


DUPLICATES = {
    "table twice": b"[a]\nx = 1\n[a]\ny = 2\n",
    "bare then quoted": b'[a]\nx = 1\n["a"]\ny = 2\n',
    "field twice": b"x = 1\nx = 2\n",
    "dotted twice": b"a.b = 1\na.b = 2\n",
    "table then dotted": b"[a.b]\nx = 1\n[a]\nb.y = 2\n",
    "key then dotted": b"a = 1\na.b = 2\n",
    "inline then dotted": b"i = {a = 1}\ni.b = 2\n",
    "inline then table": b"i = {a = 1}\n[i.b]\nc = 1\n",
    "inline key twice": b"i = {a = 1, a = 2}\n",
    "static array extended": b"a = [1]\n[[a]]\n",
    "value then table": b"x = 1\n[x]\ny = 1\n",
    "table then array of tables": b"[a]\n[[a]]\n",
    "array of tables then table": b"[[a]]\n[a]\n",
}
SYNTAX = {
    "bareword": b"x = NO\n",
    "leading zero": b"x = 012\n",
    "unclosed string": b'x = "abc\n',
    "unclosed array": b"x = [1, 2\n",
    "missing value": b"x =\n",
    "two on a line": b"x = 1 y = 2\n",
    "bad table": b"[a\n",
    "bad escape": b'x = "\\q"\n',
    "bad date": b"x = 2026-13-45\n",
}
BASE = b'schema = "morphobricks-experiment"\nschema_version = 1\n'


@pytest.mark.parametrize("name", DUPLICATES)
def test_a_key_declared_twice_in_any_spelling_is_a_duplicate_key(name):
    report = read_structure(DUPLICATES[name])
    assert report.codes == {Code.E_DUPLICATE_KEY}
    assert report.phase == 1


@pytest.mark.parametrize("name", SYNTAX)
def test_other_decode_errors_are_toml_syntax(name):
    report = read_structure(SYNTAX[name])
    assert report.codes == {Code.E_TOML_SYNTAX}


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"\xef\xbb\xbf" + BASE, Code.E_FILE_ENCODING),
        (BASE + b'x = "\xff"\n', Code.E_FILE_ENCODING),
        (BASE + b'x = "\xc0\x80"\n', Code.E_FILE_ENCODING),
        (BASE + b'x = "\xed\xa0\x80"\n', Code.E_FILE_ENCODING),
        (b"\xff\xfe" + BASE, Code.E_FILE_ENCODING),
        (BASE.decode().encode("utf-16"), Code.E_FILE_ENCODING),
        (b"[" * 100_000, Code.E_TOML_SYNTAX),
        (b"x = " + b"[" * 100_000 + b"]" * 100_000, Code.E_TOML_SYNTAX),
    ],
    ids=["bom", "invalid byte", "overlong", "surrogate", "utf16 bom", "utf16", "brackets", "deep"],
)
def test_encoding_and_hostile_bytes_are_refused_with_a_code_not_a_traceback(data, code):
    assert read_structure(data).codes == {code}
