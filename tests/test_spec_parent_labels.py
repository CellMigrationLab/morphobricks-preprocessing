"""Parent-label (grouped) masks: Spec PL1 at 69a58b3 (experiment-v1 4.5, 4.5a-4.5c, 7, 8).

- The reader accepts ``representation = "parent_labels"`` (``v38``, ``v39``) and refuses
  ``grouped`` and a re-cased value (``i136``, ``i137``, ``E_ENUM``), a grouping field and the
  capability in the file (``i138``, ``i139``, ``E_UNKNOWN_FIELD``); ``i111`` is retired.
- The mask answer is the user's (never a default, never the scan's ``grouped`` kind).
- The mask step records a ``parent_labels`` file as checked once it has read it (4.5a): no
  other condition, so a file with any number of values passes.
- Resolver contract version 2 (Spec ADR 0002): as objects ``A_MASK_NOT_INSTANCES`` with or
  without ``parent_labels_support``, never a ``binary_mask_policy``; as a region, the groups
  for a consumer with the capability, else ``conversion = "parent_labels_as_area"``.
"""

from __future__ import annotations

import pytest

from morphobricks_preprocessing import (
    PARENT_LABELS_AS_AREA,
    RESOLVER_VERSION,
    Capabilities,
    MaskNotInstancesError,
    MaskRequest,
    Request,
    RunInputs,
)
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_read import read_description_bytes
from morphobricks_preprocessing.draft_answers import DraftAnswers
from morphobricks_preprocessing.draft_format import ABSENT, SPEC_COMMIT
from morphobricks_preprocessing.mask_answers import (
    KIND_WORDS,
    MaskAnswerRefused,
    mask_kinds,
    with_mask,
)
from morphobricks_preprocessing.mask_representation import read_checked
from morphobricks_preprocessing.run_inputs_request import (
    Refusal,
    check_request,
    mask_representation_refusal,
)
from morphobricks_preprocessing.testing import make_described_run
from spec_pin import all_fixtures, fixtures, read_pin

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}
INVALID = {item.name.split("_")[0]: item for item in fixtures("invalid")}
PINNED = "69a58b3e2f89b318364c4c1252c54caf676fb6bb"
EMPTY = DraftAnswers(ABSENT, SPEC_COMMIT)
GROUPS = Capabilities(parent_labels_support=True)


# --- The pin and the fixtures (conformance plan sections 1 and 2) ---------------------------


def test_the_spec_is_pinned_at_the_pl1_merge():
    assert read_pin()["commit"] == SPEC_COMMIT == PINNED


@pytest.mark.parametrize(("number", "masks"), [("v38", {"Cells", "Mito"}), ("v39", {"Nucleoli"})])
def test_a_parent_labels_mask_is_read_as_declared(number, masks):
    report = read_description_bytes(VALID[number].data)
    assert report.errors == () and report.warnings == ()
    described = description_from_bytes(VALID[number].data)
    assert set(described.masks) == masks
    grouped = {k for k, m in described.masks.items() if m.representation == "parent_labels"}
    assert grouped == ({"Mito"} if number == "v38" else {"Nucleoli"})


@pytest.mark.parametrize(
    ("number", "code"),
    [
        ("i136", "E_ENUM"),  # "grouped": the scan's kind, not a representation
        ("i137", "E_ENUM"),  # "Parent_Labels": exact and lower case
        ("i138", "E_UNKNOWN_FIELD"),  # grouped_by: the file names no parent
        ("i139", "E_UNKNOWN_FIELD"),  # parent_labels_support: a brick's, never the file's
    ],
)
def test_the_pl1_invalid_fixtures_give_exactly_their_code(number, code):
    item = INVALID[number]
    assert item.expected_codes == {code}
    assert {c.value for c in read_description_bytes(item.data).codes} == {code}


def test_i111_is_retired_and_its_number_is_not_reused():
    names = [item.name for item in all_fixtures()]
    assert not any(name.split("_")[0] == "i111" for name in names)
    assert "was invalid/i111" in VALID["v38"].header["Source"]


# --- The mask answer (4.5: user-confirmed, never inferred) ----------------------------------


def test_grouped_labels_is_an_answer_of_its_own():
    answers = with_mask(EMPTY, "mito", "Mito", "parent_labels")
    assert answers.masks["Mito"].representation == "parent_labels"
    assert mask_kinds(answers) == {"mito": "grouped labels"}
    assert KIND_WORDS["parent_labels"] == "grouped labels"


@pytest.mark.parametrize("value", ["grouped", "Parent_Labels"])
def test_the_scan_kind_or_another_spelling_is_not_an_answer(value):
    with pytest.raises(MaskAnswerRefused) as refused:
        with_mask(EMPTY, "mito", "Mito", value)
    assert refused.value.codes == ("E_ENUM",)


# --- The mask check (4.5a) ------------------------------------------------------------------


def _v38(tmp_path, mask_step=True):
    data = VALID["v38"].data.replace(b"image_count = 12", b"image_count = 2")
    data = data.replace(b"image_count = 10", b"image_count = 2")
    run = make_described_run(tmp_path / "run", data, masks=("Cells", "Mito"), mask_step=mask_step)
    return RunInputs.open(run.output), run


def test_a_parent_labels_file_is_checked_once_the_mask_step_has_read_it(tmp_path):
    inputs, run = _v38(tmp_path)
    table = read_checked(run.output)
    # Three values per file (0 and two groups in two pieces each): a binary mask would be
    # refused; this one is recorded as checked, every file, with no other condition (4.5a).
    assert table["Mito"]["representation"] == "parent_labels"
    assert len(table["Mito"]["files"]) == len(table["Cells"]["files"]) == 4
    resolved = inputs.resolve_request(Request((), (MaskRequest("Mito", role="region"),)))
    assert not resolved.refused


def test_a_parent_labels_file_the_mask_step_has_not_read_is_unchecked(tmp_path):
    inputs, _ = _v38(tmp_path, mask_step=False)
    resolved = inputs.resolve_request(Request((), (MaskRequest("Mito", role="region"),)))
    assert [(r.code, r.subject) for r in resolved.refusals] == [("A_MASK_UNCHECKED", "Mito")]


# --- Resolver contract version 2 (ADR 0002) -------------------------------------------------


def test_the_resolver_contract_is_version_2():
    assert RESOLVER_VERSION == 2
    assert PARENT_LABELS_AS_AREA == "parent_labels_as_area"
    assert Capabilities().parent_labels_support is False


@pytest.mark.parametrize("capabilities", [Capabilities(), GROUPS], ids=["without", "with"])
def test_as_objects_it_is_refused_with_or_without_the_capability(tmp_path, capabilities):
    inputs, _ = _v38(tmp_path)
    request = Request(("DNA",), (MaskRequest("Mito"),), capabilities=capabilities)
    resolved = inputs.resolve_request(request)
    assert [(r.code, r.subject) for r in resolved.refusals] == [("A_MASK_NOT_INSTANCES", "Mito")]
    assert resolved.selections == ()
    with pytest.raises(MaskNotInstancesError):
        resolved.require()


def test_the_objects_refusal_never_suggests_a_binary_mask_policy():
    refusal = mask_representation_refusal(MaskRequest("Mito"), "parent_labels")
    assert isinstance(refusal, Refusal) and refusal.code == "A_MASK_NOT_INSTANCES"
    assert "binary_mask_policy" not in refusal.message
    assert "parent_labels" in refusal.message and "never split" in refusal.message
    assert "region" in refusal.message
    binary = mask_representation_refusal(MaskRequest("Area"), "binary")
    assert "binary_mask_policy='connected_components'" in binary.message  # unchanged


@pytest.mark.parametrize("role", ["objects", "region"])
def test_a_binary_mask_policy_on_it_is_a_malformed_request(tmp_path, role):
    inputs, _ = _v38(tmp_path)
    mask = MaskRequest("Mito", role=role, binary_mask_policy="connected_components")
    with pytest.raises(ValueError, match="declared parent_labels.*never split, relabelled"):
        inputs.resolve_request(Request((), (mask,)))


def test_as_a_region_without_the_capability_it_is_an_area_with_the_marker(tmp_path):
    inputs, _ = _v38(tmp_path)
    request = Request(("DNA",), (MaskRequest("Cells"), MaskRequest("Mito", role="region")))
    resolved = inputs.resolve_request(request)
    assert not resolved.refused and len(resolved.selections) == 4
    for selection in resolved.selections:
        cells, mito = selection.masks
        assert (cells.representation, cells.conversion) == ("instances", None)
        assert (mito.mask_id, mito.source_type, mito.role) == ("Mito", "mito", "region")
        assert mito.representation == "parent_labels"  # the declaration is kept
        assert mito.conversion == PARENT_LABELS_AS_AREA
    assert resolved.provenance.resolver_version == 2


def test_as_a_region_with_the_capability_it_is_the_groups(tmp_path):
    inputs, _ = _v38(tmp_path)
    request = Request((), (MaskRequest("Mito", role="region"),), capabilities=GROUPS)
    resolved = inputs.resolve_request(request)
    assert not resolved.refused
    for selection in resolved.selections:
        (mito,) = selection.masks
        assert (mito.representation, mito.conversion) == ("parent_labels", None)


def test_the_capability_must_be_a_bool(tmp_path):
    inputs, _ = _v38(tmp_path)
    request = Request((), (), capabilities=Capabilities(parent_labels_support=1))
    with pytest.raises(ValueError, match="capabilities must be Capabilities of bool"):
        inputs.resolve_request(request)


@pytest.mark.parametrize("capabilities", [None, {"parent_labels_support": True}, True])
def test_capabilities_that_are_not_capabilities_are_a_value_error(capabilities):
    # Review of PR #289: the three flags were read before the type check, so a request whose
    # capabilities were not a Capabilities raised AttributeError, not the contract's ValueError.
    request = Request((), (), capabilities=capabilities)
    with pytest.raises(ValueError, match="capabilities must be Capabilities of bool"):
        check_request(request, {})
