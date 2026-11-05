import argparse
import subprocess
import sys

import numpy as np
import pytest
from morphobricks_normalization.csv_io import read_plane_histograms

from morphobricks_preprocessing import (
    default_design,
    default_output_folder,
    preflight,
    read_manifest,
    write_experiment_design,
)
from morphobricks_preprocessing.cli import main, parse_size
from run_files import check_outputs
from tiff_tree import write_data, write_file, write_plain


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    rng = np.random.default_rng(0)
    for rel in ["ctrl/rep1/a.tif", "ctrl/rep1/b.tif", "drug/rep1/a.tif"]:
        write_data(root / rel, rng.integers(0, 200, (2, 3, 8, 8), dtype=np.uint8), axes="TCYX")
    return root


def confirm_design(root, output=None):
    """Write a design with every repeat group confirmed to the output folder."""
    design = default_design(preflight(root).dataset.repeats)
    for group in design.groups:
        design = design.with_confirmed(group, True, "2026-10-26")
    folder = output or default_output_folder(root)
    return write_experiment_design(design, folder / "experiment_design.csv")


@pytest.mark.parametrize(
    ("text", "size"),
    [("1GiB", 2**30), ("512 MiB", 512 * 2**20), ("1.5gib", int(1.5 * 2**30)), ("1000", 1000)]
    + [("2GB", 2 * 10**9), ("64KiB", 2**16)],
)
def test_parse_size(text, size):
    assert parse_size(text) == size


@pytest.mark.parametrize("text", ["", "-1GiB", "0", "1 TB", "lots"])
def test_parse_size_rejects_bad_values(text):
    with pytest.raises(argparse.ArgumentTypeError):
        parse_size(text)


def test_check_prints_summary_and_suggested_axes(experiment, capsys):
    assert main(["check", str(experiment)]) == 0
    out = capsys.readouterr().out
    assert "Images to analyse: 3 in 2 leaf folders" in out
    assert "ctrl/" in out and "rep1/  (2 images; technical repeat: single)" in out
    assert "Suggested option for 'run': --axes TCYX (axes in the files: TCYX)" in out
    # 'check' is the run's step stopped after the report: it writes the report, and the
    # design template prefilled from the folders (PP-8c).
    output = experiment.parent / "exp_MorphoBricks_Preprocessing"
    assert check_outputs(output) and f"Report written to {output}" in out
    assert f"The experiment design was written to {output / 'experiment_design.csv'}" in out


def test_check_exits_with_1_on_errors(experiment, capsys):
    write_file(experiment / "ctrl/rep1/bad.tif", b"garbage")
    assert main(["check", str(experiment)]) == 1
    assert "Error: 1 image could not be opened" in capsys.readouterr().out
    assert main(["check", str(experiment), "--exclude", "ctrl/rep1/bad.tif"]) == 0


def test_run_writes_the_table(experiment, capsys):
    confirm_design(experiment)
    assert main(["run", str(experiment), "--axes", "TCYX"]) == 0
    out = capsys.readouterr().out
    assert "[1/3] ctrl/rep1/a.tif" in out
    assert "Wrote 6 planes" in out
    output = experiment.parent / "exp_MorphoBricks_Preprocessing"
    records = read_plane_histograms(output / "plane_histograms.csv")
    assert {(r.t, r.channel_count) for r in records} == {(0, 3), (1, 3)}


def rgb_experiment(root):
    rng = np.random.default_rng(0)
    for rel in ["ctrl/rep1/a.tif", "drug/rep1/a.tif"]:
        data = rng.integers(0, 200, (2, 8, 8, 3), dtype=np.uint8)
        write_data(root / rel, data, axes="TYXS", photometric="rgb")
    return root


def test_check_suggests_the_image_type(experiment, tmp_path, capsys):
    assert main(["check", str(experiment)]) == 0
    out = capsys.readouterr().out
    assert "Suggested image type: fluorescence, because the images are not stored as RGB" in out
    assert main(["check", str(rgb_experiment(tmp_path / "rgb"))]) == 0
    assert "Suggested image type: rgb, because the images are stored as RGB colour" in (
        capsys.readouterr().out
    )


def test_run_uses_and_prints_the_suggested_image_type(tmp_path, capsys):
    root = rgb_experiment(tmp_path / "exp")
    confirm_design(root)
    assert main(["run", str(root), "--axes", "TYXC"]) == 0
    out = capsys.readouterr().out
    assert "Image type: rgb\n  suggested, because the images are stored as RGB colour" in out
    assert "Give --image-type fluorescence if these are fluorescence images." in out
    manifest = read_manifest(default_output_folder(root) / "run_manifest.json")
    assert manifest.image_type == "rgb" and manifest.channels_from_rgb_samples
    assert main(["run", str(root), "--axes", "TYXC", "--image-type", "fluorescence"]) == 0
    out = capsys.readouterr().out
    assert "Image type: fluorescence\n  given with --image-type" in out
    assert "Read: " not in out  # changing the type reads nothing again
    manifest = read_manifest(default_output_folder(root) / "run_manifest.json")
    assert manifest.image_type == "fluorescence"


def test_rgb_is_refused_for_images_that_cannot_be_rgb(experiment, capsys):
    confirm_design(experiment)
    assert main(["run", str(experiment), "--axes", "TCYX"]) == 0
    out = capsys.readouterr().out
    assert "Image type: fluorescence" in out
    assert "Give --image-type rgb if these are rgb images." in out
    assert main(["run", str(experiment), "--axes", "TCYX", "--image-type", "rgb"]) == 0
    capsys.readouterr()
    assert main(["run", str(experiment), "--axes", "TYCX", "--image-type", "rgb"]) == 1
    out = capsys.readouterr().out
    assert "contradict their header" in out
    assert "RGB images need exactly three colour channels" in out
    assert "8 channels of type uint8" in out and "no table or manifest was written" in out


def test_image_type_must_be_known(experiment):
    with pytest.raises(SystemExit) as excinfo:
        main(["run", str(experiment), "--axes", "TCYX", "--image-type", "brightfield"])
    assert excinfo.value.code == 2


def bright_tree(root):
    """Six 2-channel images in ctrl/rep1, one far brighter in C0, and drug/rep1 brighter."""
    rng = np.random.default_rng(3)
    second = np.clip(rng.normal(60, 10, (16, 16)), 0, 254).astype(np.uint8)
    levels = {"ctrl/rep1": [100, 102, 98, 101, 99, 180], "drug/rep1": [200, 202, 198]}
    for folder, values in levels.items():
        for i, level in enumerate(values):
            first = np.clip(rng.normal(level, 10, (16, 16)), 0, 254).astype(np.uint8)
            write_data(root / folder / f"img_{i}.tif", np.stack([first, second]), axes="CYX")
    return root


def test_run_prints_the_intensity_checks(tmp_path, capsys):
    root = bright_tree(tmp_path / "exp")
    confirm_design(root)
    assert main(["run", str(root), "--axes", "CYX"]) == 0
    out = capsys.readouterr().out
    assert "Intensity checks: 9 images checked: " in out
    assert "Limits: more than 3 robust deviations from the others, more than 1 % saturated" in out
    assert "Images far from the others of their biological repeat (image_intensity_outlier" in out
    assert "  - Image ctrl/rep1/img_5.tif, channel C0: its " in out
    assert out.count("What to do: Look at the image") == 1
    # drug is brighter than ctrl, but conditions are never compared.
    assert "drug/rep1" not in out.split("Intensity checks:")[1]
    args = ["run", str(root), "--axes", "CYX", "--outlier-limit", "50"]
    args += ["--saturation-limit", "0.2", "--min-images", "3"]
    assert main(args) == 0
    assert "no intensity warnings" in capsys.readouterr().out
    limits = read_manifest(default_output_folder(root) / "run_manifest.json").intensity_checks
    assert (limits.outlier_limit, limits.saturation_limit, limits.min_images) == (50, 0.2, 3)


def test_invalid_check_limits_are_refused(experiment, capsys):
    assert main(["run", str(experiment), "--axes", "TCYX", "--min-images", "1"]) == 1
    assert "min_images must be at least 2" in capsys.readouterr().out


def test_run_options_reach_the_run(experiment, tmp_path, capsys):
    out_dir = tmp_path / "results"
    log = tmp_path / "run.log"
    args = ["run", str(experiment), "--axes", "TCYX", "--output", str(out_dir)]
    args += ["--bins", "16", "--memory-limit", "256MiB", "--exclude", "drug/rep1/a.tif"]
    args += ["--log", str(log)]
    confirm_design(experiment, out_dir)
    assert main(args) == 0
    manifest = read_manifest(out_dir / "run_manifest.json")
    assert manifest.settings.bin_count == 16
    assert manifest.settings.memory_limit == 256 * 2**20
    assert manifest.settings.exclude == ("drug/rep1/a.tif",)
    assert manifest.mapping.roles == ("T", "C", "Y", "X")
    assert "Wrote" in log.read_text(encoding="utf-8")
    capsys.readouterr()
    assert main(args + ["--mode", "full"]) == 0
    assert "a full run was asked for (mode full)" in capsys.readouterr().out


def test_run_rejects_axes_of_the_wrong_length(experiment, capsys):
    assert main(["run", str(experiment), "--axes", "TYX"]) == 1
    out = capsys.readouterr().out
    assert "does not fit the images, whose axes are TCYX" in out
    assert "--axes TCYX" in out


def test_run_reports_blocking_errors(experiment, capsys):
    confirm_design(experiment)
    assert main(["run", str(experiment), "--axes", "Z-YX"]) == 1
    out = capsys.readouterr().out
    assert "No role is chosen for the axis C (channel)" in out
    assert "no table or manifest was written" in out


def test_run_without_readable_images(tmp_path, capsys):
    write_file(tmp_path / "exp/bad.tif", b"garbage")
    assert main(["run", str(tmp_path / "exp"), "--axes", "YX"]) == 1
    assert "could not be opened" in capsys.readouterr().out


def test_missing_axes_option(tmp_path, capsys):
    root = tmp_path / "exp"
    # b.tif lacks a C axis of one channel (it lacked Z until Z-stacks were refused).
    write_data(root / "a.tif", np.zeros((3, 1, 8, 8), np.uint8), axes="TCYX")
    write_data(root / "b.tif", np.zeros((3, 8, 8), np.uint8), axes="TYX")
    confirm_design(root)
    assert main(["run", str(root), "--axes", "TCYX"]) == 1
    assert main(["run", str(root), "--axes", "TCYX", "--missing-as-singleton"]) == 0


def test_different_channel_counts_stop_check_and_run(tmp_path, capsys):
    root = tmp_path / "exp"
    write_data(root / "a.tif", np.zeros((2, 3, 8, 8), np.uint8), axes="TCYX")
    write_data(root / "b.tif", np.zeros((2, 2, 8, 8), np.uint8), axes="TCYX")
    write_data(root / "c.tif", np.zeros((2, 3, 8, 8), np.uint8), axes="TCYX")
    assert main(["check", str(root)]) == 1
    out = capsys.readouterr().out
    assert "different numbers of channels: 3 channels (2 images), 2 channels (1 image)" in out
    confirm_design(root)
    assert main(["run", str(root), "--axes", "TCYX"]) == 1
    out = capsys.readouterr().out
    assert "different numbers of channels" in out and "b.tif" in out
    assert "no table or manifest was written" in out


def test_axes_are_needed_for_the_first_run_of_plain_stacks_only(tmp_path, capsys):
    root = tmp_path / "exp"
    write_plain(root / "ctrl/rep1/a.tif", np.zeros((2, 8, 8), np.uint8))
    confirm_design(root)
    assert main(["run", str(root)]) == 1
    assert "Give the role of each axis with --axes" in capsys.readouterr().out
    assert main(["run", str(root), "--axes", "TYX"]) == 0
    capsys.readouterr()
    assert main(["run", str(root)]) == 0
    assert "as confirmed for the earlier run (TYX)" in capsys.readouterr().out


def test_a_plain_stack_among_named_images_still_needs_axes(tmp_path, capsys):
    # F9 review: one plain stack is enough for the roles not to be taken from the headers;
    # images that no mapping describes are named instead of "--axes None".
    root = tmp_path / "exp"
    write_data(root / "ctrl/rep1/a.tif", np.zeros((2, 8, 8), np.uint8), axes="TYX")
    write_plain(root / "ctrl/rep1/b.tif", np.zeros((2, 8, 8), np.uint8))
    confirm_design(root)
    assert main(["run", str(root)]) == 1
    out = capsys.readouterr().out
    assert "--axes None" not in out and "Axis roles" not in out
    assert "arranged differently: IYX (1 image), TYX (1 image)" in out
    assert not (root.parent / "exp_MorphoBricks_Preprocessing" / "run_manifest.json").exists()


def test_images_that_name_their_axes_need_no_axes_option(experiment, capsys):
    # F9 (cross-brick test, 3 November 2026; design v0.4 §3): --axes is for plain stacks.
    confirm_design(experiment)
    assert main(["run", str(experiment)]) == 0
    out = capsys.readouterr().out
    assert "Axis roles (as every image names them):" in out
    assert "Give the role of each axis" not in out
    capsys.readouterr()
    # A later run in the same output folder reuses the confirmed roles.
    assert main(["run", str(experiment)]) == 0
    out = capsys.readouterr().out
    assert "Axis roles: as confirmed for the earlier run (TCYX)" in out
    assert "Unchanged (rows kept): 3 images" in out


def test_python_dash_m(experiment):
    completed = subprocess.run(
        [sys.executable, "-m", "morphobricks_preprocessing", "check", str(experiment)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--axes TCYX" in completed.stdout
