"""The derived experiment_design.csv as a file: the run's output, and the strict parser.

The file is an output only (v0.5.1, Spec ADR 0002 AR11 (3)): no brick reads it back for its
repeat groups (they come from the description through ``RunInputs``). Its golden here is parsed
in the test with the standard ``csv`` module; :func:`parse_design_bytes` is the strict parser of
the draft's experiment-v1 section 7 migration, which refuses anything doubtful. The strict
reader for other bricks (``read_confirmed_design``, ``ConfirmedDesign``, ``DesignCheckError``)
was removed in SWEEP-PP.
"""

import csv
import io

import pytest

from morphobricks_preprocessing import (
    DESIGN_FILENAME,
    DesignFormatError,
    DesignRow,
    DesignTable,
    default_output_folder,
    design_issues,
    parse_design_bytes,
    read_manifest,
    run_experiment,
    write_experiment_design,
)
from morphobricks_preprocessing.axes import prefill_mapping
from morphobricks_preprocessing.manifest import file_sha256
from tiff_tree import make_tree

HEADER = (
    "condition,biological_repeat,repeat_group,control,image_count,technical_repeat_count,"
    "confirmed,confirmed_on,schema_version\n"
)
GOOD = "ctrl,rep1,rep1,true,2,0,true,2026-10-31,1\ndrug,rep1b,rep1,false,2,1,true,2026-10-31,1\n"


def tree(root, counts):
    make_tree(root, [f"{folder}/img_{i:02d}.tif" for folder, n in counts.items() for i in range(n)])
    return root


# The run's output ---------------------------------------------------------------------------


def _unit(repeat, condition, folder):
    return (
        f"[repeats.{repeat}.members.{condition}]\nsource_repeat = {folder!r}\nimage_count = 1\n\n"
        f"[repeats.{repeat}.members.{condition}.technical_repeats.single]\n"
        'source_folder = "single"\nchannels = ["ch1"]\n\n'
    ).replace("'", '"')


PAIRED = (
    'schema = "morphobricks-experiment"\nschema_version = 1\n\n'
    '[conditions.ctrl]\nsource_folder = "control"\n\n'
    '[conditions.treated]\nsource_folder = "treated"\nreference = "ctrl"\n\n'
    "[channels.ch1]\n\n"
    '[repeats.rep1]\nconfirmed = true\nconfirmed_on = "2026-10-26"\n\n'
    + _unit("rep1", "ctrl", "rep1")
    + _unit("rep1", "treated", "rep1b")
    + '[repeats.rep2]\nconfirmed = true\nconfirmed_on = "2026-10-26"\n\n'
    + _unit("rep2", "treated", "rep2")
    + '[used_ids]\nconditions = ["ctrl", "treated"]\nchannels = ["ch1"]\n'
    'repeats = ["rep1", "rep2"]\ntechnical_repeats = ["single"]\n'
)


def test_the_run_writes_the_derived_file_and_records_its_hash(tmp_path):
    # The run derives the design from the published description (v0.5.1): the pairing and
    # the control are the description's. Parsed here with the csv module only.
    root = tree(tmp_path / "exp", {"control/rep1": 1, "treated/rep1b": 1, "treated/rep2": 1})
    output = default_output_folder(root)
    output.mkdir(parents=True)
    (output / "morphobricks.toml").write_text(PAIRED, encoding="utf-8")
    result = run_experiment(root, prefill_mapping("YX"))
    assert result.written, result.issues
    data = result.design_path.read_bytes()
    assert data == (
        HEADER + "control,rep1,rep1,true,1,0,true,2026-10-26,1\n"
        "treated,rep1b,rep1,false,1,0,true,2026-10-26,1\n"
        "treated,rep2,rep2,false,1,0,true,2026-10-26,1\n"
    ).encode("utf-8")
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
    assert {(r["condition"], r["biological_repeat"]): r["repeat_group"] for r in rows} == {
        ("control", "rep1"): "rep1",
        ("treated", "rep1b"): "rep1",
        ("treated", "rep2"): "rep2",
    }
    recorded = read_manifest(result.manifest_path).experiment_design_sha256
    assert recorded == file_sha256(result.design_path)


def test_every_design_the_brick_writes_is_parsed_back(tmp_path):
    rows = (
        DesignRow("FALSE", "001", "001", True, 1, 1, True, "2026-10-31"),
        DesignRow("FALSE", "TRUE", "TRUE", True, 2, 0, True, "2026-10-31"),
        DesignRow("single", "single", "single", False, 1, 0, True, "2026-10-31"),
    )
    path = write_experiment_design(DesignTable(rows), tmp_path / DESIGN_FILENAME)
    assert parse_design_bytes(path.read_bytes()) == DesignTable(rows)


# The strict parser: anything doubtful is refused ---------------------------------------------


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
        (HEADER.replace("schema_version", "condition") + GOOD, "column .condition. twice"),
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
        (
            HEADER + "a,r1,Rep1,true,2,0,true,,1\nb,r1,rep1,true,2,0,true,,1\n",
            "line 3, column repeat_group: 'rep1' differs only in case from 'Rep1' on line 2",
        ),
    ],
)
def test_anything_doubtful_is_refused(text, message):
    data = text.encode("utf-8") if isinstance(text, str) else text
    with pytest.raises(DesignFormatError, match=message):
        parse_design_bytes(data)


DUPLICATED = HEADER.replace("\n", ",repeat_group\n")


@pytest.mark.parametrize(
    "text, message",
    [
        # Issue #83: the lenient reader silently kept the last of two columns.
        (
            DUPLICATED + "ctrl,rep1,rep1,true,2,0,true,,1,other\n",
            r"names the column 'repeat_group' twice \(columns 3 and 10\)",
        ),
        # Equal values are refused too: the header itself is wrong.
        (DUPLICATED + "ctrl,rep1,rep1,true,2,0,true,,1,rep1\n", "'repeat_group' twice"),
        (
            HEADER.replace("\n", ", Control\n") + "ctrl,rep1,rep1,true,2,0,true,,1,false\n",
            r"'control' twice \(columns 4 and 10, the second written ' Control'\)",
        ),
        # Invisible characters do not make another column (review of #130).
        (
            HEADER.replace("\n", ",\ufeffcondition\n") + "ctrl,rep1,rep1,true,2,0,true,,1,x\n",
            r"'condition' twice \(columns 1 and 10",
        ),
        (
            HEADER.replace("\n", ",Condition\u200b\n") + "ctrl,rep1,rep1,true,2,0,true,,1,x\n",
            r"'condition' twice \(columns 1 and 10",
        ),
        (HEADER.replace("\n", ",\n") + "ctrl,rep1,rep1,true,2,0,true,,1,\n", "column 10 .*no name"),
        (
            HEADER.replace("\n", ",\ufeff\n") + "ctrl,rep1,rep1,true,2,0,true,,1,\n",
            "column 10 .*no name",
        ),
        (
            HEADER.replace("\n", ",  \n") + "ctrl,rep1,rep1,true,2,0,true,,1,\n",
            "column 10 .*no name",
        ),
    ],
)
def test_a_column_named_twice_or_not_at_all_is_refused(text, message):
    with pytest.raises(DesignFormatError, match=message):
        parse_design_bytes(text.encode("utf-8"))


def test_an_unconfirmed_group_is_parsed_and_refused_by_the_check():
    design = parse_design_bytes((HEADER + "ctrl,rep1,rep1,true,2,0,false,,1\n").encode())
    (issue,) = design_issues(design)
    assert issue.code == "design_not_confirmed" and issue.paths == ("ctrl/rep1",)


def test_a_group_with_two_folders_of_a_condition_is_refused_by_the_check():
    text = HEADER + "ctrl,rep1,g,true,2,0,true,,1\nctrl,rep2,g,true,2,0,true,,1\n"
    assert "repeat_group_conflict" in [
        i.code for i in design_issues(parse_design_bytes(text.encode()))
    ]
