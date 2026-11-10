"""The rows of experiment_design.csv: the run's check, the writer and the strict parser.

The file is an output only (v0.5.1, Spec ADR 0002 AR11 (3)): the run derives it from the
published description and checks it against the folders found (:func:`reconcile_design`,
:func:`design_issues`); :func:`parse_design_bytes` is the strict parser of a file the writer wrote
(no brick reads the file as an input). The editor model, the default pairing, the lenient
reader, ``load_design`` and the strict reader for other bricks were removed (SWEEP-PP).
"""

from dataclasses import replace

import pytest

from described_tree import run_described
from morphobricks_preprocessing import (
    DESIGN_FILENAME,
    DesignFormatError,
    DesignRow,
    DesignTable,
    biological_repeats,
    default_output_folder,
    design_issues,
    discover_experiment,
    parse_design_bytes,
    preflight,
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


def table(root, groups=None, *, confirmed=True, controls=()):
    """The rows a description of ``root`` derives: each folder its own repeat unless moved."""
    groups = groups or {}
    return DesignTable(
        tuple(
            DesignRow(
                r.condition,
                r.biological_repeat,
                groups.get(r.key, r.biological_repeat),
                control=r.condition in controls,
                image_count=r.image_count,
                technical_repeat_count=r.technical_repeat_count,
                confirmed=confirmed,
                confirmed_on=TODAY if confirmed else "",
            )
            for r in repeats_of(root)
        )
    )


PAIRED = {
    "control/rep1/t1": 2,
    "control/rep1/t2": 1,
    "control/rep2": 2,
    "treated/rep1": 2,
    "treated/rep2_day2": 2,
}


# What the rows say -----------------------------------------------------------------------


def test_the_rows_answer_groups_controls_and_members(tmp_path):
    design = table(tree(tmp_path / "exp", PAIRED), controls=("control",))
    assert design.groups == ("rep1", "rep2", "rep2_day2")
    assert design.repeat_groups() == {
        ("control", "rep1"): "rep1",
        ("control", "rep2"): "rep2",
        ("treated", "rep1"): "rep1",
        ("treated", "rep2_day2"): "rep2_day2",
    }
    row = design.row(("control", "rep1"))
    assert (row.image_count, row.technical_repeat_count) == (3, 2)
    assert design.confirmed and design.controls == ("control",) and not design.single_repeat
    assert [r.key for r in design.members("rep1")] == [("control", "rep1"), ("treated", "rep1")]


def test_a_group_cannot_hold_two_folders_of_a_condition(tmp_path):
    clash = table(tree(tmp_path / "exp", PAIRED), {("control", "rep2"): "rep1"})
    assert clash.conflicts == {"rep1": ("control",)}
    assert not clash.confirmed
    found = design_issues(clash)
    assert codes(found) == ["repeat_group_conflict"]
    assert found[0].is_error and found[0].paths == ("control/rep1", "control/rep2", "treated/rep1")


def test_unconfirmed_groups_are_errors(tmp_path):
    root = tree(tmp_path / "exp", PAIRED)
    design = DesignTable(
        tuple(
            row if row.repeat_group == "rep1" else replace(row, confirmed=False, confirmed_on="")
            for row in table(root).rows
        )
    )
    (found,) = design_issues(design)
    assert found.code == "design_not_confirmed" and found.is_error
    assert "2 repeat groups (rep2, rep2_day2) are not confirmed" in found.message
    assert found.paths == ("control/rep2", "treated/rep2_day2")
    assert design_issues(table(root)) == []


REMOVED_FIXES = ("design command", "--confirm", "experiment_design.csv", "repeat_group column")


def test_the_design_refusals_name_the_fix_of_a_described_run(tmp_path):
    # Review of PP-C: the actions named the removed design command and editing
    # experiment_design.csv, which is now an output only (derived from the description).
    root = tree(tmp_path / "exp", PAIRED)
    clash = table(root, {("control", "rep2"): "rep1"})
    found = [*design_issues(table(root, confirmed=False)), *design_issues(clash)]
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
    root = tree(tmp_path / "exp", {"control": 2, "treated": 3})
    design = table(root, confirmed=False)
    assert design.single_repeat and design.groups == ("single",)
    (found,) = design_issues(design)
    assert found.message.startswith("The biological repeat of this experiment is not confirmed")
    assert design_issues(table(root)) == []


# The run's check against the folders found -----------------------------------------------


def test_unchanged_folders_keep_the_answers(tmp_path):
    root = tree(tmp_path / "exp", PAIRED)
    design = table(root, controls=("control",))
    # Images added to a confirmed folder keep the confirmation and its counts.
    make_tree(root, ["control/rep1/t1/img_99.tif"])
    again = reconcile_design(design, repeats_of(root))
    assert again == design
    assert again.row(("control", "rep1")).image_count == 3


def test_a_new_repeat_asks_only_for_its_group(tmp_path):
    root = tree(tmp_path / "exp", PAIRED)
    design = table(root, controls=("treated",))
    make_tree(root, ["treated/rep2/img_00.tif", "treated/rep3/img_00.tif"])
    again = reconcile_design(design, repeats_of(root))
    assert again.unconfirmed_groups == ("rep2", "rep3")
    assert again.group_confirmed("rep1") and again.group_confirmed("rep2_day2")
    # A new folder of a control condition is a control too.
    assert again.row(("treated", "rep3")).control
    assert "design_not_confirmed" in codes(design_issues(again))


def test_removed_folders_are_dropped(tmp_path):
    root = tree(tmp_path / "exp", PAIRED)
    again = reconcile_design(
        table(root),
        repeats_of(root, excluded=["control/rep2/img_00.tif", "control/rep2/img_01.tif"]),
    )
    assert again.confirmed and again.groups == ("rep1", "rep2_day2")


def test_a_pairing_of_the_rows_is_kept(tmp_path):
    root = tree(tmp_path / "exp", PAIRED)
    design = table(root, {("treated", "rep2_day2"): "rep2"})
    again = reconcile_design(design, repeats_of(root))
    assert again.repeat_groups()[("treated", "rep2_day2")] == "rep2" and again.confirmed


# The file: written, and parsed strictly ---------------------------------------------------


def test_file_round_trip_is_byte_identical(tmp_path):
    # Labels a table reader could mistake for other values stay text ("NA" and "null"
    # themselves are refused by discovery since decision D-e).
    root = tree(tmp_path / "exp", {"FALSE/001/t1": 1, "FALSE/TRUE": 2})
    design = table(root, controls=("FALSE",))
    path = write_experiment_design(design, tmp_path / DESIGN_FILENAME)
    data = path.read_bytes()
    assert b"\r\n" not in data
    assert data.decode("utf-8").splitlines() == [
        "condition,biological_repeat,repeat_group,control,image_count,technical_repeat_count,"
        "confirmed,confirmed_on,schema_version",
        "FALSE,001,001,true,1,1,true,2026-10-26,1",
        "FALSE,TRUE,TRUE,true,2,0,true,2026-10-26,1",
    ]
    read = parse_design_bytes(data)
    assert read == design
    assert write_experiment_design(read, tmp_path / "again.csv").read_bytes() == data


@pytest.mark.parametrize(
    "text",
    [
        "\ufeff" + HEADER + "ctrl,rep1,rep1,true,2,0,true,,1\n",  # a byte-order mark
        HEADER.replace("\n", "\r\n") + "ctrl,rep1,rep1,TRUE,2,0,true,,1\r\n",  # TRUE
        HEADER + "ctrl,rep1,rep1,true,2,0,Yes,,1\n",
        HEADER + "ctrl,rep1,rep1,true,2,0,1,,1\n",
        HEADER + "ctrl,rep1,rep1,true,2,0,true,,1\n,,,,,,,,\n",  # an empty row
    ],
)
def test_a_file_saved_by_a_spreadsheet_is_refused(text):
    with pytest.raises(DesignFormatError):
        parse_design_bytes(text.encode("utf-8"))


@pytest.mark.parametrize("group", ["", "NA", "na", "NaN", "none", "NULL", "n/a", "<NA>", "<na>"])
def test_a_repeat_group_that_reads_as_missing_is_refused(group):
    data = (HEADER + f"ctrl,rep1,{group},false,1,0,true,,1\n").encode("utf-8")
    with pytest.raises(DesignFormatError, match=r"line 2, column repeat_group: .*missing"):
        parse_design_bytes(data)


@pytest.mark.parametrize("group", [" rep1", "rep1 ", "\trep1"])
def test_a_repeat_group_with_outer_whitespace_is_refused(group):
    data = (HEADER + f'ctrl,rep1,"{group}",false,1,0,true,,1\n').encode("utf-8")
    with pytest.raises(DesignFormatError, match="line 2: repeat_group .*whitespace"):
        parse_design_bytes(data)


def test_a_confirmation_date_in_another_form_is_refused():
    data = (HEADER + "ctrl,rep1,rep1,false,1,0,true,26/10/2026,1\n").encode("utf-8")
    with pytest.raises(DesignFormatError) as caught:
        parse_design_bytes(data)
    assert str(caught.value) == (
        "experiment_design.csv, line 2, column confirmed_on: '26/10/2026' is not a date in the "
        "form YYYY-MM-DD (for example 2026-10-26)."
    )


@pytest.mark.parametrize("on", ["2026-10-1", "20261026", "2026-02-30", "2026/10/26", "today"])
def test_only_iso_dates_are_confirmation_dates(on):
    data = (HEADER + f"ctrl,rep1,rep1,false,1,0,true,{on},1\n").encode("utf-8")
    with pytest.raises(DesignFormatError, match="column confirmed_on: .*YYYY-MM-DD"):
        parse_design_bytes(data)


def test_repeat_groups_differing_only_in_case_are_refused(tmp_path):
    # Decision D-e: a name is spelled one way; Rep1 and rep1 are not two repeat groups.
    data = (
        HEADER + "ctrl,rep1,Rep1,false,1,0,true,,1\ndrug,rep1,rep1,false,1,0,true,,1\n"
    ).encode()
    with pytest.raises(DesignFormatError) as caught:
        parse_design_bytes(data)
    assert str(caught.value).startswith(
        "experiment_design.csv, line 3, column repeat_group: 'rep1' differs only in case from "
        "'Rep1' on line 2."
    )
    root = tree(tmp_path / "exp", {"control/rep1": 1, "treated/rep1": 1})
    clash = table(root, {("treated", "rep1"): "REP1"})
    assert "invalid_experiment_design" in codes(design_issues(clash))
    with pytest.raises(DesignFormatError, match="only in case"):
        write_experiment_design(clash, tmp_path / "out.csv")


def test_the_writer_writes_only_iso_dates_and_valid_groups(tmp_path):
    root = tree(tmp_path / "exp", {"control/rep1": 1, "treated/rep1": 1})
    design = table(root)
    path = tmp_path / DESIGN_FILENAME
    for bad in (
        DesignTable(tuple(replace(r, confirmed_on="26/10/2026") for r in design.rows)),
        table(root, {("treated", "rep1"): "NA"}),
        table(root, {("treated", "rep1"): " rep2"}),
    ):
        assert "invalid_experiment_design" in codes(design_issues(bad))
        with pytest.raises(DesignFormatError, match="cannot be written"):
            write_experiment_design(bad, path)
        assert not path.exists()
    assert parse_design_bytes(write_experiment_design(design, path).read_bytes()) == design


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("ctrl,rep1,rep1,maybe,1,0,true,,1\n", "control must be true or false"),
        ("ctrl,rep1,rep1,false,many,0,true,,1\n", "image_count must be a whole number"),
        ("ctrl,rep1,rep1,false,1,0,true,,2\n", "schema_version '2' is not supported"),
        ("ctrl,rep1,rep1,false,1,0,true,,1\nctrl,rep1,rep1,false,1,0,true,,1\n", "more than once"),
        ("ctrl,rep1,rep1,true,1,0,true,,1\nctrl,rep2,rep2,false,1,0,true,,1\n", "control must be"),
        (",rep1,rep1,false,1,0,true,,1\n", "condition"),
    ],
)
def test_invalid_files_are_refused(body, message):
    with pytest.raises(DesignFormatError, match=message):
        parse_design_bytes((HEADER + body).encode("utf-8"))


def test_files_without_the_columns_are_refused():
    with pytest.raises(DesignFormatError, match="must have the columns"):
        parse_design_bytes(b"condition,biological_repeat\nctrl,rep1\n")
    with pytest.raises(DesignFormatError, match="lists no folder"):
        parse_design_bytes(HEADER.encode("utf-8"))


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
    design = parse_design_bytes(result.design_path.read_bytes())
    assert design.repeat_groups() == {("control", "rep1"): "rep1", ("treated", "rep1b"): "rep1"}
    assert design.controls == ("control",) and design.confirmed
    manifest = read_manifest(result.manifest_path)
    assert manifest.experiment_design_sha256 == file_sha256(result.design_path)
    # The table does not depend on the design.
    assert result.csv_path.read_bytes() == plain.csv_path.read_bytes()


def test_preflight_repeats_feed_the_design(tmp_path):
    root = tree(tmp_path / "exp", PAIRED)
    assert preflight(root).dataset.repeats == tuple(repeats_of(root))
