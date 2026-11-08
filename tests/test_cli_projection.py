"""``convert --project`` on the command line (issue #176).

The options, the refusals and the next commands.

The regression is ``test_convert_project_makes_copies_the_run_accepts``: argparse rejected
``--project`` before, and a Z-stack could only be refused.
"""

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import read_conversion_record
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.discovery import default_output_folder
from test_projection_convert import stack_experiment, write_mask


def convert(exp, to, *extra):
    return main(["convert", str(exp), "--to", str(to), "--axes", "ZCYX", *extra])


def test_convert_project_makes_copies_the_run_accepts(tmp_path, capsys):
    """The regression: ``--project`` did not exist, and a Z-stack could only be refused."""
    exp, copies = tmp_path / "exp", tmp_path / "copies"
    data = stack_experiment(exp)
    assert convert(exp, copies) == 1  # without --project a Z-stack is still refused
    refused = capsys.readouterr().out
    assert "are Z-stacks" in refused or "is a Z-stack" in refused
    assert "--project max|mean|sum" in refused
    assert "Fiji" not in refused and not copies.exists()

    assert convert(exp, copies, "--project", "max", "--z-range", "1:4") == 0
    text = capsys.readouterr().out
    assert "max projections of slices 1:4, written as 2-D OME-TIFF: 2 images" in text
    out = default_output_folder(copies.resolve())
    assert out.name == "copies_MorphoBricks_Preprocessing"
    (check,) = [line.split()[1:] for line in text.splitlines() if "preprocessing check " in line]
    (run,) = [line.split()[1:] for line in text.splitlines() if "preprocessing run " in line]
    assert check == ["check", str(copies), "--output", str(out)]
    assert run == ["run", str(copies), "--output", str(out), "--axes", "CYX"]
    for name, stack in data.items():
        assert (tifffile.imread(copies / name) == stack[1:4].max(axis=0)).all()
    record, _ = read_conversion_record(copies)
    assert record.projection.method == "max" and record.projection.z_range == (1, 4)
    # The next commands work: check (writes the design), confirm it, run.
    main(check)
    assert main(["design", "--output", str(out), "--confirm-all"]) == 0
    capsys.readouterr()
    assert main(run) == 0, capsys.readouterr().out
    assert (out / "plane_histograms.csv").is_file()


def test_the_check_of_a_z_stack_names_the_command(tmp_path, capsys):
    exp = tmp_path / "exp"
    stack_experiment(exp, names=("a.tif",))
    main(["check", str(exp), "--output", str(tmp_path / "o")])
    text = capsys.readouterr().out
    assert "Z-stack" in text
    assert "convert EXPERIMENT --to FOLDER --axes ROLES --project max|mean|sum" in text


@pytest.mark.parametrize("args", [["--project"], ["--project", "median"], ["--project", "MAX"]])
def test_project_needs_one_of_the_three_methods(tmp_path, capsys, args):
    with pytest.raises(SystemExit) as stopped:
        convert(tmp_path / "exp", tmp_path / "c", *args)
    assert stopped.value.code == 2
    error = capsys.readouterr().err
    assert "--project needs a method: max, mean or sum. There is no default" in error
    assert not (tmp_path / "c").exists()


@pytest.mark.parametrize("bad", ["3", "3:3", "5:2", "a:b", "-1:2", "1.5:3", "1:"])
def test_z_range_is_start_stop_whole_numbers(tmp_path, capsys, bad):
    with pytest.raises(SystemExit) as stopped:
        convert(tmp_path / "exp", tmp_path / "c", "--project", "max", f"--z-range={bad}")
    assert stopped.value.code == 2
    assert "--z-range needs START:STOP" in capsys.readouterr().err


def test_z_range_without_project_is_refused(tmp_path, capsys):
    exp = tmp_path / "exp"
    stack_experiment(exp, names=("a.tif",))
    assert convert(exp, tmp_path / "c", "--z-range", "0:2") == 2
    assert "--z-range needs --project" in capsys.readouterr().err
    assert not (tmp_path / "c").exists()


def test_a_range_that_does_not_fit_exits_1_and_writes_nothing(tmp_path, capsys):
    exp = tmp_path / "exp"
    stack_experiment(exp, names=("a.tif",), slices=4)
    assert convert(exp, tmp_path / "c", "--project", "max", "--z-range", "0:9") == 1
    text = capsys.readouterr().out
    assert "does not fit 1 image (a.tif: 4 slices)" in text
    assert "No copies were written" in text and not (tmp_path / "c").exists()


def test_a_3d_mask_stops_the_command_and_a_2d_mask_does_not(tmp_path, capsys):
    exp, masks = tmp_path / "exp", tmp_path / "masks"
    stack_experiment(exp, names=("ctrl/r1/a.tif",))
    write_mask(masks / "Nuclei" / "ctrl" / "r1" / "a.tif", (5, 8, 9), "ZYX")
    code = convert(exp, tmp_path / "c", "--project", "max", "--masks", str(masks))
    assert code == 1 and "Masks are never projected" in capsys.readouterr().out
    assert not (tmp_path / "c").exists()
    write_mask(masks / "Nuclei" / "ctrl" / "r1" / "a.tif", (8, 9), "YX")
    assert convert(exp, tmp_path / "c", "--project", "max", "--masks", str(masks)) == 0


def test_another_method_into_the_same_folder_is_refused_naming_both(tmp_path, capsys):
    exp, copies = tmp_path / "exp", tmp_path / "copies"
    stack_experiment(exp, names=("a.tif",))
    assert convert(exp, copies, "--project", "max") == 0
    capsys.readouterr()
    assert convert(exp, copies, "--project", "mean") == 1
    text = capsys.readouterr().out
    assert "holds max projections of all slices" in text and "asked for mean" in text
    assert convert(exp, copies, "--project", "max") == 0
    assert "already there and unchanged: 1 image" in capsys.readouterr().out
    assert np.issubdtype(tifffile.imread(copies / "a.tif").dtype, np.uint16)
