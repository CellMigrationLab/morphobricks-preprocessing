"""Conformance P21, P26 (recorded header names) and P29: discovery before any description.

Spec experiment-v1 §3.3 and §6, conformance plan section 4. The refusals apply only when a
description is in use (owner decision Q1): a run without one accepts what v0.4 accepted.
P26's comparison of header names with a description's confirmed names (``R_HEADER_CONTRADICTION``)
belongs to the physical checks (Stage 1, PR 8); here the names are recorded as read, untrimmed.
"""

import numpy as np
import tifffile

from morphobricks_preprocessing import (
    prefill_mapping,
    preflight,
    read_manifest,
    run_experiment,
)
from morphobricks_preprocessing.discovery import discover_experiment
from morphobricks_preprocessing.metadata import inspect_tiff
from tiff_tree import write_data, write_tiff

DECOMPOSED = "Café"
COMPOSED = "Café"


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
    pre = preflight(root, with_description=True)
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
    assert "R_FOLDER_NOT_NFC" not in codes(preflight(root, with_description=True).issues)


def test_p21_without_a_description_discovery_is_as_in_v0_4(tmp_path):
    root = tmp_path / "exp"
    write_tiff(root / DECOMPOSED / "rep1" / "a.tif")
    result = discover_experiment(root)
    assert "R_FOLDER_NOT_NFC" not in codes(result.issues)
    assert not any(i.is_error for i in result.issues)
    assert result.leaves[0].condition == DECOMPOSED


def test_p21_preflight_carries_the_refusal(tmp_path):
    root = tmp_path / "exp"
    write_tiff(root / DECOMPOSED / "rep1" / "a.tif")
    assert "R_FOLDER_NOT_NFC" in codes(preflight(root, with_description=True).issues)
    assert "R_FOLDER_NOT_NFC" not in codes(preflight(root).issues)


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
    run_experiment(root, prefill_mapping("CYX"), output_folder=out)
    entry = read_manifest(out / "run_manifest.json").files[0]
    assert entry.raw_channel_names == (" DAPI", f"{DECOMPOSED} 488")
    assert entry.channel_names == ("DAPI", f"{DECOMPOSED} 488")
    assert entry.channel_names_trimmed


# P29 ----------------------------------------------------------------------------------


def test_p29_na_and_a_leading_space_are_invalid_mask_type_names(tmp_path):
    root, masks = masks_tree(tmp_path, "NA", " cells", "Nuclei")
    pre = preflight(root, masks_folder=masks, with_description=True)
    found = issue(pre.masks.issues, "R_MASK_TYPE_NAME_INVALID")
    assert found.is_error
    assert set(found.paths) == {"NA", " cells"}
    assert "R_FOLDER_NOT_NFC" not in codes(pre.masks.issues)
    assert not pre.ok
    # Never renamed: the types are as discovered.
    assert set(pre.masks.types) == {"NA", " cells", "Nuclei"}


def test_p29_a_decomposed_mask_type_is_not_nfc(tmp_path):
    root, masks = masks_tree(tmp_path, DECOMPOSED, "Nuclei")
    pre = preflight(root, masks_folder=masks, with_description=True)
    found = issue(pre.masks.issues, "R_FOLDER_NOT_NFC")
    assert found.is_error and found.paths == (DECOMPOSED,)
    assert "R_MASK_TYPE_NAME_INVALID" not in codes(pre.masks.issues)
    assert DECOMPOSED in pre.masks.types


def test_p29_the_three_cases_together(tmp_path):
    root, masks = masks_tree(tmp_path, "NA", " cells", DECOMPOSED)
    found = preflight(root, masks_folder=masks, with_description=True).masks.issues
    assert issue(found, "R_MASK_TYPE_NAME_INVALID").paths == (" cells", "NA")
    assert issue(found, "R_FOLDER_NOT_NFC").paths == (DECOMPOSED,)


def test_p29_valid_mask_types_are_accepted(tmp_path):
    root, masks = masks_tree(tmp_path, COMPOSED, "Nuclei")
    pre = preflight(root, masks_folder=masks, with_description=True)
    assert not {"R_FOLDER_NOT_NFC", "R_MASK_TYPE_NAME_INVALID"} & set(codes(pre.masks.issues))


def test_p29_without_a_description_mask_types_are_accepted_as_in_v0_4(tmp_path):
    root, masks = masks_tree(tmp_path, "NA", " cells", DECOMPOSED)
    pre = preflight(root, masks_folder=masks)
    assert not {"R_FOLDER_NOT_NFC", "R_MASK_TYPE_NAME_INVALID"} & set(codes(pre.masks.issues))
    assert not any(i.is_error for i in pre.masks.issues)
