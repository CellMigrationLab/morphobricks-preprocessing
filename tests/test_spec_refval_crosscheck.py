"""The reader against the Spec's reference validator, the oracle (conformance plan section 2).

The reference validator (``tools/audit/refval.py``, vendored at the pin, loaded by path) is the
oracle: the reader must give the same phase, the same set of codes (and the same table path
wherever the oracle's is precise) and the same warnings on every fixture, and on every one-step
mutation of the valid fixtures: a field deleted, retyped, renamed, duplicated, or given a value
or key borrowed from the same file. A difference is a bug in the reader or, if the text is
ambiguous, a point to take to the Spec: it is never settled by editing the oracle.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from morphobricks_preprocessing.description_read import PHASES, read_description_bytes
from morphobricks_preprocessing.description_warnings import check_warnings
from spec_oracle import mutations, oracle_document_verdict, oracle_verdict, refval, verdict
from spec_pin import all_fixtures, fixtures

ALL = all_fixtures()
MUTATED = (
    "v01", "v02", "v03", "v06", "v07", "v10", "v11", "v13",
    "v17", "v28", "v29", "v31", "v33", "v34", "v35", "v36", "v38", "v39",
)  # fmt: skip
DOCUMENT_PHASES = tuple(number for number, _ in PHASES)
PHASE_4_AND_5 = {
    "E_ID_TEXT", "E_ID_SYNTAX", "E_ID_RESERVED", "E_ID_NOT_NFC", "E_ID_CASE_COLLISION",
    "E_SINGLE_MISUSE", "E_UNKNOWN_CONDITION", "E_UNKNOWN_CHANNEL",
    "E_DUPLICATE_CHANNEL_IN_UNIT", "E_DUPLICATE_VALUE", "E_RGB_UNIT_CHANNELS",
    "E_RGB_TOKEN_MISUSE", "E_RGB_ALIAS", "E_DISPLAY_NAME_COLLISION", "E_USED_ID_MISSING",
    "E_SOURCE_FOLDER_REUSED", "E_SOURCE_FOLDER_CONFLICT", "E_CONDITION_WITHOUT_MEMBERS",
    "E_SOURCE_REPEAT_REUSED", "E_REPEAT_ID_SHADOWS_SOURCE", "E_MASK_SOURCE_TYPE_REUSED",
    "E_REFERENCE_UNKNOWN", "E_REFERENCE_SELF", "E_REFERENCE_CYCLE",
}  # fmt: skip


def _reader(report) -> tuple[int, frozenset[str], frozenset]:
    codes, pairs = verdict([(issue.code.value, issue.path) for issue in report.errors])
    return report.phase, codes, pairs


@pytest.mark.parametrize("item", ALL, ids=lambda item: f"{item.kind}/{item.name}")
def test_the_reader_and_the_oracle_agree_on_a_fixture(item):
    phase, codes, pairs, warnings = oracle_verdict(item.data)
    report = read_description_bytes(item.data)
    mine_phase, mine_codes, mine_pairs = _reader(report)
    assert mine_codes == codes
    assert mine_pairs == pairs
    if codes:
        assert mine_phase == phase
    assert {code.value for code in report.warning_codes} == warnings


def test_the_oracle_is_the_pinned_one():
    # Loaded from the vendored copy, whose hash test_spec_pin.py holds to the pin.
    assert Path(refval().__file__).as_posix().endswith("tools/audit/refval.py")


def _mine(doc: dict):
    for phase, check in PHASES:
        issues = check(doc)
        if issues:
            return phase, *verdict([(i.code.value, i.path) for i in issues.items])
    return PHASES[-1][0], frozenset(), frozenset()


def _oracle_warnings(doc: dict) -> set[str]:
    report = refval().Report()
    refval().warnings(doc, report)
    return set(report.warning_codes)


def _documents():
    by_number = {item.name.split("_")[0]: item for item in fixtures("valid")}
    for number in MUTATED:
        yield number, dict(read_description_bytes(by_number[number].data).document)


COVERED: set[str] = set()


@pytest.mark.parametrize(
    ("number", "doc"), list(_documents()), ids=lambda v: v if isinstance(v, str) else ""
)
def test_the_reader_and_the_oracle_agree_on_every_one_step_mutation(number, doc):
    checked = 0
    for label, mutated in mutations(doc):
        phase, codes, pairs = oracle_document_verdict(mutated, DOCUMENT_PHASES)
        got = _mine(mutated)
        assert (got[0], set(got[1]), set(got[2])) == (phase, codes, pairs), f"{number}: {label}"
        if not codes:
            mine_warnings = {issue.code.value for issue in check_warnings(mutated).items}
            assert mine_warnings == _oracle_warnings(mutated), f"{number}: {label}"
            COVERED.add("PASS")
        COVERED.update(codes)
        checked += 1
    assert checked > 1000


def test_the_mutations_reach_every_error_code_of_phases_4_and_5():
    # Runs after the mutation tests of this file (collection order): a rule the corpus never
    # triggers would be a rule the cross-check never compared.
    assert PHASE_4_AND_5 <= COVERED, sorted(PHASE_4_AND_5 - COVERED)
