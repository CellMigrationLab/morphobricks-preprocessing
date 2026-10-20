import argparse
import subprocess
import sys

import numpy as np
import pytest

from morphobricks_preprocessing import read_manifest, read_plane_histograms
from morphobricks_preprocessing.cli import main, parse_size
from tiff_tree import write_data, write_file


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    rng = np.random.default_rng(0)
    for rel in ["ctrl/rep1/a.tif", "ctrl/rep1/b.tif", "drug/rep1/a.tif"]:
        write_data(root / rel, rng.integers(0, 200, (2, 3, 8, 8), dtype=np.uint8), axes="ZCYX")
    return root


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
    assert "Suggested option for 'run': --axes ZCYX (axes in the files: ZCYX)" in out
    assert not (experiment.parent / "exp_MorphoBricks_Preprocessing").exists()


def test_check_exits_with_1_on_errors(experiment, capsys):
    write_file(experiment / "ctrl/rep1/bad.tif", b"garbage")
    assert main(["check", str(experiment)]) == 1
    assert "Error: 1 image could not be opened" in capsys.readouterr().out
    assert main(["check", str(experiment), "--exclude", "ctrl/rep1/bad.tif"]) == 0


def test_run_writes_the_table(experiment, capsys):
    assert main(["run", str(experiment), "--axes", "ZCYX"]) == 0
    out = capsys.readouterr().out
    assert "[1/3] ctrl/rep1/a.tif" in out
    assert "Wrote 18 planes" in out
    output = experiment.parent / "exp_MorphoBricks_Preprocessing"
    records = read_plane_histograms(output / "plane_histograms.csv")
    assert {(r.z, r.c) for r in records} == {(z, c) for z in range(2) for c in range(3)}


def test_run_options_reach_the_run(experiment, tmp_path, capsys):
    out_dir = tmp_path / "results"
    log = tmp_path / "run.log"
    args = ["run", str(experiment), "--axes", "TCYX", "--output", str(out_dir)]
    args += ["--bins", "16", "--memory-limit", "256MiB", "--exclude", "drug/rep1/a.tif"]
    args += ["--log", str(log)]
    assert main(args) == 0
    manifest = read_manifest(out_dir / "run_manifest.json")
    assert manifest.settings.bin_count == 16
    assert manifest.settings.memory_limit == 256 * 2**20
    assert manifest.settings.exclude == ("drug/rep1/a.tif",)
    assert manifest.mapping.roles == ("T", "C", "Y", "X")
    assert "Wrote" in log.read_text(encoding="utf-8")
    capsys.readouterr()
    assert main(args + ["--force-rebuild"]) == 0
    assert "a full rebuild was requested" in capsys.readouterr().out


def test_run_rejects_axes_of_the_wrong_length(experiment, capsys):
    assert main(["run", str(experiment), "--axes", "ZYX"]) == 1
    out = capsys.readouterr().out
    assert "does not fit the images, whose axes are ZCYX" in out
    assert "--axes ZCYX" in out


def test_run_reports_blocking_errors(experiment, capsys):
    assert main(["run", str(experiment), "--axes", "Z-YX"]) == 1
    out = capsys.readouterr().out
    assert "No role is chosen for the axis C (channel)" in out
    assert "Nothing was written" in out


def test_run_without_readable_images(tmp_path, capsys):
    write_file(tmp_path / "exp/bad.tif", b"garbage")
    assert main(["run", str(tmp_path / "exp"), "--axes", "YX"]) == 1
    assert "could not be opened" in capsys.readouterr().out


def test_missing_axes_option(tmp_path, capsys):
    root = tmp_path / "exp"
    write_data(root / "a.tif", np.zeros((2, 3, 8, 8), np.uint8), axes="ZCYX")
    write_data(root / "b.tif", np.zeros((2, 8, 8), np.uint8), axes="ZYX")
    assert main(["run", str(root), "--axes", "ZCYX"]) == 1
    assert main(["run", str(root), "--axes", "ZCYX", "--missing-as-singleton"]) == 0


def test_axes_are_required_for_run(experiment):
    with pytest.raises(SystemExit) as excinfo:
        main(["run", str(experiment)])
    assert excinfo.value.code == 2


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
    assert "--axes ZCYX" in completed.stdout


@pytest.mark.parametrize(
    ("text", "levels"), [("auto", None), ("none", 0), ("3", 3), ("AUTO", None)]
)
def test_parse_pyramid_levels(text, levels):
    from morphobricks_preprocessing.cli import parse_pyramid_levels

    assert parse_pyramid_levels(text) == levels


@pytest.mark.parametrize("text", ["8", "-1", "many"])
def test_parse_pyramid_levels_rejects_bad_values(text):
    from morphobricks_preprocessing.cli import parse_pyramid_levels

    with pytest.raises(argparse.ArgumentTypeError):
        parse_pyramid_levels(text)


def test_conversion_options(experiment, tmp_path, capsys):
    pytest.importorskip("zarr")
    out_dir = tmp_path / "out"
    args = ["run", str(experiment), "--axes", "ZCYX", "--output", str(out_dir), "--convert-tiff"]
    args += ["--chunk-size", "64", "--compression", "zstd", "--compression-level", "7"]
    args += ["--pyramid-levels", "none"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "Conversion (chunks of 64 x 64 px, Blosc Zstandard level 7, no smaller levels)" in out
    assert "so the space they take is duplicated" in out and "free space on the output" in out
    settings = read_manifest(out_dir / "run_manifest.json").settings
    assert settings.convert_tiffs
    assert (settings.conversion.chunk_size, settings.conversion.compression) == (64, "zstd")


def test_invalid_chunk_size_is_refused(experiment, capsys):
    assert main(["run", str(experiment), "--axes", "ZCYX", "--chunk-size", "4"]) == 1
    assert "chunk size must be between 16" in capsys.readouterr().out
