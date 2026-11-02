"""PP-8c item 6: the experiment design from the folders, earlier and from the command line.

- ``check`` writes the prefilled ``experiment_design.csv`` when the output folder holds
  none (only ``run`` did); it never replaces a design, also one the user edited.
- ``design --output OUT`` prints the design and what is still to confirm, and changes it
  without editing the file (``--confirm``, ``--confirm-all``, ``--control``,
  ``--no-control``, ``--repeat-group``), under the lock, with the run's reader and
  validator. Nothing is confirmed by default; anything unknown is refused, nothing changed.
- ``edit_design`` / ``DesignEdit`` do the same in Python.
"""

import numpy as np
import pytest

from morphobricks_preprocessing import read_experiment_design
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.run.lock import output_lock
from tiff_tree import write_data

DESIGN = "experiment_design.csv"


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    rels = ["ctrl/day1/a.tif", "ctrl/day2/a.tif", "drug/day1/a.tif", "drug/d2/a.tif"]
    for seed, rel in enumerate(rels):
        data = np.random.default_rng(seed).integers(0, 200, (6, 8), dtype=np.uint8)
        write_data(root / rel, data, "YX")
    return root


@pytest.fixture
def checked(experiment, tmp_path):
    output = tmp_path / "out"
    assert main(["check", str(experiment), "--output", str(output)]) == 0
    return output


def design(output):
    return read_experiment_design(output / DESIGN)


def test_check_writes_the_prefilled_template(checked):
    found = design(checked)
    assert [(r.condition, r.biological_repeat, r.repeat_group) for r in found.rows] == [
        ("ctrl", "day1", "day1"),
        ("ctrl", "day2", "day2"),
        ("drug", "d2", "d2"),
        ("drug", "day1", "day1"),
    ]
    assert not any(r.confirmed or r.control for r in found.rows)
    assert [r.image_count for r in found.rows] == [1, 1, 1, 1]


def test_check_never_replaces_a_design(experiment, checked):
    path = checked / DESIGN
    edited = path.read_text(encoding="utf-8").replace("ctrl,day1,day1,false", "ctrl,day1,day1,true")
    edited = edited.replace("ctrl,day2,day2,false", "ctrl,day2,day2,true")
    path.write_text(edited, encoding="utf-8")
    assert main(["check", str(experiment), "--output", str(checked)]) == 0
    assert path.read_text(encoding="utf-8") == edited


def test_design_prints_the_table_and_what_is_unconfirmed(checked, capsys):
    before = (checked / DESIGN).read_bytes()
    assert main(["design", "--output", str(checked)]) == 0
    out = capsys.readouterr().out
    assert "condition  biological repeat  repeat group  control" in out
    assert "Not confirmed yet: d2, day1, day2" in out
    assert (checked / DESIGN).read_bytes() == before  # nothing confirmed by default


def test_design_pairs_marks_and_confirms(checked, capsys):
    args = ["design", "--output", str(checked), "--repeat-group", "drug/d2=day2"]
    assert main([*args, "--control", "ctrl", "--confirm", "day2"]) == 0
    found = design(checked)
    assert found.repeat_groups()[("drug", "d2")] == "day2"
    assert found.controls == ("ctrl",)
    assert found.group_confirmed("day2") and not found.group_confirmed("day1")
    assert all(len(r.confirmed_on) == 10 for r in found.members("day2"))
    assert "Not confirmed yet: day1" in capsys.readouterr().out
    assert main(["design", "--output", str(checked), "--confirm-all"]) == 0
    assert design(checked).confirmed
    assert main(["design", "--output", str(checked), "--no-control", "ctrl"]) == 0
    assert design(checked).controls == ()


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--confirm", "day9"], "unknown repeat group 'day9'"),
        (["--control", "placebo"], "unknown condition 'placebo'"),
        (["--repeat-group", "ctrl/day9=day1"], "unknown folder ctrl/day9"),
        (["--repeat-group", "ctrl-day1"], "is not CONDITION/REPEAT=GROUP"),
        (["--control", "ctrl", "--no-control", "ctrl"], "cannot be marked a control and not"),
        (["--repeat-group", "drug/d2=NA"], "repeat_group"),
    ],
)
def test_design_refuses_unknown_names_and_changes_nothing(checked, capsys, args, message):
    before = (checked / DESIGN).read_bytes()
    assert main(["design", "--output", str(checked), *args]) == 1
    out = capsys.readouterr().out
    assert "The experiment design was not changed" in out and message in out
    assert (checked / DESIGN).read_bytes() == before


def test_design_refuses_to_confirm_a_group_with_two_folders_of_a_condition(checked, capsys):
    args = ["design", "--output", str(checked), "--repeat-group", "ctrl/day2=day1"]
    assert main([*args, "--confirm", "day1"]) == 1
    out = capsys.readouterr().out
    assert "repeat group 'day1' cannot be confirmed" in out and "only one folder" in out
    assert not design(checked).group_confirmed("day1")


def test_design_without_a_design_file_says_how_to_get_one(tmp_path, capsys):
    (tmp_path / "out").mkdir()
    assert main(["design", "--output", str(tmp_path / "out")]) == 1
    assert "'check' or 'run' writes it" in capsys.readouterr().out


def test_design_is_refused_while_a_run_holds_the_lock(checked, capsys):
    with output_lock(checked):
        assert main(["design", "--output", str(checked), "--confirm-all"]) == 1
    assert "Another run is using this output folder" in capsys.readouterr().out
    assert not design(checked).confirmed


def test_a_design_confirmed_with_the_command_lets_the_run_go(experiment, checked):
    assert main(["design", "--output", str(checked), "--confirm-all"]) == 0
    assert main(["run", str(experiment), "--output", str(checked), "--axes", "YX"]) == 0


def test_the_python_interface(checked):
    from morphobricks_preprocessing import DesignEdit, DesignEditError, edit_design

    changed, path = edit_design(checked, DesignEdit(confirm=("day1",)), on="2026-11-02")
    assert path == (checked / DESIGN).resolve()
    assert changed.confirmed_on("day1") == "2026-11-02"
    assert edit_design(checked, DesignEdit(confirm=("day1",)))[1] is None  # nothing changed
    with pytest.raises(DesignEditError, match="unknown repeat group"):
        edit_design(checked, DesignEdit(confirm=("nope",)))
