"""The design writer refuses a value of the wrong type and a table its parser refuses (#410).

``DesignRow`` holds what a caller gives it. The writer used to turn ``control`` and
``confirmed`` into text by truthiness, so ``control="false"`` was written ``true`` (a condition
silently made a control), and it wrote counts and rows its own strict parser refuses (a negative
count, a folder twice, one condition both control and not). Now each value must have its exact
type (a bool, an int of at least 0, a text) and the bytes are parsed back with
:func:`parse_design_bytes` and must equal the table before the file is replaced; a refusal names
the folder, the column and the type found, and nothing is written.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from morphobricks_preprocessing import (
    DESIGN_FILENAME,
    DesignFormatError,
    DesignRow,
    DesignTable,
    design_issues,
    parse_design_bytes,
    write_experiment_design,
)

ON = "2026-11-23"
CTRL = DesignRow("ctrl", "rep1", "rep1", True, 2, 0, True, ON)
DRUG = DesignRow("drug", "rep1", "rep1", False, 3, 1, True, ON)
VALID = DesignTable((CTRL, DRUG))


def _with(**changes) -> DesignTable:
    return DesignTable((replace(CTRL, **changes), DRUG))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"control": "false"}, "control must be True or False, not 'false' (of type str)"),
        ({"control": 1}, "control must be True or False, not 1 (of type int)"),
        ({"control": 0}, "control must be True or False, not 0 (of type int)"),
        ({"control": None}, "control must be True or False, not None (of type NoneType)"),
        ({"confirmed": "false"}, "confirmed must be True or False, not 'false' (of type str)"),
        ({"confirmed": 1}, "confirmed must be True or False, not 1 (of type int)"),
        (
            {"image_count": True},
            "image_count must be a whole number of at least 0, not True (of type bool)",
        ),
        (
            {"image_count": 2.0},
            "image_count must be a whole number of at least 0, not 2.0 (of type float)",
        ),
        (
            {"image_count": "2"},
            "image_count must be a whole number of at least 0, not '2' (of type str)",
        ),
        ({"image_count": -3}, "image_count must be a whole number of at least 0, not -3"),
        (
            {"technical_repeat_count": -1},
            "technical_repeat_count must be a whole number of at least 0, not -1",
        ),
        ({"confirmed_on": None}, "confirmed_on must be a text, not None (of type NoneType)"),
        ({"repeat_group": 5}, "repeat_group must be a text, not 5 (of type int)"),
    ],
)
def test_a_value_of_the_wrong_type_is_refused_and_nothing_is_written(tmp_path, changes, message):
    path = tmp_path / DESIGN_FILENAME
    bad = _with(**changes)
    with pytest.raises(DesignFormatError) as caught:
        write_experiment_design(bad, path)
    assert str(caught.value) == f"The experiment design cannot be written: ctrl/rep1: {message}."
    assert not path.exists()
    issues = design_issues(bad)
    assert [i.code for i in issues] == ["invalid_experiment_design"]
    assert message in issues[0].message


def test_control_false_as_text_is_never_written_as_true(tmp_path):
    """The issue's case: ``"false"`` is truthy, so it used to be written ``true``."""
    path = tmp_path / DESIGN_FILENAME
    write_experiment_design(VALID, path)
    before = path.read_bytes()
    with pytest.raises(DesignFormatError, match=r"ctrl/rep1: control must be True or False"):
        write_experiment_design(_with(control="false"), path)
    assert path.read_bytes() == before  # the file is not replaced


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            (CTRL, replace(CTRL, image_count=5)),
            "experiment_design.csv lists a folder more than once: ctrl / rep1",
        ),
        (
            (CTRL, replace(CTRL, biological_repeat="rep2", repeat_group="rep2", control=False)),
            "experiment_design.csv: control must be the same in every row of condition ctrl.",
        ),
    ],
    ids=["folder-twice", "mixed-control"],
)
def test_a_table_its_own_parser_refuses_is_not_written(tmp_path, rows, message):
    path = tmp_path / DESIGN_FILENAME
    with pytest.raises(DesignFormatError) as caught:
        write_experiment_design(DesignTable(rows), path)
    assert str(caught.value) == f"The experiment design cannot be written: {message}"
    assert not path.exists()


def test_valid_values_are_written_unchanged_and_read_back_equal(tmp_path):
    path = write_experiment_design(VALID, tmp_path / DESIGN_FILENAME)
    assert path.read_text(encoding="utf-8").splitlines()[1:] == [
        f"ctrl,rep1,rep1,true,2,0,true,{ON},1",
        f"drug,rep1,rep1,false,3,1,true,{ON},1",
    ]
    assert parse_design_bytes(path.read_bytes()) == VALID
    assert design_issues(VALID) == []
    unconfirmed = DesignTable((replace(CTRL, confirmed=False, confirmed_on=""), DRUG))
    again = write_experiment_design(unconfirmed, tmp_path / "unconfirmed.csv")
    assert parse_design_bytes(again.read_bytes()) == unconfirmed  # still writable


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"control": np.True_}, "control must be True or False, not np.True_ (of type numpy.bool)"),
        (
            {"image_count": np.int64(2)},
            "image_count must be a whole number of at least 0, not np.int64(2) "
            "(of type numpy.int64)",
        ),
    ],
)
def test_a_numpy_value_is_named_with_its_module(tmp_path, changes, message):
    """A numpy bool's short name is ``bool``, which would read as the builtin (#470)."""
    bad = _with(**changes)
    with pytest.raises(DesignFormatError) as caught:
        write_experiment_design(bad, tmp_path / DESIGN_FILENAME)
    assert message in str(caught.value)
    assert message in design_issues(bad)[0].message


def test_a_type_error_gets_a_type_action_not_the_repeat_group_one():
    """#470: the action said to name repeat groups and dates for a wrong ``control``."""
    bad = DesignTable((replace(CTRL, control="false", image_count=2.0, confirmed_on=None), DRUG))
    (issue,) = design_issues(bad)
    assert issue.action == (
        "Give confirmed_on as text; control as True or False; "
        "image_count as a whole number of at least 0."
    )
    assert "repeat group" not in issue.action


def test_a_type_error_and_a_name_error_each_keep_their_own_action():
    bad = DesignTable((replace(CTRL, control="false"), replace(DRUG, repeat_group=" rep1")))
    (issue,) = design_issues(bad)
    assert issue.action.startswith("Give control as True or False. Give every repeat group a name")
