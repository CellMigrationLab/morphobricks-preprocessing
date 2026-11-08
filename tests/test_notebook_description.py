"""Step 1's description cards: IDs, shown-as names, pairing and references (Stage 1, PR 14).

Widgets are driven headless, as in ``test_notebook_ui``. The cards hold the answers of the draft
experiment description; every answer is saved to ``morphobricks.draft.json``, and nothing is
confirmed until the user clicks.
"""

import html
import shutil

import pytest

from morphobricks_preprocessing.description_file import DRAFT_FILENAME
from morphobricks_preprocessing.draft_format import SPEC_COMMIT, answers_from_bytes
from tiff_tree import design_tree

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402

FOLDERS = ["ctrl/rep1", "ctrl/rep2", "drug/rep1", "drug/experiment_23"]


@pytest.fixture
def root(tmp_path):
    return design_tree(tmp_path / "exp", FOLDERS)


@pytest.fixture
def out(tmp_path):
    return tmp_path / "out"


def labelled(root, out):
    """Open the notebook on ``root``, check the folder and confirm the labels."""
    nb = PreprocessingNotebook(str(root), output_folder=str(out))
    for step in (nb.welcome, nb.folder_step, nb.axis_step, nb.run_step, nb.results_step):
        step()
    nb.check_button.click()
    nb.labels_confirmed.button.click()
    return nb


def saved(out):
    """Return the answers on disk, read with the strict reader."""
    return answers_from_bytes((out / DRAFT_FILENAME).read_bytes())


def pair_experiment_23(nb):
    """Choose repeat group rep2 for drug's folder experiment_23, in the design card."""
    (dropdown,) = [
        box.children[2]
        for box in nb.pairing_grid.children[1:]
        if "experiment_23" in box.children[1].value and "drug" in box.children[0].value
    ]
    dropdown.value = "rep2"


def test_the_names_are_proposed_from_the_folders_and_nothing_is_confirmed(root, out):
    nb = labelled(root, out)
    assert ui.is_visible(nb.names_card) and ui.is_visible(nb.references_card)
    assert {key: box.value for key, box in nb.id_inputs.items()} == {
        ("conditions", "ctrl"): "ctrl",
        ("conditions", "drug"): "drug",
        ("repeats", "experiment_23"): "experiment_23",
        ("repeats", "rep1"): "rep1",
        ("repeats", "rep2"): "rep2",
    }
    # The technical-repeat level does not exist here: single is not asked about.
    assert all(kind != "technical_repeats" for kind, _ in nb.id_inputs)
    assert "proposed" in nb._id_states[("conditions", "ctrl")].value
    assert "confirmed" not in nb._id_states[("conditions", "ctrl")].value
    assert not any(box.disabled for box in nb.id_inputs.values())
    assert not nb.ids_confirmed.value
    assert all(slot.confirmed is None for slot in nb.draft.conditions.values())
    assert {q.code for q in nb.draft.open_questions()} >= {"id_pending"}
    assert not (out / DRAFT_FILENAME).exists()  # no answer given, nothing saved
    assert "Nothing is saved yet" in nb.names_status.value


def test_a_click_confirms_the_ids_and_saves_them(root, out):
    nb = labelled(root, out)
    nb.ids_confirmed.button.click()
    assert nb.ids_confirmed.value
    assert "confirmed" in nb._id_states[("conditions", "ctrl")].value
    assert all(box.disabled for box in nb.id_inputs.values())
    assert {c: s.confirmed for c, s in nb.draft.conditions.items()} == {
        "ctrl": "ctrl",
        "drug": "drug",
    }
    assert {c: a.id for c, a in saved(out).conditions.items()} == {"ctrl": "ctrl", "drug": "drug"}
    assert saved(out).technical_repeats == {"single": "single"}  # the level that does not exist
    assert "saved as you give them" in nb.names_status.value
    # The user can take it back.
    nb.ids_confirmed.button.click()
    assert not nb.ids_confirmed.value and not any(b.disabled for b in nb.id_inputs.values())
    assert saved(out).conditions == {} and saved(out).technical_repeats == {}


def test_a_typed_id_is_adopted_as_typed(root, out):
    nb = labelled(root, out)
    nb.id_inputs[("conditions", "ctrl")].value = "UNTREATED"
    assert "typed" in nb._id_states[("conditions", "ctrl")].value
    assert not (out / DRAFT_FILENAME).exists()  # typing is not an answer
    nb.ids_confirmed.button.click()
    assert saved(out).conditions["ctrl"].id == "UNTREATED"


def test_an_invalid_id_is_refused_with_the_readers_message_and_nothing_is_confirmed(tmp_path):
    root = design_tree(tmp_path / "exp", ["control/rep1", "drug/rep1"])
    out = tmp_path / "out"
    nb = labelled(root, out)
    # "control" is a forbidden name: no proposal, and the box starts empty.
    assert nb.id_inputs[("conditions", "control")].value == ""
    assert "needs a name" in nb._id_states[("conditions", "control")].value
    assert any(
        "cannot be its own ID" in getattr(w, "value", "") for w in _all_widgets(nb.names_grid)
    )
    nb.ids_confirmed.button.click()
    assert not nb.ids_confirmed.value
    assert "nothing was confirmed" in nb.names_status.value
    assert "E_ID_TEXT" in nb.names_status.value
    nb.id_inputs[("conditions", "control")].value = "files"
    nb.ids_confirmed.button.click()
    assert not nb.ids_confirmed.value
    message = "'files' cannot be the ID of conditions 'control': E_FORBIDDEN_FIELD"
    assert html.escape(message) in nb.names_status.value
    nb.id_inputs[("conditions", "control")].value = " ctrl"  # no trimming
    nb.ids_confirmed.button.click()
    assert not nb.ids_confirmed.value and "E_ID_TEXT" in nb.names_status.value
    assert all(slot.confirmed is None for slot in nb.draft.conditions.values())
    assert not (out / DRAFT_FILENAME).exists()
    nb.id_inputs[("conditions", "control")].value = "ctrl"
    nb.ids_confirmed.button.click()
    assert nb.ids_confirmed.value
    assert saved(out).conditions["control"].id == "ctrl"


def test_two_conditions_cannot_share_an_id(root, out):
    nb = labelled(root, out)
    nb.id_inputs[("conditions", "drug")].value = "ctrl"
    nb.ids_confirmed.button.click()
    assert not nb.ids_confirmed.value
    assert "another one already has this ID" in nb.names_status.value
    assert not (out / DRAFT_FILENAME).exists()


def test_two_differently_named_folders_are_paired_into_one_repeat(root, out):
    nb = labelled(root, out)
    assert ("repeats", "experiment_23") in nb.id_inputs
    pair_experiment_23(nb)
    assert sorted(nb.draft.repeats) == ["rep1", "rep2"]
    assert ("repeats", "experiment_23") not in nb.id_inputs
    members = [m.key for m in nb.draft.repeats["rep2"].members]
    assert members == [("ctrl", "rep2"), ("drug", "experiment_23")]
    # The names card shows both folders under the repeat, with their image counts.
    row = " ".join(getattr(w, "value", "") for w in _all_widgets(nb.names_grid))
    assert "ctrl/rep2" in row and "drug/experiment_23" in row
    assert dict(saved(out).pairing) == {("drug", "experiment_23"): "rep2"}
    nb.ids_confirmed.button.click()
    assert saved(out).repeats["rep2"].id == "rep2"
    assert "experiment_23" not in saved(out).repeats


def test_pairing_and_unpairing_ask_only_for_the_names_that_are_new(root, out):
    nb = labelled(root, out)
    nb.ids_confirmed.button.click()
    assert nb.ids_confirmed.value
    pair_experiment_23(nb)
    # rep1 and rep2 keep the names the user confirmed; experiment_23 is no longer a repeat.
    assert nb.ids_confirmed.value
    assert "No longer a repeat of its own: experiment_23" in nb.names_status.value
    assert "experiment_23" not in saved(out).repeats
    (dropdown,) = [
        box.children[2]
        for box in nb.pairing_grid.children[1:]
        if "experiment_23" in box.children[1].value and "drug" in box.children[0].value
    ]
    dropdown.value = "experiment_23"  # take the folder out of rep2 again
    assert not nb.ids_confirmed.value  # experiment_23 is a repeat again, and unnamed
    assert not nb.id_inputs[("repeats", "experiment_23")].disabled
    assert nb.id_inputs[("repeats", "rep1")].disabled
    assert dict(saved(out).pairing) == {}


def test_the_comparable_date_of_a_repeat_is_the_design_cards_click(root, out):
    nb = labelled(root, out)
    pair_experiment_23(nb)
    nb.group_confirmations["rep1"].button.click()
    day = nb.design.confirmed_on("rep1")
    assert day and saved(out).repeats["rep1"].confirmed_on == day
    assert "rep2" not in saved(out).repeats  # not confirmed: no date, no entry
    nb.group_confirmations["rep1"].button.click()  # undo
    assert saved(out).repeats == {}


def test_a_reference_is_chosen_saved_and_marks_the_control(root, out):
    nb = labelled(root, out)
    assert nb.reference_boxes["drug"].value is None  # nothing is chosen for the user
    assert not ui.is_visible(nb.controls_box)  # the design card's tick boxes are replaced
    nb.reference_boxes["drug"].value = "ctrl"
    assert dict(nb.draft.references) == {"drug": "ctrl"}
    assert saved(out).conditions["drug"].reference == "ctrl"
    assert nb.design.controls == ("ctrl",)  # experiment_design.csv follows the references
    assert "the reference of drug" in nb.references_grid.children[1].children[2].value
    nb.reference_boxes["drug"].value = None
    assert saved(out).conditions == {} and nb.design.controls == ()


def test_a_loop_of_references_is_refused_at_once(root, out):
    nb = labelled(root, out)
    nb.reference_boxes["drug"].value = "ctrl"
    before = (out / DRAFT_FILENAME).read_bytes()
    nb.reference_boxes["ctrl"].value = "drug"
    assert "ctrl → drug → ctrl is a loop" in nb.references_status.value
    assert nb.reference_boxes["ctrl"].value is None  # the box goes back to what is saved
    assert dict(nb.draft.references) == {"drug": "ctrl"}
    assert (out / DRAFT_FILENAME).read_bytes() == before


def test_a_shown_as_name_is_saved_and_the_id_stays(root, out):
    nb = labelled(root, out)
    nb.name_inputs[("conditions", "ctrl")].value = "Untreated cells"
    assert saved(out).conditions["ctrl"].display_name == "Untreated cells"
    assert saved(out).conditions["ctrl"].id is None  # the name is not a confirmation
    nb.ids_confirmed.button.click()
    assert saved(out).conditions["ctrl"].id == "ctrl"
    nb.name_inputs[("conditions", "ctrl")].value = ""
    assert saved(out).conditions["ctrl"].display_name is None


def test_a_saved_draft_resumes_with_its_answers_shown(root, out):
    first = labelled(root, out)
    pair_experiment_23(first)
    first.id_inputs[("conditions", "ctrl")].value = "UNTREATED"
    first.ids_confirmed.button.click()
    first.name_inputs[("conditions", "drug")].value = "Drug A"
    first.reference_boxes["drug"].value = "ctrl"
    first.group_confirmations["rep1"].button.click()
    before = (out / DRAFT_FILENAME).read_bytes()

    again = labelled(root, out)
    assert "Your saved answers were read" in again.names_status.value
    assert again.ids_confirmed.value
    assert again.id_inputs[("conditions", "ctrl")].value == "UNTREATED"
    assert again.id_inputs[("conditions", "ctrl")].disabled
    assert sorted(again.draft.repeats) == ["rep1", "rep2"]
    assert again.name_inputs[("conditions", "drug")].value == "Drug A"
    assert again.reference_boxes["drug"].value == "ctrl"
    assert again.draft.repeats["rep1"].confirmed and not again.draft.repeats["rep2"].confirmed
    assert (out / DRAFT_FILENAME).read_bytes() == before  # looking changes nothing


def answered_tree(root, out):
    """Pair, name, confirm, refer and confirm comparability, saving everything."""
    nb = labelled(root, out)
    pair_experiment_23(nb)
    nb.name_inputs[("conditions", "drug")].value = "Drug A"
    nb.ids_confirmed.button.click()
    nb.reference_boxes["drug"].value = "ctrl"
    return nb


def test_a_folder_removed_after_pairing_keeps_the_other_answers_and_names_the_dropped(root, out):
    answered_tree(root, out)
    before = (out / DRAFT_FILENAME).read_bytes()
    shutil.rmtree(root / "drug" / "experiment_23")
    again = labelled(root, out)
    assert "Your saved answers were read" in again.names_status.value
    assert "folder drug/experiment_23 (paired with rep2)" in again.names_status.value
    assert (out / DRAFT_FILENAME).read_bytes() == before  # looking changes nothing
    assert again.ids_confirmed.value  # ctrl, drug, rep1 and rep2 keep the IDs confirmed
    assert again.name_inputs[("conditions", "drug")].value == "Drug A"
    assert again.reference_boxes["drug"].value == "ctrl"
    again.name_inputs[("conditions", "ctrl")].value = "Cells"  # the next answer
    assert dict(saved(out).pairing) == {}
    assert saved(out).conditions["drug"].reference == "ctrl"
    assert saved(out).repeats["rep2"].id == "rep2" and not list(out.glob("*.unused-*"))


def test_a_condition_removed_after_answering_is_listed_and_the_rest_is_kept(root, out):
    answered_tree(root, out)
    shutil.rmtree(root / "drug")
    again = labelled(root, out)
    note = again.names_status.value
    assert "condition drug" in note and "folder drug/experiment_23" in note
    assert again.id_inputs[("conditions", "ctrl")].disabled  # ctrl's ID is still confirmed
    assert again.id_inputs[("conditions", "ctrl")].value == "ctrl"
    assert saved(out).conditions["drug"].reference == "ctrl"  # on disk until the next answer


def test_a_saved_pairing_that_differs_from_the_design_follows_the_design(root, out):
    first = labelled(root, out)
    pair_experiment_23(first)
    first.ids_confirmed.button.click()
    (out / "experiment_design.csv").unlink()  # the design forgets the pairing
    before = (out / DRAFT_FILENAME).read_bytes()
    again = labelled(root, out)
    assert "pairing of the experiment design above is used" in again.names_status.value
    assert again.id_inputs[("repeats", "rep1")].disabled  # kept
    assert not again.ids_confirmed.value  # experiment_23 is a repeat again, and unnamed
    assert (out / DRAFT_FILENAME).read_bytes() == before
    again.ids_confirmed.button.click()
    assert dict(saved(out).pairing) == {}
    assert saved(out).repeats["experiment_23"].id == "experiment_23"


def test_a_draft_that_cannot_be_used_is_copied_aside_and_never_overwritten(root, out):
    first = labelled(root, out)
    first.ids_confirmed.button.click()
    path = out / DRAFT_FILENAME
    path.write_bytes(path.read_bytes().replace(SPEC_COMMIT.encode(), b"1" * 40))
    stale = path.read_bytes()
    again = labelled(root, out)
    assert "was not used" in again.names_status.value
    assert "written under Spec commit " + "1" * 40 in again.names_status.value
    assert not again.ids_confirmed.value and path.read_bytes() == stale
    again.ids_confirmed.button.click()  # the next answer replaces the file ...
    assert path.read_bytes() != stale
    assert (out / "morphobricks.draft.unused-1.json").read_bytes() == stale  # ... after a copy
    path.write_bytes(stale)
    third = labelled(root, out)
    third.ids_confirmed.button.click()
    assert (out / "morphobricks.draft.unused-1.json").read_bytes() == stale  # never overwritten
    assert (out / "morphobricks.draft.unused-2.json").read_bytes() == stale


def test_without_a_writable_output_folder_the_answers_are_kept_in_the_notebook(root):
    nb = labelled(root, "")
    nb.output.value = ""
    nb.ids_confirmed.button.click()
    assert nb.ids_confirmed.value  # answered; saved with the default output folder if it can be


def test_a_flat_experiment_asks_for_no_condition_or_technical_repeat_names(tmp_path):
    root = design_tree(tmp_path / "exp", [""])  # images directly in the experiment folder
    out = tmp_path / "out"
    nb = labelled(root, out)
    assert sorted(nb.id_inputs) == [("repeats", "single")]
    assert not ui.is_visible(nb.references_card)  # one condition: nothing to compare
    nb.ids_confirmed.button.click()
    assert nb.ids_confirmed.value
    assert saved(out).conditions["single"].id == "single"


def _all_widgets(box):
    for child in getattr(box, "children", ()):
        yield child
        yield from _all_widgets(child)


def labels(box):
    """Return the dropdown's texts by value."""
    return {value: text for text, value in box.options}


def test_the_reference_dropdown_names_the_id_and_the_folder_and_follows_the_id(root, out):
    nb = labelled(root, out)
    assert labels(nb.reference_boxes["drug"])["ctrl"] == "ctrl (folder ctrl)"
    nb.id_inputs[("conditions", "ctrl")].value = "UNTREATED"  # typed, not confirmed
    assert labels(nb.reference_boxes["drug"])["ctrl"] == "UNTREATED (folder ctrl)"
    nb.reference_boxes["drug"].value = "ctrl"
    nb.id_inputs[("conditions", "ctrl")].value = "UNT"
    assert labels(nb.reference_boxes["drug"])["ctrl"] == "UNT (folder ctrl)"
    assert nb.reference_boxes["drug"].value == "ctrl"  # the choice is kept
    assert saved(out).conditions["drug"].reference == "ctrl"
    nb.ids_confirmed.button.click()
    assert labels(nb.reference_boxes["ctrl"])["drug"] == "drug (folder drug)"
    assert labels(nb.reference_boxes["drug"])["ctrl"] == "UNT (folder ctrl)"


def test_an_unanswered_reference_and_no_comparison_look_different(root, out):
    nb = labelled(root, out)
    box = nb.reference_boxes["drug"]
    assert [text for text, _ in box.options[:2]] == ["— choose —", "no comparison"]
    assert box.value is None and not (out / DRAFT_FILENAME).exists()
    box.value = ""  # the user answered: compared against nothing
    assert box.value == "" and saved(out).conditions["drug"].reference == ""
    assert dict(nb.draft.references) == {} and nb.design.controls == ()
    again = labelled(root, out)
    assert again.reference_boxes["drug"].value == ""  # resumed as answered, not as unanswered
    assert again.reference_boxes["ctrl"].value is None
    again.reference_boxes["drug"].value = None  # back to unanswered
    assert saved(out).conditions == {}


def test_the_design_summary_follows_the_references(root, out):
    nb = labelled(root, out)
    for toggle in nb.group_confirmations.values():
        toggle.button.click()  # the summary is shown once the repeats are confirmed
    assert "Controls: none" in nb.design_status.value
    nb.reference_boxes["drug"].value = "ctrl"
    assert "Compared against (from references): drug → ctrl" in nb.design_status.value
    assert "Controls:" not in nb.design_status.value
    nb.reference_boxes["drug"].value = ""
    assert "Compared against (from references): nothing" in nb.design_status.value


def test_the_names_card_says_in_bold_which_levels_get_the_id_single(root, out, tmp_path):
    nb = labelled(root, out)  # conditions and repeats, no technical-repeat folders
    note = nb.single_note.value
    assert "<b>Your experiment has no technical-repeat folders: they get the ID " in note
    assert "they get the ID single.</b>" in note and "condition folders" not in note
    flat = labelled(design_tree(tmp_path / "flat", [""]), tmp_path / "flat_out")
    assert "no condition folders and no technical-repeat folders" in flat.single_note.value
    deep = design_tree(tmp_path / "deep", ["ctrl/rep1/t1", "ctrl/rep1/t2", "drug/rep1/t1"])
    nb3 = labelled(deep, tmp_path / "deep_out")
    assert ("technical_repeats", "t1") in nb3.id_inputs
    assert nb3.single_note.value == ""  # nothing to say when every level exists


def test_a_duplicate_shown_as_name_gets_an_orange_hint_as_it_is_typed(root, out):
    nb = labelled(root, out)
    ctrl, drug = nb.name_hints[("conditions", "ctrl")], nb.name_hints[("conditions", "drug")]
    assert ctrl.value == drug.value == ""
    nb.name_inputs[("conditions", "ctrl")].value = "Cells"
    nb.name_inputs[("conditions", "drug")].value = "cells"  # the same ignoring case
    assert "also shown for condition ctrl" in drug.value and "#C2410C" in drug.value
    assert "also shown for condition drug" in ctrl.value
    assert "also shown" not in nb.name_hints[("repeats", "rep1")].value  # another kind
    assert saved(out).conditions["drug"].display_name == "cells"  # saved; review refuses it
    nb.name_inputs[("conditions", "drug")].value = "Drug A"
    assert ctrl.value == drug.value == ""
    nb.name_inputs[("conditions", "drug")].value = "Cells"
    again = labelled(root, out)  # resumed: the hint is shown again
    assert "also shown for condition" in again.name_hints[("conditions", "ctrl")].value


def test_typing_a_shown_as_name_keeps_the_box_being_typed_in(root, out):
    nb = labelled(root, out)
    box = nb.name_inputs[("conditions", "ctrl")]
    box.value = "Un"
    assert nb.name_inputs[("conditions", "ctrl")] is box  # not rebuilt under the cursor


def test_the_ids_gate_neither_step_2_nor_run(root, out):
    nb = labelled(root, out)
    pair_experiment_23(nb)
    for toggle in nb.group_confirmations.values():
        toggle.button.click()
    assert not nb.ids_confirmed.value
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    nb.image_type_checked.button.click()
    assert not nb.run_button.disabled
    nb.run_button.click()
    assert nb.result.written
