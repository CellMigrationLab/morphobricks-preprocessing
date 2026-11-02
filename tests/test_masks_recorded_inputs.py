"""PP-8c item 3: the ``masks`` command uses the inputs the run recorded, like ``run`` (PP-7).

Given only ``--output``, ``masks`` takes the experiment folder from the folder's v2
manifest (``experiment_not_recorded`` without one), and the masks folder, mask axes and
exclusions recorded there unless they are given. A value given that differs from the
recorded one is used, and named with its effect.
"""

import json

import numpy as np

from morphobricks_preprocessing import default_design, prefill_mapping, preflight, run_experiment
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.dataset import biological_repeats
from morphobricks_preprocessing.testing import make_clean_run
from tiff_tree import write_data, write_plain

RELATIONS = "mask_relationships.json"


def relations(output):
    return json.loads((output / RELATIONS).read_text(encoding="utf-8"))


def test_masks_given_only_the_output_folder_uses_the_recorded_run(tmp_path, capsys):
    clean = make_clean_run(tmp_path, axes="YX", shape=(8, 8), mask_types=("Cells",))
    assert main(["masks", "--output", str(clean.output)]) == 0
    out = capsys.readouterr().out
    assert "Changed" not in out
    assert relations(clean.output)  # written, from the recorded experiment and masks


def test_masks_takes_the_recorded_masks_folder(tmp_path, capsys):
    clean = make_clean_run(tmp_path, axes="YX", shape=(8, 8), mask_types=("Cells",))
    assert main(["masks", str(clean.experiment), "--output", str(clean.output)]) == 0
    assert (clean.output / RELATIONS).is_file()


def test_masks_without_an_experiment_or_a_recorded_run_stops(tmp_path, capsys):
    assert main(["masks", "--output", str(tmp_path / "out")]) == 1
    out = capsys.readouterr().out
    assert "No experiment folder was given" in out
    assert not (tmp_path / "out" / RELATIONS).exists()


def test_masks_names_a_masks_folder_that_differs_from_the_recorded_one(tmp_path, capsys):
    clean = make_clean_run(tmp_path, axes="YX", shape=(8, 8), mask_types=("Cells",))
    other = tmp_path / "other_masks"
    (clean.masks / "Cells").rename(clean.masks / "Nuclei")
    clean.masks.rename(other)
    assert main(["masks", "--output", str(clean.output), "--masks", str(other)]) == 0
    out = capsys.readouterr().out
    assert f"Changed masks_folder: {clean.masks.as_posix()} -> {other.as_posix()}" in out
    assert "the run keeps the recorded value until it is given this one" in out


def test_masks_takes_the_recorded_mask_axes_of_plain_stacks(tmp_path, capsys):
    root, masks, output = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    for seed, rel in enumerate(["ctrl/r1/a.tif", "drug/r1/a.tif"]):
        write_data(root / rel, np.random.default_rng(seed).integers(0, 9, (3, 8, 8), "u1"), "ZYX")
        labels = np.zeros((3, 8, 8), np.uint16)
        labels[:, 1:4, 1:4] = 1
        write_plain(masks / "Cells" / rel, labels)  # a plain stack: its axes are stated
    design = default_design(biological_repeats(preflight(root).discovery))
    design = design.with_confirmed("r1", True, "2026-11-02")
    result = run_experiment(
        root,
        prefill_mapping("ZYX"),
        output_folder=output,
        masks_folder=masks,
        mask_axes={"Cells": "ZYX"},
        design=design,
    )
    assert result.written, result.issues
    assert main(["masks", "--output", str(output)]) == 0
    assert (output / RELATIONS).is_file()


def test_masks_takes_the_recorded_exclusions(tmp_path, capsys):
    clean = make_clean_run(tmp_path, axes="YX", shape=(8, 8), mask_types=("Cells",))
    excluded = "ctrl/rep1/b.ome.tif"
    assert run_experiment(output_folder=clean.output, exclude=[excluded]).written
    assert main(["masks", "--output", str(clean.output)]) == 0
    out = capsys.readouterr().out
    assert "[3/3]" in out and "/4]" not in out  # the excluded image's mask is not read
    assert "Changed" not in out
    assert main(["masks", "--output", str(clean.output), "--no-exclude"]) == 0
    assert "Changed exclude: ctrl/rep1/b.ome.tif -> none" in capsys.readouterr().out
