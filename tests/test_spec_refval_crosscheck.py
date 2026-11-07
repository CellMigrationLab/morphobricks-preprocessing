"""The reader against the Spec's reference validator, the oracle (conformance plan section 2).

The reference validator (``tools/audit/refval.py``, vendored at the pin, loaded by path) is the
oracle: the reader must give the same phase, the same set of codes and the same table paths on
every fixture, and on every one-step mutation of the valid fixtures (a field deleted, retyped,
renamed or duplicated, one at a time). A difference is a bug in the reader or, if the text is
ambiguous, a point to take to the Spec: it is never settled by editing the oracle.
"""

from __future__ import annotations

import pytest

from morphobricks_preprocessing.description_read import PHASES, read_structure
from spec_oracle import mutations, normal, oracle_document_verdict, oracle_verdict, refval
from spec_pin import all_fixtures, fixtures

ALL = all_fixtures()
MUTATED = ("v01", "v03", "v07", "v11", "v13", "v28", "v33", "v34", "v35")
DOCUMENT_PHASES = tuple(number for number, _ in PHASES)


def _reader_verdict(data: bytes):
    report = read_structure(data)
    paths = frozenset(normal(issue.path) for issue in report.errors)
    return report.phase, frozenset(code.value for code in report.codes), paths


@pytest.mark.parametrize("item", ALL, ids=lambda item: f"{item.kind}/{item.name}")
def test_the_reader_and_the_oracle_agree_on_a_fixture(item):
    phase, codes, paths, _ = oracle_verdict(item.data)
    mine_phase, mine_codes, mine_paths = _reader_verdict(item.data)
    if not codes:
        assert mine_codes == frozenset()
    elif phase <= max(DOCUMENT_PHASES):
        assert (mine_phase, mine_codes, mine_paths) == (phase, codes, paths)
    else:
        # The oracle went on to a phase the reader does not implement yet: nothing earlier.
        assert mine_codes == frozenset()


def test_the_oracle_is_the_pinned_one():
    # Loaded from the vendored copy, whose hash test_spec_pin.py holds to the pin.
    assert refval().__file__.endswith("tools/audit/refval.py")


def _mine(doc: dict):
    for phase, check in PHASES:
        issues = check(doc)
        if issues:
            codes = {issue.code.value for issue in issues.items}
            return phase, codes, {normal(issue.path) for issue in issues.items}
    return PHASES[-1][0], set(), set()


def _documents():
    by_number = {item.name.split("_")[0]: item for item in fixtures("valid")}
    for number in MUTATED:
        report = read_structure(by_number[number].data)
        yield number, dict(report.document)


@pytest.mark.parametrize(
    ("number", "doc"), list(_documents()), ids=lambda v: v if isinstance(v, str) else ""
)
def test_the_reader_and_the_oracle_agree_on_every_one_step_mutation(number, doc):
    checked = 0
    for label, mutated in mutations(doc):
        expected = oracle_document_verdict(mutated, DOCUMENT_PHASES)
        got = _mine(mutated)
        assert got == expected, f"{number}: {label}"
        checked += 1
    assert checked > 500
