"""The shared strict reader of experiment_design.csv (contract §16, plan question 40)."""

import hashlib
import re

import pytest

from morphobricks_preprocessing import (
    DESIGN_FILENAME,
    ConfirmedDesign,
    DesignCheckError,
    biological_repeats,
    default_design,
    default_output_folder,
    discover_experiment,
    read_confirmed_design,
    read_experiment_design,
    read_manifest,
    run_experiment,
    write_experiment_design,
)
from morphobricks_preprocessing.axes import prefill_mapping
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.manifest import file_sha256
from tiff_tree import make_tree

TODAY = "2026-10-31"
HEADER = (
    "condition,biological_repeat,repeat_group,control,image_count,technical_repeat_count,"
    "confirmed,confirmed_on,schema_version\n"
)
GOOD = "ctrl,rep1,rep1,true,2,0,true,2026-10-31,1\ndrug,rep1b,rep1,false,2,1,true,2026-10-31,1\n"


def tree(root, counts):
    make_tree(root, [f"{folder}/img_{i:02d}.tif" for folder, n in counts.items() for i in range(n)])
    return root


def confirm_all(design):
    for group in design.groups:
        design = design.with_confirmed(group, True, TODAY)
    return design


def write(tmp_path, text):
    path = tmp_path / DESIGN_FILENAME
    path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
    return path


def refusal(path, **kwargs):
    with pytest.raises(DesignCheckError) as caught:
        read_confirmed_design(path, **kwargs)
    (issue,) = caught.value.issues
    assert issue.is_error and issue.paths == (str(path),)
    assert issue.code in str(caught.value)
    return issue


# What both bricks need ---------------------------------------------------------------------


def test_a_design_written_by_a_run_reads_with_everything_both_bricks_need(tmp_path):
    root = tree(tmp_path / "exp", {"control/rep1": 1, "treated/rep1b": 1, "treated/rep2": 1})
    repeats = biological_repeats(discover_experiment(root))
    design = default_design(repeats).with_group(("treated", "rep1b"), "rep1")
    design = confirm_all(design).with_control("control", True)
    result = run_experiment(root, prefill_mapping("YX"), design=design)
    assert result.written
    recorded = read_manifest(result.manifest_path).experiment_design_sha256

    read = read_confirmed_design(result.design_path, expected_sha256=recorded)
    assert isinstance(read, ConfirmedDesign)
    assert read.path == result.design_path and read.design == design
    assert read.sha256 == recorded == file_sha256(result.design_path)
    assert dict(read.repeat_groups) == {
        ("control", "rep1"): "rep1",
        ("treated", "rep1b"): "rep1",
        ("treated", "rep2"): "rep2",
    }
    assert {g: dict(m) for g, m in read.group_members().items()} == {
        "rep1": {"control": "rep1", "treated": "rep1b"},
        "rep2": {"treated": "rep2"},
    }
    assert read.conditions == ("control", "treated") and read.controls == ("control",)
    assert read.groups == ("rep1", "rep2")
    with pytest.raises(TypeError):
        read.repeat_groups[("control", "rep1")] = "other"  # read-only


def test_a_spreadsheet_edit_is_read_strictly_once_the_run_has_written_it(tmp_path, capsys):
    # The lenient reader takes the user's own edits; the run writes them back as the brick
    # writes every design, and that file is what other bricks read.
    root = tree(tmp_path / "exp", {"control/rep1": 1, "treated/rep1b": 1})
    output = default_output_folder(root)
    output.mkdir(parents=True)
    edited = write(
        output,
        "﻿" + HEADER.replace("\n", "\r\n") + "control ,rep1,rep1,TRUE,1,0,Yes,2026-10-26,1\r\n"
        "treated,rep1b,rep1,FALSE,1,0,1,,1\r\n",
    )
    with pytest.raises(DesignCheckError, match="byte-order mark"):
        read_confirmed_design(edited)
    assert main(["run", str(root), "--axes", "YX"]) == 0
    capsys.readouterr()
    read = read_confirmed_design(
        edited, expected_sha256=read_manifest(output / "run_manifest.json").experiment_design_sha256
    )
    assert dict(read.repeat_groups) == {("control", "rep1"): "rep1", ("treated", "rep1b"): "rep1"}
    # A confirmation without a date got the run's date.
    assert read.design.row(("treated", "rep1b")).confirmed_on != ""
    assert read.design.row(("control", "rep1")).confirmed_on == "2026-10-26"


def test_the_hash_is_of_the_bytes_read(tmp_path):
    path = write(tmp_path, HEADER + GOOD)
    read = read_confirmed_design(path)
    assert read.sha256 == hashlib.sha256((HEADER + GOOD).encode()).hexdigest()
    # Windows line endings are the same rows (the hash differs, as the bytes do).
    crlf = write(tmp_path, (HEADER + GOOD).replace("\n", "\r\n"))
    assert read_confirmed_design(crlf).repeat_groups == read.repeat_groups


# Refusals ----------------------------------------------------------------------------------


def test_a_missing_file_is_refused(tmp_path):
    assert refusal(tmp_path / DESIGN_FILENAME).code == "missing_experiment_design"
    assert refusal(tmp_path).code == "missing_experiment_design"  # a folder


@pytest.mark.parametrize(
    ("text", "message"),
    [
        # Structure
        (b"\xef\xbb\xbf" + (HEADER + GOOD).encode(), "byte-order mark"),
        ((HEADER + GOOD).encode("utf-16"), "byte-order mark|not UTF-8"),
        ("caf\xe9".encode("latin-1"), "not UTF-8"),
        ("", "must have the columns"),
        (HEADER.replace("repeat_group,control", "control,repeat_group") + GOOD, "in this order"),
        (HEADER.replace("\n", ",notes\n") + GOOD, "in this order"),
        (HEADER.replace("schema_version", "condition") + GOOD, "in this order"),
        (HEADER + "ctrl,rep1,rep1,true,2,0,true,2026-10-31\n", "8 values for 9 columns"),
        (HEADER + "ctrl,rep1,rep1,true,2,0,true,2026-10-31,1,x\n", "10 values for 9 columns"),
        (HEADER + '"ctrl,rep1,rep1,true,2,0,true,2026-10-31,1\n', "well-formed CSV"),
        (HEADER + GOOD + "\n", "0 values for 9 columns"),
        (HEADER, "lists no folder"),
        # Values: nothing is trimmed, converted or filled in
        (HEADER + "ctrl ,rep1,rep1,true,2,0,true,,1\n", "whitespace"),
        (HEADER + "ctrl,rep1, rep1,true,2,0,true,,1\n", "whitespace"),
        (HEADER + "ctrl,rep1,rep1,TRUE,2,0,true,,1\n", "control must be true or false"),
        (HEADER + "ctrl,rep1,rep1,true,2,0,yes,,1\n", "confirmed must be true or false"),
        (HEADER + "ctrl,rep1,rep1,,2,0,true,,1\n", "control must be true or false"),
        (HEADER + "ctrl,rep1,,true,2,0,true,,1\n", "column repeat_group: repeat_group '' reads"),
        (HEADER + "ctrl,rep1,n/a,true,2,0,true,,1\n", "column repeat_group: repeat_group 'n/a'"),
        (
            HEADER + "ctrl,rep1,rep1,true,2,0,true,26/10/2026,1\n",
            "line 2, column confirmed_on: '26/10/2026' is not a date in the form YYYY-MM-DD",
        ),
        (HEADER + "ctrl,rep1,rep1,true,2,0,true,2026-10-1,1\n", "column confirmed_on"),
        (HEADER + "ctrl,rep1,rep1,true,,0,true,,1\n", "image_count must be a whole number"),
        (HEADER + "ctrl,rep1,rep1,true,+2,0,true,,1\n", "image_count must be a whole number"),
        (HEADER + "ctrl,rep1,rep1,true,2,-1,true,,1\n", "technical_repeat_count must be"),
        (HEADER + "ctrl,rep1,rep1,true,2,0,true,,2\n", "schema_version '2'"),
        (HEADER + "NA,rep1,rep1,true,2,0,true,,1\n", "condition 'NA' reads as a missing"),
        (HEADER + "ctrl,,rep1,true,2,0,true,,1\n", "biological_repeat '' reads as a missing"),
        # Rows
        (HEADER + GOOD + GOOD.splitlines()[0] + "\n", "more than once"),
        (HEADER + "ctrl,rep1,rep1,true,2,0,true,,1\nctrl,rep2,rep2,false,2,0,true,,1\n", "control"),
        (HEADER + "ctrl,rep1,rep1,true,2,0,true,,1\nCtrl,rep2,rep2,true,2,0,true,,1\n", "case"),
        (HEADER + "a,Rep1,g1,true,2,0,true,,1\nb,rep1,g2,true,2,0,true,,1\n", "case"),
    ],
)
def test_anything_doubtful_is_refused(tmp_path, text, message):
    issue = refusal(write(tmp_path, text))
    assert issue.code == "unreadable_experiment_design"
    assert re.search(message, issue.message), issue.message


def test_the_lenient_reader_takes_what_the_shared_reader_refuses(tmp_path):
    # The copies in CellPatcher and MorphoFeature used the lenient reader: spreadsheet
    # spellings of booleans and spaces around labels were read.
    path = write(tmp_path, HEADER + "ctrl ,rep1,rep1,TRUE,2,0,yes,,1\n")
    assert read_experiment_design(path).repeat_groups() == {("ctrl", "rep1"): "rep1"}
    assert refusal(path).code == "unreadable_experiment_design"
    # A duplicated column: the lenient reader silently kept the last one.
    path = write(
        tmp_path,
        HEADER.replace("\n", ",repeat_group\n") + "ctrl,rep1,rep1,true,2,0,true,,1,other\n",
    )
    assert read_experiment_design(path).repeat_groups() == {("ctrl", "rep1"): "other"}
    assert refusal(path).code == "unreadable_experiment_design"


def test_an_unconfirmed_group_is_refused(tmp_path):
    path = write(tmp_path, HEADER + "ctrl,rep1,rep1,true,2,0,false,,1\n")
    with pytest.raises(DesignCheckError) as caught:
        read_confirmed_design(path)
    # design_issues names the folders of the group, as in the preprocessing run.
    (issue,) = caught.value.issues
    assert issue.code == "design_not_confirmed" and issue.paths == ("ctrl/rep1",)


def test_a_group_with_two_folders_of_a_condition_is_refused(tmp_path):
    path = write(
        tmp_path,
        HEADER + "ctrl,rep1,g,true,2,0,true,,1\nctrl,rep2,g,true,2,0,true,,1\n",
    )
    with pytest.raises(DesignCheckError) as caught:
        read_confirmed_design(path)
    assert "repeat_group_conflict" in [issue.code for issue in caught.value.issues]


def test_a_design_other_than_the_runs_is_refused(tmp_path):
    path = write(tmp_path, HEADER + GOOD)
    assert refusal(path, expected_sha256="0" * 64).code == "changed_experiment_design"
    assert read_confirmed_design(path, expected_sha256=file_sha256(path)).sha256 == file_sha256(
        path
    )


def test_every_design_the_brick_writes_is_read(tmp_path):
    root = tree(tmp_path / "exp", {"FALSE/001/t1": 1, "FALSE/TRUE": 2, "single": 1})
    design = confirm_all(
        default_design(biological_repeats(discover_experiment(root))).with_control("FALSE", True)
    )
    path = write_experiment_design(design, tmp_path / DESIGN_FILENAME)
    assert read_confirmed_design(path).design == design
