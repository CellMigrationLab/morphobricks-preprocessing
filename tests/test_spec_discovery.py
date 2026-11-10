"""Conformance P21, P26 (recorded header names) and P29: discovery before any description.

Spec experiment-v1 §3.3 and §6, conformance plan section 4. The refusals apply only when a
description is in use (owner decision Q1): a run without one accepts what v0.4 accepted. P21 and
P29 are shown on their named fixtures (``v01``, ``v07``) through the run, the mask step and the
command line (``check``, ``run``, ``masks``, ``describe``); the notebook's folder check and
*Check and save* are in ``test_notebook_name_refusals.py``.
P26's comparison of header names with a description's confirmed names (``R_HEADER_CONTRADICTION``)
belongs to the physical checks (Stage 1, PR 8); here the names are recorded as read, untrimmed.
"""

import numpy as np
import tifffile

from described_tree import main_described, run_described
from description_tree import MAPPING, build_experiment, write_image, write_mask
from morphobricks_preprocessing import (
    prefill_mapping,
    preflight,
    read_manifest,
    run_experiment,
)
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.metadata import inspect_tiff
from morphobricks_preprocessing.run.mask_step import prepare_mask_step
from spec_pin import fixtures
from tiff_tree import write_data, write_tiff

DECOMPOSED = "Café"
COMPOSED = "Café"
VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}


def codes(issues):
    return [issue.code for issue in issues]


def issue(issues, code):
    return next(i for i in issues if i.code == code)


def masks_tree(tmp_path, *mask_types):
    root = tmp_path / "exp"
    write_tiff(root / "ctrl" / "rep1" / "a.tif")
    masks = tmp_path / "masks"
    for name in mask_types:
        write_data(masks / name / "ctrl" / "rep1" / "a.tif", np.ones((8, 8), np.uint16))
    return root, masks


# P21 ----------------------------------------------------------------------------------


def test_p21_a_decomposed_folder_is_refused_with_a_description(tmp_path):
    root = tmp_path / "exp"
    write_tiff(root / DECOMPOSED / "rep1" / "a.tif")
    write_tiff(root / "ctrl" / DECOMPOSED / "a.tif")
    pre = preflight(root)
    result = pre.discovery
    found = issue(pre.issues, "R_FOLDER_NOT_NFC")
    assert found.is_error
    assert set(found.paths) == {DECOMPOSED, f"ctrl/{DECOMPOSED}"}
    # Nothing is renamed or normalized: the labels and the folders are as read.
    assert {leaf.condition for leaf in result.leaves} == {DECOMPOSED, "ctrl"}
    assert (root / DECOMPOSED).is_dir()


def test_p21_a_composed_folder_is_accepted(tmp_path):
    root = tmp_path / "exp"
    write_tiff(root / COMPOSED / "rep1" / "a.tif")
    assert "R_FOLDER_NOT_NFC" not in codes(preflight(root).issues)


def test_p21_preflight_carries_the_refusal(tmp_path):
    root = tmp_path / "exp"
    write_tiff(root / DECOMPOSED / "rep1" / "a.tif")
    assert "R_FOLDER_NOT_NFC" in codes(preflight(root).issues)


# P26 (the part that is recorded here) -------------------------------------------------


def write_named(path, names):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(
        path,
        np.zeros((len(names), 6, 7), np.uint16),
        ome=True,
        photometric="minisblack",
        metadata={"axes": "CYX", "Channel": {"Name": names}},
    )
    return path


def test_p26_the_header_names_are_recorded_as_read_untrimmed(tmp_path):
    path = write_named(tmp_path / "a.ome.tif", [" DAPI", f"{DECOMPOSED} 488"])
    info = inspect_tiff(path)
    assert info.raw_channel_names == (" DAPI", f"{DECOMPOSED} 488")
    # The trimmed names the existing checks use are unchanged.
    assert info.channel_names == ("DAPI", f"{DECOMPOSED} 488")
    assert info.channel_names_trimmed


def test_p26_the_manifest_records_the_header_name_as_read(tmp_path):
    root = tmp_path / "exp"
    write_named(root / "ctrl" / "a.ome.tif", [" DAPI", f"{DECOMPOSED} 488"])
    out = tmp_path / "out"
    run_described(root, prefill_mapping("CYX"), output_folder=out)
    entry = read_manifest(out / "run_manifest.json").files[0]
    assert entry.raw_channel_names == (" DAPI", f"{DECOMPOSED} 488")
    assert entry.channel_names == ("DAPI", f"{DECOMPOSED} 488")
    assert entry.channel_names_trimmed


# P29 ----------------------------------------------------------------------------------


def test_p29_na_and_a_leading_space_are_invalid_mask_type_names(tmp_path):
    root, masks = masks_tree(tmp_path, "NA", " cells", "Nuclei")
    pre = preflight(root, masks_folder=masks)
    found = issue(pre.masks.issues, "R_MASK_TYPE_NAME_INVALID")
    assert found.is_error
    assert set(found.paths) == {"NA", " cells"}
    assert "R_FOLDER_NOT_NFC" not in codes(pre.masks.issues)
    assert not pre.ok
    # Never renamed: the types are as discovered.
    assert set(pre.masks.types) == {"NA", " cells", "Nuclei"}


def test_p29_a_decomposed_mask_type_is_not_nfc(tmp_path):
    root, masks = masks_tree(tmp_path, DECOMPOSED, "Nuclei")
    pre = preflight(root, masks_folder=masks)
    found = issue(pre.masks.issues, "R_FOLDER_NOT_NFC")
    assert found.is_error and found.paths == (DECOMPOSED,)
    assert "R_MASK_TYPE_NAME_INVALID" not in codes(pre.masks.issues)
    assert DECOMPOSED in pre.masks.types


def test_p29_the_three_cases_together(tmp_path):
    root, masks = masks_tree(tmp_path, "NA", " cells", DECOMPOSED)
    found = preflight(root, masks_folder=masks).masks.issues
    assert issue(found, "R_MASK_TYPE_NAME_INVALID").paths == (" cells", "NA")
    assert issue(found, "R_FOLDER_NOT_NFC").paths == (DECOMPOSED,)


def test_p29_valid_mask_types_are_accepted(tmp_path):
    root, masks = masks_tree(tmp_path, COMPOSED, "Nuclei")
    pre = preflight(root, masks_folder=masks)
    assert not {"R_FOLDER_NOT_NFC", "R_MASK_TYPE_NAME_INVALID"} & set(codes(pre.masks.issues))


# P21 and P29 on their named fixtures, through the production paths --------------------
#
# A run, the mask step and the command line refuse these names; the tests above call
# ``preflight`` directly. Without a published description every one of them is refused first
# (``A_DESCRIPTION_REQUIRED``, Spec ADR 0002 AR10): there is no undescribed run any more.

NAME_CODES = {"R_FOLDER_NOT_NFC", "R_MASK_TYPE_NAME_INVALID"}
P29_TYPES = ("nuclei", "cells", "NA", " cells", DECOMPOSED)


def published(tmp_path, name, *, publish=True):
    """Build the tree of fixture ``name``; publish it in the output folder unless told not to."""
    data = VALID[name].data
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir(parents=True)
    if publish:
        (out / "morphobricks.toml").write_bytes(data)
    return root, out, build_experiment(root, description_from_bytes(data))


def p21_tree(tmp_path, *, publish=True):
    """``v01`` with a decomposed folder ``CTRL/Cafe\\u0301`` holding one image."""
    root, out, _ = published(tmp_path, "v01", publish=publish)
    write_image(root / "CTRL" / DECOMPOSED / "a.tif", 2)
    return root, out


def p29_tree(tmp_path, *, publish=True):
    """``v07`` with the mask folders of :data:`P29_TYPES`: two declared, three refused."""
    root, out, images = published(tmp_path, "v07", publish=publish)
    masks = tmp_path / "masks"
    for mask_type in P29_TYPES:
        for image in images:
            write_mask(masks, mask_type, image, [0, 1, 2])
    return root, out, masks


def test_p21_a_run_with_published_v01_refuses_a_decomposed_folder(tmp_path):
    root, out = p21_tree(tmp_path)
    result = run_described(root, MAPPING, output_folder=out)
    assert not result.written
    found = issue(result.issues, "R_FOLDER_NOT_NFC")
    assert found.is_error and found.paths == (f"CTRL/{DECOMPOSED}",)
    assert "R_UNIT_NOT_DECLARED" in codes(result.issues)  # the unit is new, as before
    # Nothing renamed or normalized: the folder is as written, no composed twin appears.
    assert (root / "CTRL" / DECOMPOSED).is_dir()
    assert sorted(p.name for p in (root / "CTRL").iterdir()) == sorted([DECOMPOSED, "rep1", "rep2"])
    assert not (out / "plane_histograms.csv").exists()


def test_p29_a_run_with_published_v07_refuses_the_mask_type_names(tmp_path):
    root, out, masks = p29_tree(tmp_path)
    result = run_described(root, MAPPING, output_folder=out, masks_folder=masks)
    assert not result.written
    invalid = issue(result.issues, "R_MASK_TYPE_NAME_INVALID")
    assert invalid.is_error and invalid.paths == (" cells", "NA")
    not_nfc = issue(result.issues, "R_FOLDER_NOT_NFC")
    assert not_nfc.is_error and not_nfc.paths == (DECOMPOSED,)
    assert "R_MASK_TYPE_NOT_DECLARED" in codes(result.issues)
    assert sorted(p.name for p in masks.iterdir()) == sorted(P29_TYPES)  # never renamed


def test_p29_the_mask_step_refuses_the_names_with_a_published_description(tmp_path):
    root, out, masks = p29_tree(tmp_path)
    step = prepare_mask_step(root, out, masks_folder=masks)
    assert issue(step.issues, "R_MASK_TYPE_NAME_INVALID").paths == (" cells", "NA")
    assert issue(step.issues, "R_FOLDER_NOT_NFC").paths == (DECOMPOSED,)
    assert not step.ready


def test_p21_the_mask_step_refuses_a_decomposed_experiment_folder(tmp_path):
    root, out = p21_tree(tmp_path)
    masks = tmp_path / "masks"
    write_mask(masks, "nuclei", f"CTRL/{DECOMPOSED}/a.tif", [0, 1])
    step = prepare_mask_step(root, out, masks_folder=masks)
    assert issue(step.issues, "R_FOLDER_NOT_NFC").paths == (f"CTRL/{DECOMPOSED}",)
    assert not step.ready


# The command line ------------------------------------------------------------------------
#
# The command line prints the plain message; the report it writes holds the code.

NFC_TEXT = "not in Unicode NFC form"
INVALID_TEXT = "cannot identify a mask type"


def reported(out):
    """Return the codes of the issues in the output folder's ``discovery_report.json``."""
    report = (out / "discovery_report.json").read_text(encoding="utf-8")
    return {code for code in NAME_CODES | {"R_MASK_TYPE_NOT_DECLARED"} if f'"{code}"' in report}


def test_p21_the_command_line_check_and_run_refuse_a_decomposed_folder(tmp_path, capsys):
    root, out = p21_tree(tmp_path)
    assert main_described(["check", str(root), "--output", str(out)]) == 1
    assert NFC_TEXT in capsys.readouterr().out
    assert reported(out) == {"R_FOLDER_NOT_NFC"}
    assert main_described(["run", str(root), "--output", str(out), "--axes", "CYX"]) == 1
    assert NFC_TEXT in capsys.readouterr().out
    assert reported(out) == {"R_FOLDER_NOT_NFC"}
    assert not (out / "plane_histograms.csv").exists()


def test_p29_the_command_line_check_and_masks_refuse_the_mask_type_names(tmp_path, capsys):
    root, out, masks = p29_tree(tmp_path)
    assert main_described(["check", str(root), "--output", str(out), "--masks", str(masks)]) == 1
    printed = capsys.readouterr().out
    assert INVALID_TEXT in printed and NFC_TEXT in printed
    assert {"R_MASK_TYPE_NAME_INVALID", "R_FOLDER_NOT_NFC"} <= reported(out)
    assert main_described(["masks", str(root), "--output", str(out), "--masks", str(masks)]) == 1
    printed = capsys.readouterr().out
    assert INVALID_TEXT in printed and NFC_TEXT in printed
    assert not (out / "mask_relationships.json").exists()


def test_p21_the_command_line_describe_refuses_a_decomposed_folder(tmp_path, capsys):
    root, out = p21_tree(tmp_path)
    assert main(["describe", str(root), "--output", str(out)]) == 1
    assert NFC_TEXT in capsys.readouterr().out
    # Without a published description too: describe drafts a description from the folders.
    root, out = p21_tree(tmp_path / "draft", publish=False)
    assert main(["describe", str(root), "--output", str(out)]) == 1
    assert NFC_TEXT in capsys.readouterr().out
    assert list(out.iterdir()) == []  # describe writes nothing


def test_p21_p29_without_a_published_description_every_route_is_refused(tmp_path, capsys):
    root, out, masks = p29_tree(tmp_path, publish=False)
    write_image(root / "CTRL" / DECOMPOSED / "a.tif", 2)
    result = run_experiment(root, MAPPING, output_folder=out, masks_folder=masks)
    assert codes(result.issues) == ["A_DESCRIPTION_REQUIRED"] and not result.written
    step = prepare_mask_step(root, out, masks_folder=masks)
    assert step.refusal.code == "A_DESCRIPTION_REQUIRED" and not step.ready
    assert main(["check", str(root), "--output", str(out), "--masks", str(masks)]) == 1
    printed = capsys.readouterr().out
    assert "morphobricks.toml" in printed and NFC_TEXT not in printed
    assert list(out.iterdir()) == []  # nothing is read or written
