"""The whole reader on the Spec fixtures (conformance plan section 2): phases 1 to 5, warnings.

Every ``valid/`` file is accepted with exactly the warnings of its ``Expected`` line; every
``invalid/`` file is refused with exactly its code set, all from its phase, and nothing from a
later one; every transition file is valid on its own. The reader is given the raw bytes.
"""

from __future__ import annotations

import pytest

from morphobricks_preprocessing.description_read import read_description_bytes
from morphobricks_preprocessing.description_rules import Code
from spec_pin import catalogue, codes_in, fixtures

VALID = fixtures("valid")
INVALID = fixtures("invalid")
TRANSITIONS = fixtures("transitions")
ABSENT = {Code.E_MISSING_FIELD, Code.E_SCHEMA_ID, Code.E_SCHEMA_VERSION_MISSING}


def _name(item):
    return item.name


def test_the_fixture_counts_are_the_catalogue_s():
    assert (len(VALID), len(INVALID), len(TRANSITIONS)) == (36, 134, 12)


@pytest.mark.parametrize("item", VALID, ids=_name)
def test_a_valid_file_is_accepted_with_exactly_its_warnings(item):
    report = read_description_bytes(item.data)
    assert report.errors == ()
    assert {code.value for code in report.warning_codes} == item.expected_warnings
    assert report.document is not None


@pytest.mark.parametrize("item", INVALID, ids=_name)
def test_an_invalid_file_gives_exactly_its_codes_from_its_phase(item):
    report = read_description_bytes(item.data)
    assert {code.value for code in report.codes} == item.expected_codes
    assert report.phase == item.phase
    # Warnings are only for a file without errors, and nothing comes from a later phase.
    assert report.warnings == ()


@pytest.mark.parametrize("item", INVALID, ids=_name)
def test_each_error_carries_a_table_path_and_the_offending_value(item):
    for issue in read_description_bytes(item.data).errors:
        assert issue.path
        assert issue.value is not None or issue.code in ABSENT


@pytest.mark.parametrize("item", TRANSITIONS, ids=_name)
def test_a_transition_file_is_valid_on_its_own(item):
    report = read_description_bytes(item.data)
    assert report.errors == ()
    listed = {code for code in codes_in(catalogue()[item.name][2]) if code.startswith("W_")}
    assert {code.value for code in report.warning_codes} == listed


def test_the_report_has_the_digest_of_the_exact_bytes():
    import hashlib

    item = VALID[0]
    assert read_description_bytes(item.data).sha256 == hashlib.sha256(item.data).hexdigest()


def test_the_reader_is_given_bytes_never_decoded_text():
    with pytest.raises(TypeError, match="raw bytes"):
        read_description_bytes(VALID[0].data.decode("utf-8"))


NOT_NFC = ["i78_", "i83_", "i84_", "i85_"]


@pytest.mark.parametrize("prefix", NOT_NFC)
def test_a_non_nfc_identity_is_refused_as_written_and_never_normalized(prefix):
    import unicodedata

    item = next(item for item in INVALID if item.name.startswith(prefix))
    report = read_description_bytes(item.data)
    refused = [issue for issue in report.errors if issue.code == Code.E_ID_NOT_NFC]
    assert refused
    for issue in refused:
        # The value reported is the spelling in the file, not its NFC form.
        assert unicodedata.normalize("NFC", issue.value) != issue.value
    assert report.codes == {Code.E_ID_NOT_NFC}


def test_ids_are_kept_exactly_as_written():
    item = next(item for item in VALID if item.name.startswith("v26_"))
    document = read_description_bytes(item.data).document
    assert set(document["conditions"]) >= {"cl001", "CL1000"}
    assert set(document["repeats"]) >= {"R999", "R1000"}


def test_a_display_name_never_stands_for_an_id():
    for prefix, code in (
        ("i89_", Code.E_REFERENCE_UNKNOWN),
        ("i90_", Code.E_UNKNOWN_CONDITION),
        ("i91_", Code.E_UNKNOWN_CHANNEL),
    ):
        item = next(item for item in INVALID if item.name.startswith(prefix))
        assert read_description_bytes(item.data).codes == {code}


def test_one_code_per_broken_rule():
    item = next(item for item in INVALID if item.name.startswith("i118_"))
    assert read_description_bytes(item.data).codes == {Code.E_ID_TEXT, Code.E_ID_SYNTAX}
    for prefix in ("i116_", "i117_"):
        item = next(item for item in INVALID if item.name.startswith(prefix))
        codes = read_description_bytes(item.data).codes
        assert Code.E_ID_NOT_NFC in codes and Code.E_UNKNOWN_CHANNEL not in codes
