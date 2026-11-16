"""The command line's ``convert-mask`` (Spec ADR 0005 BM12): it calls the conversion helpers."""

from __future__ import annotations

import json

import tifffile

from conversion_tree import CLASSES, lines, run_with, tree
from morphobricks_preprocessing.cli import main


def test_convert_mask_to_instances_writes_and_names_the_next_steps(tmp_path, capsys):
    run, image, masks = run_with(tmp_path)
    args = ["convert-mask", "--output", str(run.output_folder), "--mask", "TumorRegion"]
    assert main([*args, "--to-instances", "TumorObjects"]) == 0
    printed = capsys.readouterr().out
    assert f"{image}: TumorObjects/{image} (written)" in printed
    assert "declare the mask TumorObjects (source type TumorObjects) as instances" in printed
    assert "run preprocessing and the masks step again" in printed
    line = json.loads(lines(masks, "TumorObjects"))
    assert line["operation"] == "binary_to_instances"
    assert tifffile.imread(masks / f"TumorObjects/{image}").max() == 2
    before = tree(masks)
    assert main([*args, "--to-instances", "TumorObjects"]) == 0  # the same call: a no-op
    assert "(already written)" in capsys.readouterr().out
    assert tree(masks) == before


def test_convert_mask_to_classes_writes_one_mask_per_class(tmp_path, capsys):
    run, image, masks = run_with(tmp_path, extra={"Classes": CLASSES})
    args = ["convert-mask", "--output", str(run.output_folder), "--mask", "Classes"]
    assert main([*args, "--to-classes", "Tissue", "--image", image]) == 0
    printed = capsys.readouterr().out
    assert f"Tissue_class1/{image} (written), Tissue_class2/{image} (written)" in printed
    assert "declare the mask Tissue_class2 (source type Tissue_class2) as binary" in printed


def test_convert_mask_prints_a_refusal_and_exits_1(tmp_path, capsys):
    run, image, masks = run_with(tmp_path)
    before = tree(masks)
    args = ["convert-mask", "--output", str(run.output_folder), "--mask", "Nuclei"]
    assert main([*args, "--to-instances", "TumorObjects"]) == 1
    printed = capsys.readouterr().out
    assert printed.startswith(f"{image}: not converted. 'Nuclei' (nuclei/{image}) is declared")
    assert tree(masks) == before
    assert main([*args[:-1], "Tumour", "--to-instances", "TumorObjects"]) == 1
    assert "'Tumour' is not a mask of this run's description" in capsys.readouterr().out
