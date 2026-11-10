"""The draft of an experiment description: proposed IDs.

Experiment-v1 section 4.6a, conformance scenario P32 (the proposal part). The
owner's answers: Q4 a header name is proposed as a channel ID only when it is valid as it
stands, Q6 the proposal rule covers repeat IDs and the condition ``single`` rule, Q8 the added
fields are ``condition_id``, ``repeat_group`` and ``technical_repeat_id``.
"""

import pytest

from morphobricks_preprocessing import discover_experiment
from morphobricks_preprocessing.draft import DraftError, draft_from_discovery
from morphobricks_preprocessing.draft_ids import (
    id_problems,
    propose_channel_ids,
    propose_id,
)
from tiff_tree import make_tree


def folders(root, *paths):
    """Write two images into each folder path and return the discovery of the root."""
    make_tree(root, [f"{p}/{name}.tif" for p in paths for name in ("a", "b")])
    return discover_experiment(root)


# --- proposed IDs (P32, Q4, Q6) ---------------------------------------------------------------


def test_the_folder_label_is_proposed_when_the_reader_would_accept_it():
    assert propose_id("conditions", "CTRL") == "CTRL"
    assert propose_id("technical_repeats", "acquisition_1") == "acquisition_1"
    assert propose_id("repeats", "rep1") == "rep1"
    assert propose_id("conditions", "single") == "single"
    assert propose_id("repeats", "R999") == "R999"  # no width or prefix rule


@pytest.mark.parametrize(
    ("kind", "label"),
    [
        ("conditions", "control"),
        ("technical_repeats", "files"),
        ("technical_repeats", "overrides"),
        ("repeats", "z"),  # Q6: a repeat folder gets no proposal either
        ("repeats", "paths"),
        ("conditions", "NA"),
        ("conditions", " CTRL"),
        ("conditions", "Single"),
    ],
)
def test_a_label_that_is_not_an_allowed_id_gets_no_proposal_and_is_never_repaired(kind, label):
    assert id_problems(kind, label)
    assert propose_id(kind, label) is None


def test_a_label_that_collides_with_a_used_id_gets_no_proposal():
    assert propose_id("repeats", "rep1", ["rep1"]) is None
    assert id_problems("repeats", "rep1", ["rep1"]) == ("R_USED_ID_REUSED",)
    assert id_problems("conditions", "ctrl", ["CTRL"]) == ("E_ID_CASE_COLLISION",)
    assert propose_id("conditions", "ctrl", ["CTRL"]) is None


def test_p32_forbidden_folders_get_no_proposal_and_nothing_is_adopted(tmp_path):
    root = tmp_path / "exp"
    discovery = folders(root, "control/rep1/files", "control/rep1/overrides", "DrugA/rep1/files")
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    draft = draft_from_discovery(discovery)
    assert draft.conditions["control"].proposed is None
    assert draft.conditions["DrugA"].proposed == "DrugA"
    assert draft.technical_repeats["files"].proposed is None
    assert draft.technical_repeats["overrides"].proposed is None
    assert all(slot.confirmed is None for slot in draft.conditions.values())
    assert not draft.publishable
    assert {q.code for q in draft.open_questions()} >= {"id_pending", "repeat_not_confirmed"}
    assert before == sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))  # no file written
    assert sorted(p.name for p in root.iterdir()) == ["DrugA", "control"]  # no folder renamed


def test_p32_ordinary_folders_are_proposed_as_their_labels(tmp_path):
    draft = draft_from_discovery(
        folders(tmp_path / "exp", "CTRL/rep1/acquisition_1", "CTRL/rep2/acquisition_1")
    )
    assert draft.conditions["CTRL"].proposed == "CTRL"
    assert draft.technical_repeats["acquisition_1"].proposed == "acquisition_1"
    assert {g: r.id.proposed for g, r in draft.repeats.items()} == {"rep1": "rep1", "rep2": "rep2"}


def test_a_repeat_folder_named_z_has_no_proposal_and_the_user_names_it(tmp_path):
    draft = draft_from_discovery(folders(tmp_path / "exp", "A/z", "B/z", "A/rep2", "B/rep2"))
    assert draft.repeats["z"].id.proposed is None
    assert [m.condition for m in draft.repeats["z"].members] == ["A", "B"]
    assert draft.repeats["z"].members[0].source_repeat == "z"  # the source label is kept
    with pytest.raises(DraftError) as refused:
        draft.confirm_id("repeats", "z", "z")
    assert "E_FORBIDDEN_FIELD" in refused.value.codes
    named = draft.confirm_id("repeats", "z", "R1")
    assert named.repeats["z"].id.confirmed == "R1"
    assert draft.repeats["z"].id.confirmed is None  # a draft is never changed in place


def test_a_flat_experiment_proposes_single_for_every_level(tmp_path):
    draft = draft_from_discovery(folders(tmp_path / "exp", "."))
    assert draft.conditions["single"].proposed == "single"
    assert draft.technical_repeats["single"].proposed == "single"
    assert draft.repeats["single"].id.proposed == "single"
    assert draft.unit_rows()[0].condition == "single"


def test_the_condition_single_rule_ties_the_id_to_its_folder(tmp_path):
    draft = draft_from_discovery(folders(tmp_path / "exp", "."))
    with pytest.raises(DraftError) as refused:
        draft.confirm_id("conditions", "single", "main")
    assert refused.value.codes == ("E_SINGLE_MISUSE",)
    draft = draft_from_discovery(folders(tmp_path / "two", "A/rep1", "B/rep1"))
    with pytest.raises(DraftError) as refused:
        draft.confirm_id("conditions", "A", "single")
    assert refused.value.codes == ("E_SINGLE_MISUSE",)


def test_used_ids_are_never_proposed_again(tmp_path):
    discovery = folders(tmp_path / "exp", "A/rep1", "A/rep2")
    draft = draft_from_discovery(discovery, used_ids={"repeats": ["rep1"], "conditions": ["a"]})
    assert draft.repeats["rep1"].id.proposed is None
    assert draft.repeats["rep2"].id.proposed == "rep2"
    assert draft.conditions["A"].proposed is None  # `a` is retired and collides ignoring case


def test_ids_in_the_draft_appear_beside_the_unchanged_source_labels(tmp_path):
    discovery = folders(tmp_path / "exp", "control/rep1", "DrugA/rep1")
    draft = draft_from_discovery(discovery)
    draft = draft.confirm_id("conditions", "control", "ctrl")
    draft = draft.confirm_id("conditions", "DrugA", "DrugA")
    draft = draft.confirm_id("technical_repeats", "single", "single")
    draft = draft.confirm_id("repeats", "rep1", "rep1")
    rows = {r.condition: r for r in draft.unit_rows()}
    assert rows["control"].condition_id == "ctrl"
    assert rows["control"].biological_repeat == "rep1"
    assert (rows["control"].repeat_group, rows["control"].technical_repeat_id) == ("rep1", "single")
    assert rows["DrugA"].condition_id == "DrugA"
    with pytest.raises(DraftError) as refused:
        draft.confirm_id("conditions", "DrugA", "CTRL")  # differs from `ctrl` only in case
    assert refused.value.codes == ("E_ID_CASE_COLLISION",)


def test_counts_come_from_the_folders_and_excluded_images_are_not_counted(tmp_path):
    discovery = folders(tmp_path / "exp", "A/rep1/t1", "A/rep1/t2")
    draft = draft_from_discovery(discovery, excluded=["A/rep1/t2/a.tif", "A/rep1/t2/b.tif"])
    (member,) = draft.repeats["rep1"].members
    assert member.image_count == 2
    assert [(u.source_folder, u.image_count) for u in member.units] == [("t1", 2)]


# --- channel IDs from raw header names (Q4) ----------------------------------------------------


def proposed(names, used=()):
    return [p.proposed_id for p in propose_channel_ids(names, used)]


def test_a_header_name_is_proposed_as_the_channel_id_only_when_valid_as_it_stands():
    assert proposed(["DAPI", "Phalloidin488", "ACTIN_2"]) == ["DAPI", "Phalloidin488", "ACTIN_2"]
    assert proposed(["DAPI 405"]) == [None]  # never repaired to DAPI_405
    assert proposed([" DAPI"]) == [None]  # never trimmed
    assert proposed(["DAPI-405", "405nm", "a__b"]) == [None, None, None]
    assert proposed(["cell", "Nucleus", "z"]) == [None, None, None]  # reserved
    assert proposed(["R", "g", "B"]) == [None, None, None]  # fixed RGB IDs, no aliases


def test_unnamed_and_duplicate_header_names_get_no_proposal():
    assert proposed([None, "", "DAPI"]) == [None, None, "DAPI"]
    assert proposed(["DAPI", "dapi"]) == [None, None]  # one channel cannot stand at two indices
    assert proposed(["DAPI"], used=["Dapi"]) == [None]  # retired ID ignoring case


def test_a_proposal_keeps_the_raw_header_name_and_its_index():
    (first, second) = propose_channel_ids(["DAPI", " raw "])
    assert (first.index, first.header_name, first.proposed_id) == (0, "DAPI", "DAPI")
    assert (second.index, second.header_name, second.proposed_id) == (1, " raw ", None)
