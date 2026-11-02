"""PP-8c item 3: the ``masks`` command uses the inputs the run recorded, like ``run`` (PP-7).

Given only ``--output``, ``masks`` takes the experiment folder from the folder's v2
manifest (``experiment_not_recorded`` without one), and the masks folder, mask axes and
exclusions recorded there unless they are given. A value given that differs from the
recorded one is used, and named with its effect.
"""

import json

import numpy as np
import pytest

from morphobricks_preprocessing import (
    default_design,
    prefill_mapping,
    preflight,
    read_manifest,
    run_experiment,
)
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.dataset import biological_repeats
from morphobricks_preprocessing.run.recorded import GivenInputs, resolve_inputs
from morphobricks_preprocessing.run.reuse import read_previous
from morphobricks_preprocessing.testing import make_clean_run
from tiff_tree import write_data, write_plain
from zarr_tree import write_labels, write_ngff

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


def test_dropping_the_masks_drops_the_mask_axes_stated_for_them(tmp_path, capsys):
    """``--no-masks`` after a run with stated mask axes runs without masks (PP-10c)."""
    root, masks, output = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    for seed, rel in enumerate(["ctrl/r1/a.tif", "drug/r1/a.tif"]):
        write_data(root / rel, np.random.default_rng(seed).integers(0, 9, (3, 8, 8), "u1"), "ZYX")
        write_plain(masks / "Cells" / rel, np.ones((3, 8, 8), np.uint16))
    design = default_design(biological_repeats(preflight(root).discovery))
    design = design.with_confirmed("r1", True, "2026-11-02")
    stated = run_experiment(
        root,
        prefill_mapping("ZYX"),
        output_folder=output,
        masks_folder=masks,
        mask_axes={"Cells": "ZYX"},
        design=design,
    )
    assert stated.written, stated.issues
    assert main(["run", str(root), "--output", str(output), "--no-masks"]) == 0
    assert "invalid_mask_axes" not in capsys.readouterr().out
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest.get("masks") is None


def test_dropping_the_masks_keeps_the_mask_axes_of_label_images(tmp_path):
    """Only the masks folder's statements go with it; label-image ones stay (review)."""
    pytest.importorskip("zarr")
    root, masks, output = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    for rel in ["ctrl/r1/a.ome.zarr", "drug/r1/a.ome.zarr"]:
        image = write_ngff(root / rel, np.zeros((2, 8, 8), np.uint8), "zyx")
        write_labels(image, {"lab": np.ones((8, 8), np.uint16)}, "yx")  # 2-D, stated shared
    for rel in ["ctrl/r1/b.tif", "drug/r1/b.tif"]:
        write_data(root / rel, np.ones((2, 8, 8), np.uint8), "ZYX")
        write_plain(masks / "Cells" / rel, np.ones((2, 8, 8), np.uint16))
    design = default_design(biological_repeats(preflight(root).discovery))
    design = design.with_confirmed("r1", True, "2026-11-02")
    first = run_experiment(
        root,
        prefill_mapping("ZYX"),
        output_folder=output,
        masks_folder=masks,
        mask_axes={"Cells": "ZYX", "lab": "YX"},
        design=design,
    )
    assert first.written, first.issues
    previous = read_previous(output)[0]
    dropped = resolve_inputs(GivenInputs(masks_folder=False), previous)
    assert (dropped.masks_folder, dropped.mask_axes) == (None, {"lab": "YX"})
    # The same label statement given again is no change; only the folder is dropped.
    again = resolve_inputs(GivenInputs(masks_folder=False, mask_axes={"lab": "YX"}), previous)
    assert [change.name for change in again.changes] == ["masks_folder"]
    # A new statement is named against the statements the run keeps, not the dropped ones.
    added = resolve_inputs(GivenInputs(masks_folder=False, mask_axes={"N": "ZYX"}), previous)
    change = next(change for change in added.changes if change.name == "mask_axes")
    assert "Cells" not in change.recorded + change.given and "N" in change.given
    result = run_experiment(root, output_folder=output, masks_folder=False, design=design)
    assert result.written, result.issues
    recorded = read_manifest(result.manifest_path).masks
    assert {(f["type"], f["roles"], f["axes_from"]) for f in recorded["files"]} == {
        ("lab", "YX", "stated")
    }
    assert [change.name for change in result.input_changes] == ["masks_folder"]


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
