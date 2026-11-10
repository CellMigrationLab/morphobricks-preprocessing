import pytest

from described_tree import run_described
from morphobricks_preprocessing import (
    DESIGN_FILENAME,
    DesignCheckError,
    DesignFormatError,
    biological_repeats,
    default_design,
    default_output_folder,
    design_issues,
    discover_experiment,
    load_design,
    preflight,
    read_experiment_design,
    read_manifest,
    reconcile_design,
    run_experiment,
    write_experiment_design,
)
from morphobricks_preprocessing.axes import prefill_mapping
from morphobricks_preprocessing.manifest import file_sha256
from tiff_tree import make_tree

TODAY = "2026-10-26"
HEADER = (
    "condition,biological_repeat,repeat_group,control,image_count,technical_repeat_count,"
    "confirmed,confirmed_on,schema_version\n"
)


def codes(issues):
    return [issue.code for issue in issues]


def tree(root, counts):
    """Write ``counts[folder]`` images into each folder."""
    make_tree(root, [f"{folder}/img_{i:02d}.tif" for folder, n in counts.items() for i in range(n)])
    return root


def repeats_of(root, excluded=()):
    return biological_repeats(discover_experiment(root), excluded)


def confirm_all(design, repeats=()):
    for group in design.groups:
        design = design.with_confirmed(group, True, TODAY, repeats)
    return design


PAIRED = {
    "control/rep1/t1": 2,
    "control/rep1/t2": 1,
    "control/rep2": 2,
    "treated/rep1": 2,
    "treated/rep2_day2": 2,
}


# Pairing and confirmation ----------------------------------------------------------------


def test_same_named_repeats_pair_by_default(tmp_path):
    design = default_design(repeats_of(tree(tmp_path / "exp", PAIRED)))
    assert design.groups == ("rep1", "rep2", "rep2_day2")
    assert design.repeat_groups() == {
        ("control", "rep1"): "rep1",
        ("control", "rep2"): "rep2",
        ("treated", "rep1"): "rep1",
        ("treated", "rep2_day2"): "rep2_day2",
    }
    assert design.missing_conditions("rep2") == ("treated",)
    row = design.row(("control", "rep1"))
    assert (row.image_count, row.technical_repeat_count) == (3, 2)
    assert not design.confirmed and design.unconfirmed_groups == design.groups
    assert design.controls == () and not design.single_repeat


def test_moving_a_folder_pairs_it_and_asks_again(tmp_path):
    design = confirm_all(default_design(repeats_of(tree(tmp_path / "exp", PAIRED))))
    assert design.confirmed and design.confirmed_on("rep1") == TODAY
    moved = design.with_group(("treated", "rep2_day2"), "rep2")
    assert moved.groups == ("rep1", "rep2")
    assert moved.missing_conditions("rep2") == ()
    # The group that changed is asked again; the others keep their answer.
    assert moved.unconfirmed_groups == ("rep2",)
    assert moved.group_confirmed("rep1")
    assert moved.with_group(("treated", "rep2_day2"), "rep2") == moved


def test_a_group_cannot_hold_two_folders_of_a_condition(tmp_path):
    design = default_design(repeats_of(tree(tmp_path / "exp", PAIRED)))
    clash = confirm_all(design.with_group(("control", "rep2"), "rep1"))
    assert clash.conflicts == {"rep1": ("control",)}
    assert not clash.confirmed
    found = design_issues(clash)
    assert codes(found) == ["repeat_group_conflict"]
    assert found[0].is_error and found[0].paths == ("control/rep1", "control/rep2", "treated/rep1")


def test_unconfirmed_groups_are_errors(tmp_path):
    design = default_design(repeats_of(tree(tmp_path / "exp", PAIRED)))
    design = design.with_confirmed("rep1", True, TODAY)
    (found,) = design_issues(design)
    assert found.code == "design_not_confirmed" and found.is_error
    assert "2 repeat groups (rep2, rep2_day2) are not confirmed" in found.message
    assert found.paths == ("control/rep2", "treated/rep2_day2")
    assert design_issues(confirm_all(design)) == []


REMOVED_FIXES = ("design command", "--confirm", "experiment_design.csv", "repeat_group column")


def test_the_design_refusals_name_the_fix_of_a_described_run(tmp_path):
    # Review of PP-C: the actions named the removed design command and editing
    # experiment_design.csv, which is now an output only (derived from the description).
    design = default_design(repeats_of(tree(tmp_path / "exp", PAIRED)))
    clash = design.with_group(("control", "rep2"), "rep1")
    found = [*design_issues(design), *design_issues(clash)]
    unpaired = preflight(tree(tmp_path / "unpaired", {"a/r1": 1, "b/r2": 1})).issues
    found += [issue for issue in unpaired if issue.code == "repeat_missing_conditions"]
    assert {i.code for i in found} == {
        "design_not_confirmed",
        "repeat_group_conflict",
        "repeat_missing_conditions",
    }
    for issue in found:
        assert "Check and save" in issue.action, issue.code
        assert not [fix for fix in REMOVED_FIXES if fix in issue.action], issue.action


def test_without_biological_repeat_folders_one_question(tmp_path):
    design = default_design(repeats_of(tree(tmp_path / "exp", {"control": 2, "treated": 3})))
    assert design.single_repeat and design.groups == ("single",)
    (found,) = design_issues(design)
    assert found.message.startswith("The biological repeat of this experiment is not confirmed")
    assert design_issues(confirm_all(design)) == []


def test_controls_are_recorded(tmp_path):
    design = default_design(repeats_of(tree(tmp_path / "exp", PAIRED)))
    design = design.with_control("control", True)
    assert design.controls == ("control",)
    assert all(row.control for row in design.rows if row.condition == "control")
    assert design.with_control("control", False).controls == ()


# Reruns ------------------------------------------------------------------------------------


def test_unchanged_folders_keep_the_answers(tmp_path):
    root = tree(tmp_path / "exp", PAIRED)
    design = confirm_all(default_design(repeats_of(root)).with_control("control", True))
    # Images added to a confirmed folder keep the confirmation and its counts.
    make_tree(root, ["control/rep1/t1/img_99.tif"])
    again, changes = reconcile_design(design, repeats_of(root))
    assert again == design and not changes.any
    assert again.row(("control", "rep1")).image_count == 3


def test_a_new_repeat_asks_only_for_its_group(tmp_path):
    root = tree(tmp_path / "exp", PAIRED)
    design = confirm_all(default_design(repeats_of(root)).with_control("treated", True))
    make_tree(root, ["treated/rep2/img_00.tif", "treated/rep3/img_00.tif"])
    again, changes = reconcile_design(design, repeats_of(root))
    assert changes.added == (("treated", "rep2"), ("treated", "rep3"))
    assert changes.reopened == ("rep2",)
    assert again.unconfirmed_groups == ("rep2", "rep3")
    assert again.group_confirmed("rep1") and again.group_confirmed("rep2_day2")
    # A new folder of a control condition is a control too.
    assert again.row(("treated", "rep3")).control


def test_removed_folders_are_dropped(tmp_path):
    root = tree(tmp_path / "exp", PAIRED)
    design = confirm_all(default_design(repeats_of(root)))
    again, changes = reconcile_design(
        design, repeats_of(root, excluded=["control/rep2/img_00.tif", "control/rep2/img_01.tif"])
    )
    assert changes.removed == (("control", "rep2"),) and changes.added == ()
    assert again.confirmed and again.groups == ("rep1", "rep2_day2")


def test_manual_pairing_survives_a_rerun(tmp_path):
    root = tree(tmp_path / "exp", PAIRED)
    design = default_design(repeats_of(root)).with_group(("treated", "rep2_day2"), "rep2")
    path = write_experiment_design(confirm_all(design), tmp_path / DESIGN_FILENAME)
    again, _ = reconcile_design(read_experiment_design(path), repeats_of(root))
    assert again.repeat_groups()[("treated", "rep2_day2")] == "rep2" and again.confirmed


# The file ----------------------------------------------------------------------------------


def test_file_round_trip_is_byte_identical(tmp_path):
    # Labels a table reader could mistake for other values stay text ("NA" and "null"
    # themselves are refused by discovery since decision D-e).
    root = tree(tmp_path / "exp", {"FALSE/001/t1": 1, "FALSE/TRUE": 2})
    design = confirm_all(default_design(repeats_of(root)).with_control("FALSE", True))
    path = write_experiment_design(design, tmp_path / DESIGN_FILENAME)
    data = path.read_bytes()
    assert b"\r\n" not in data
    assert data.decode("utf-8").splitlines() == [
        "condition,biological_repeat,repeat_group,control,image_count,technical_repeat_count,"
        "confirmed,confirmed_on,schema_version",
        "FALSE,001,001,true,1,1,true,2026-10-26,1",
        "FALSE,TRUE,TRUE,true,2,0,true,2026-10-26,1",
    ]
    read = read_experiment_design(path)
    assert read == design
    assert write_experiment_design(read, tmp_path / "again.csv").read_bytes() == data


def test_files_edited_in_a_spreadsheet_still_read(tmp_path):
    path = tmp_path / DESIGN_FILENAME
    text = (
        "﻿condition,biological_repeat,repeat_group,control,image_count,"
        "technical_repeat_count,confirmed,confirmed_on,schema_version\r\n"
        "ctrl,rep1,rep1,TRUE,2,0,Yes,,1\r\n"
        "drug,rep1b,rep1,FALSE,2,0,1,2026-10-25,1\r\n"
        ",,,,,,,,\r\n"
    )
    path.write_bytes(text.encode("utf-8"))
    design = read_experiment_design(path)
    assert design.repeat_groups() == {("ctrl", "rep1"): "rep1", ("drug", "rep1b"): "rep1"}
    assert design.controls == ("ctrl",) and design.confirmed
    # A confirmation without a date gets one when it is used.
    assert design.dated(TODAY).row(("ctrl", "rep1")).confirmed_on == TODAY
    assert design.dated(TODAY).row(("drug", "rep1b")).confirmed_on == "2026-10-25"


# Typed edits (owner decision, 31 October 2026) --------------------------------------------


@pytest.mark.parametrize("group", ["", "NA", "na", "NaN", "none", "NULL", "n/a", "<NA>", "<na>"])
def test_a_repeat_group_that_reads_as_missing_is_refused(tmp_path, group):
    path = tmp_path / DESIGN_FILENAME
    path.write_bytes((HEADER + f"ctrl,rep1,{group},false,1,0,true,,1\n").encode("utf-8"))
    with pytest.raises(DesignFormatError, match=r"line 2, column repeat_group: .*missing"):
        read_experiment_design(path)


@pytest.mark.parametrize("group", [" rep1", "rep1 ", "\trep1"])
def test_a_repeat_group_with_outer_whitespace_is_refused(tmp_path, group):
    path = tmp_path / DESIGN_FILENAME
    path.write_bytes((HEADER + f'ctrl,rep1,"{group}",false,1,0,true,,1\n').encode("utf-8"))
    with pytest.raises(DesignFormatError, match="line 2, column repeat_group: .*whitespace"):
        read_experiment_design(path)


def test_a_confirmation_date_in_another_form_is_refused(tmp_path):
    path = tmp_path / DESIGN_FILENAME
    path.write_bytes((HEADER + "ctrl,rep1,rep1,false,1,0,true,26/10/2026,1\n").encode("utf-8"))
    with pytest.raises(DesignFormatError) as caught:
        read_experiment_design(path)
    assert str(caught.value) == (
        "experiment_design.csv, line 2, column confirmed_on: '26/10/2026' is not a date in the "
        "form YYYY-MM-DD (for example 2026-10-26)."
    )


@pytest.mark.parametrize("on", ["2026-10-1", "20261026", "2026-02-30", "2026/10/26", "today"])
def test_only_iso_dates_are_confirmation_dates(tmp_path, on):
    path = tmp_path / DESIGN_FILENAME
    path.write_bytes((HEADER + f"ctrl,rep1,rep1,false,1,0,true,{on},1\n").encode("utf-8"))
    with pytest.raises(DesignFormatError, match="column confirmed_on: .*YYYY-MM-DD"):
        read_experiment_design(path)


def test_repeat_groups_differing_only_in_case_are_refused(tmp_path):
    # Decision D-e: a name is spelled one way; Rep1 and rep1 are not two repeat groups.
    path = tmp_path / DESIGN_FILENAME
    path.write_bytes(
        (HEADER + "ctrl,rep1,Rep1,false,1,0,true,,1\ndrug,rep1,rep1,false,1,0,true,,1\n").encode()
    )
    with pytest.raises(DesignFormatError) as caught:
        read_experiment_design(path)
    assert str(caught.value).startswith(
        "experiment_design.csv, line 3, column repeat_group: 'rep1' differs only in case from "
        "'Rep1' on line 2."
    )
    root = tree(tmp_path / "exp", {"control/rep1": 1, "treated/rep1": 1})
    clash = default_design(repeats_of(root)).with_group(("treated", "rep1"), "REP1")
    assert "invalid_experiment_design" in codes(design_issues(clash))
    with pytest.raises(DesignFormatError, match="only in case"):
        write_experiment_design(clash, tmp_path / "out.csv")


def test_the_writer_writes_only_iso_dates_and_valid_groups(tmp_path):
    root = tree(tmp_path / "exp", {"control/rep1": 1, "treated/rep1": 1})
    design = default_design(repeats_of(root))
    path = tmp_path / DESIGN_FILENAME
    for bad in (
        design.with_confirmed("rep1", True, "26/10/2026"),
        design.with_group(("treated", "rep1"), "NA"),
        design.with_group(("treated", "rep1"), " rep2"),
    ):
        assert "invalid_experiment_design" in codes(design_issues(bad))
        with pytest.raises(DesignFormatError, match="cannot be written"):
            write_experiment_design(bad, path)
        assert not path.exists()
    dated = confirm_all(design).dated("2026-10-31")
    assert read_experiment_design(write_experiment_design(dated, path)) == dated


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("ctrl,rep1,rep1,maybe,1,0,true,,1\n", "control must be true or false"),
        ("ctrl,rep1,rep1,false,many,0,true,,1\n", "image_count must be a whole number"),
        ("ctrl,rep1,rep1,false,1,0,true,,2\n", "schema_version '2' is not supported"),
        ("ctrl,rep1,rep1,false,1,0,true,,1\nctrl,rep1,rep1,false,1,0,true,,1\n", "more than once"),
        ("ctrl,rep1,rep1,true,1,0,true,,1\nctrl,rep2,rep2,false,1,0,true,,1\n", "control must be"),
        (",rep1,rep1,false,1,0,true,,1\n", "must not be empty"),
    ],
)
def test_invalid_files_are_refused(tmp_path, body, message):
    path = tmp_path / DESIGN_FILENAME
    path.write_bytes((HEADER + body).encode("utf-8"))
    with pytest.raises(DesignFormatError, match=message):
        read_experiment_design(path)


def test_files_without_the_columns_are_refused(tmp_path):
    path = tmp_path / DESIGN_FILENAME
    path.write_bytes(b"condition,biological_repeat\nctrl,rep1\n")
    with pytest.raises(DesignFormatError, match="repeat_group, control"):
        read_experiment_design(path)
    with pytest.raises(DesignFormatError, match="cannot be read"):
        read_experiment_design(tmp_path / "missing.csv")


# The run -----------------------------------------------------------------------------------


PAIRED_DESCRIPTION = """schema = "morphobricks-experiment"
schema_version = 1

[conditions.ctrl]
source_folder = "control"

[conditions.treated]
source_folder = "treated"
reference = "ctrl"

[channels.ch1]

[repeats.rep1]
confirmed = true
confirmed_on = "2026-10-26"

[repeats.rep1.members.ctrl]
source_repeat = "rep1"
image_count = 1

[repeats.rep1.members.ctrl.technical_repeats.single]
source_folder = "single"
channels = ["ch1"]

[repeats.rep1.members.treated]
source_repeat = "rep1b"
image_count = 1

[repeats.rep1.members.treated.technical_repeats.single]
source_folder = "single"
channels = ["ch1"]

[used_ids]
conditions = ["ctrl", "treated"]
channels = ["ch1"]
repeats = ["rep1"]
technical_repeats = ["single"]
"""


def test_the_run_derives_the_design_from_the_description_and_records_its_hash(tmp_path):
    # v0.5.1: experiment_design.csv is an output only, derived from the published description.
    root = tree(tmp_path / "exp", {"control/rep1": 1, "treated/rep1b": 1})
    plain = run_described(root, prefill_mapping("YX"), output_folder=tmp_path / "plain")
    assert "repeat_missing_conditions" in codes(plain.issues)  # each folder its own repeat
    output = default_output_folder(root)
    output.mkdir(parents=True)
    (output / "morphobricks.toml").write_text(PAIRED_DESCRIPTION, encoding="utf-8")
    result = run_experiment(root, prefill_mapping("YX"))
    assert result.written
    # Paired by the description, the repeat is found in every condition.
    assert "repeat_missing_conditions" not in codes(result.issues)
    design = read_experiment_design(result.design_path)
    assert design.repeat_groups() == {("control", "rep1"): "rep1", ("treated", "rep1b"): "rep1"}
    assert design.controls == ("control",) and design.confirmed
    manifest = read_manifest(result.manifest_path)
    assert manifest.experiment_design_sha256 == file_sha256(result.design_path)
    # The table does not depend on the design.
    assert result.csv_path.read_bytes() == plain.csv_path.read_bytes()


# Command line ------------------------------------------------------------------------------


# load_design (owner decision of 31 October 2026, plan §12 question 43) ------------------


def test_load_design_without_a_file_gives_the_default_design(tmp_path):
    root = tree(tmp_path / "exp", {"control/rep1": 1, "treated/rep1": 1})
    design, changes = load_design(tmp_path / "out", repeats_of(root))
    assert design == default_design(repeats_of(root))
    assert not changes.any
    assert not (tmp_path / "out").exists()


def test_load_design_reads_and_reconciles_a_saved_design(tmp_path):
    root = tree(tmp_path / "exp", {"control/rep1": 1, "treated/rep1": 1})
    saved = confirm_all(default_design(repeats_of(root))).with_control("control", True)
    write_experiment_design(saved, tmp_path / "out" / DESIGN_FILENAME)
    make_tree(root, ["treated/rep2/img_00.tif"])
    design, changes = load_design(tmp_path / "out", repeats_of(root))
    assert design.group_confirmed("rep1") and design.controls == ("control",)
    assert changes.added == (("treated", "rep2"),)


@pytest.mark.parametrize(
    ("edit", "where"),
    [
        (lambda text: text.replace(TODAY, "26/10/2026"), "line 2, column confirmed_on"),
        (lambda text: text.replace("rep1,rep1,", "rep1,NA,", 1), "line 2, column repeat_group"),
        (lambda text: "not a design", "column(s)"),
    ],
)
def test_load_design_raises_for_an_unreadable_file(tmp_path, edit, where):
    # Owner decision of 31 October 2026: no default design and no problem string; the
    # refusal names the line and column, and the file is left as it is.
    root = tree(tmp_path / "exp", {"control/rep1": 1, "treated/rep1": 1})
    path = tmp_path / "out" / DESIGN_FILENAME
    write_experiment_design(confirm_all(default_design(repeats_of(root))), path)
    path.write_text(edit(path.read_text(encoding="utf-8")), encoding="utf-8")
    unreadable = path.read_bytes()
    with pytest.raises(DesignCheckError) as caught:
        load_design(tmp_path / "out", repeats_of(root))
    (issue,) = caught.value.issues
    assert issue.code == "unreadable_experiment_design"
    assert issue.is_error
    assert issue.message.startswith("The experiment design cannot be read: ")
    assert where in issue.message
    assert issue.paths == (str(path),)
    assert issue.action
    assert isinstance(caught.value.__cause__, DesignFormatError)
    assert isinstance(caught.value, ValueError)
    assert path.read_bytes() == unreadable


def test_load_design_raises_when_the_design_path_is_a_folder(tmp_path):
    root = tree(tmp_path / "exp", {"control/rep1": 1, "treated/rep1": 1})
    (tmp_path / "out" / DESIGN_FILENAME).mkdir(parents=True)
    with pytest.raises(DesignCheckError, match="unreadable_experiment_design"):
        load_design(tmp_path / "out", repeats_of(root))


def test_preflight_repeats_feed_the_design(tmp_path):
    root = tree(tmp_path / "exp", PAIRED)
    assert preflight(root).dataset.repeats == tuple(repeats_of(root))
