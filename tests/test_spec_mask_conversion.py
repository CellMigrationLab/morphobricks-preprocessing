"""Spec ADR 0005 step 2b: ``RunInputs.binary_to_instances`` (BM12), fixture F11 (a)-(o).

On described ``v08`` runs (``TumorRegion`` declared ``binary``, ``Nuclei`` ``instances``): the
refusals before any read or write, the output path in the type-folder layout, the lineage line
(exact keys, ``root: "masks"``, the canonical-header digest), collision refusals, idempotent
retries, the reader-consistent publication (failures, pending lines) and mask discovery ignoring
the lineage and temporary files.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import numpy as np
import pytest
import tifffile

from conversion_tree import (
    APART,
    NUCLEI,
    blobs,
    discovered,
    expected_line,
    fail_replace,
    file,
    independent_digest,
    lines,
    no_reads,
    run_with,
    tree,
    unchecked,
    write,
)
from morphobricks_preprocessing import (
    MaskRequest,
    MaskUncheckedError,
    Request,
    RunInputs,
    UnrecordedFilesError,
)
from morphobricks_preprocessing.mask_lineage import (
    LINEAGE_FILENAME,
    TEMP_SUFFIX,
    content_sha256,
    conversion_outputs,
    read_lineage,
)

# --- F11: binary -> instances -------------------------------------------------------------


def test_f11a_binary_to_instances_writes_the_labels_and_the_exact_lineage_line(tmp_path):
    run, image, masks = run_with(tmp_path)
    result = run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    (output,) = result.outputs
    assert output.published and output.class_value is None
    assert result.representation == "instances" and result.note == ""
    relative = f"TumorObjects/{image}"
    assert output.path == relative
    written = tifffile.imread(masks / relative)
    assert written.dtype == np.int32
    resolved = run.resolve_request(
        Request((), (MaskRequest("TumorRegion", binary_mask_policy="connected_components"),))
    )
    (selection,) = resolved.require()
    np.testing.assert_array_equal(written, run.read_objects(selection, "TumorRegion", 0, 0))
    assert sorted(np.unique(written)) == [0, 1, 2] and written[1, 1] == 1
    line = json.loads(lines(masks, "TumorObjects"))
    assert tuple(line) == (
        "source", "output", "operation", "connectivity", "class_value", "helper", "helper_version"
    )  # fmt: skip
    expected = expected_line(
        file("TumorRegion", f"tumor_region/{image}", "YX", APART),
        file("TumorObjects", relative, "YX", written),
        "binary_to_instances",
    )
    assert line == expected
    assert tuple(line["source"]) == tuple(expected["source"])
    assert lines(masks, "TumorObjects") == json.dumps(expected) + "\n"
    assert output.record == expected
    assert line["output"]["dtype"] == "<i4" and line["source"]["dtype"] == "|u1"


def test_f11b_the_same_call_again_is_a_no_op(tmp_path):
    run, image, masks = run_with(tmp_path)
    run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    before = tree(masks)
    again = run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    assert tree(masks) == before
    assert not again.outputs[0].published
    assert again.outputs[0].line == lines(masks, "TumorObjects")


def test_f11c_an_undeclared_mask_id_is_refused_naming_the_declared_ids(tmp_path, monkeypatch):
    run, image, masks = run_with(tmp_path)
    before = tree(masks)
    no_reads(monkeypatch)
    with pytest.raises(ValueError) as caught:
        run.binary_to_instances(image, "Tumour", output_mask_id="TumorObjects")
    assert str(caught.value) == (
        "'Tumour' is not a mask of this run's description; it declares 'Nuclei', 'TumorRegion'"
    )
    assert tree(masks) == before


def test_f11d_an_instances_mask_is_refused(tmp_path, monkeypatch):
    run, image, masks = run_with(tmp_path)
    before = tree(masks)
    no_reads(monkeypatch)
    with pytest.raises(ValueError) as caught:
        run.binary_to_instances(image, "Nuclei", output_mask_id="TumorObjects")
    assert str(caught.value) == (
        f"'Nuclei' (nuclei/{image}) is declared instances; binary_to_instances converts a mask "
        "declared binary (an instances mask holds objects already, and a parent_labels mask is "
        "never split into objects)"
    )
    assert tree(masks) == before


def test_f11e_a_source_not_yet_checked_is_a_mask_unchecked(tmp_path, monkeypatch):
    run, image, masks = run_with(tmp_path, mask_step=False)
    before = tree(masks)
    no_reads(monkeypatch)
    with pytest.raises(MaskUncheckedError) as caught:
        run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    assert caught.value.code == "A_MASK_UNCHECKED"
    assert str(caught.value) == unchecked(image)
    assert tree(masks) == before


def test_f11f_a_source_changed_since_its_check_is_a_mask_unchecked(tmp_path, monkeypatch):
    run, image, masks = run_with(tmp_path)
    other = blobs((0, 4, 0, 4))
    write(masks / "tumor_region" / image, other, "YX")
    os.utime(masks / "tumor_region" / image, ns=(1, 1))
    before = tree(masks)
    no_reads(monkeypatch)
    with pytest.raises(MaskUncheckedError) as caught:
        run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    assert str(caught.value) == unchecked(image)
    assert tree(masks) == before


def test_f11g_a_file_with_two_z_planes_is_refused(tmp_path, monkeypatch):
    # The run analyses 2-D planes only (z_stack_not_supported), so no run records a Z-stack
    # mask today: the recorded header is stubbed as a binary ZYX file with z_count = 2.
    run, image, masks = run_with(tmp_path)
    real = RunInputs._recorded_mask

    def recorded(self, fov, mask_type, z, t):
        mask, folder = real(self, fov, mask_type, z, t)
        return dataclasses.replace(mask, axes="ZYX", shape=(2, 8, 8)), folder

    monkeypatch.setattr(RunInputs, "_recorded_mask", recorded)
    before = tree(masks)
    no_reads(monkeypatch)
    with pytest.raises(ValueError) as caught:
        run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    assert str(caught.value) == (
        f"'TumorRegion' (tumor_region/{image}) has 2 Z planes; binary_to_instances labels 2-D "
        "planes only, and a Z-stack is not converted"
    )
    assert tree(masks) == before


def test_f11h_a_declared_output_id_and_a_foreign_output_file_are_refused(tmp_path):
    run, image, masks = run_with(tmp_path)
    before = tree(masks)
    with pytest.raises(ValueError) as caught:
        run.binary_to_instances(image, "TumorRegion", output_mask_id="Nuclei")
    assert str(caught.value) == (
        "The output mask ID 'Nuclei' is declared in the description already; choose another "
        "ID: an output never replaces a declared mask"
    )
    write(masks / "TumorObjects" / image, NUCLEI, "YX")  # written by someone else
    before = tree(masks)
    with pytest.raises(ValueError) as caught:
        run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    assert str(caught.value) == (
        "The masks folder holds masks of the type 'TumorObjects' that this conversion did not "
        f"write (TumorObjects/{image}); choose another output mask ID: nothing is overwritten"
    )
    assert tree(masks) == before


@pytest.mark.parametrize("bad", ["in_cells", "Cell", "2nd", "a__b", " Tumor", "nan"])
def test_f11h_an_output_id_breaking_the_mask_id_rules_is_refused(tmp_path, bad):
    run, image, masks = run_with(tmp_path)
    before = tree(masks)
    with pytest.raises(ValueError, match="breaks the mask ID rules of experiment-v1 section 3.2"):
        run.binary_to_instances(image, "TumorRegion", output_mask_id=bad)
    assert tree(masks) == before


def test_f11i_a_failed_lineage_write_leaves_nothing_and_the_retry_gives_a(tmp_path, monkeypatch):
    reference, image, ref_masks = run_with(tmp_path / "a")
    expected = reference.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    run, image, masks = run_with(tmp_path / "b")
    before = tree(masks)
    with monkeypatch.context() as patch:
        fail_replace(patch, LINEAGE_FILENAME)
        with pytest.raises(OSError, match="injected"):
            run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    assert tree(masks) == before  # no output, no line, no temporary file, no folder left
    assert not (masks / "TumorObjects").exists()
    again = run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    assert again.outputs[0].line == expected.outputs[0].line
    np.testing.assert_array_equal(
        tifffile.imread(masks / again.outputs[0].path),
        tifffile.imread(ref_masks / expected.outputs[0].path),
    )


def test_f11i_a_failed_rename_removes_the_line_again(tmp_path, monkeypatch):
    run, image, masks = run_with(tmp_path)
    run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    before = tree(masks)
    target = Path(image).with_suffix(".tif").name
    with monkeypatch.context() as patch:
        fail_replace(patch, target)
        with pytest.raises(OSError, match="injected"):
            run.classes_to_binary(image, "Nuclei", output_prefix="Seg")
    assert tree(masks) == before
    assert not list(masks.rglob(f"*{TEMP_SUFFIX}"))


def test_f11i_a_pending_line_is_ignored_then_completed_by_the_next_call(tmp_path):
    run, image, masks = run_with(tmp_path)
    first = run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    line = lines(masks, "TumorObjects")
    (masks / first.outputs[0].path).unlink()  # as if the process died between (2) and (3)
    stale = masks / "TumorObjects" / Path(image).parent / f".{Path(image).name}.x{TEMP_SUFFIX}"
    stale.write_bytes(b"partial")
    found, issues = discovered(run, masks)
    assert (image, "TumorObjects") not in found and not issues
    again = run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    assert again.outputs[0].published and lines(masks, "TumorObjects") == line
    assert not stale.exists()


def test_f11j_the_digest_covers_shape_axes_and_dtype():
    pixels = np.arange(64, dtype=np.uint8)
    assert content_sha256(pixels.reshape(8, 8), axes="YX") != content_sha256(
        pixels.reshape(4, 16), axes="YX"
    )
    cube = pixels.reshape(1, 8, 8)
    assert content_sha256(cube, axes="TYX") != content_sha256(cube, axes="ZYX")
    assert content_sha256(cube, axes="TYX") == independent_digest(cube, "TYX")
    big = pixels.astype(">u2").reshape(8, 8)
    assert content_sha256(big, axes="YX") == content_sha256(big.astype("<u2"), axes="YX")


def test_f11k_another_source_with_identical_pixels_is_a_collision(tmp_path):
    run, image, masks = run_with(tmp_path, extra={"TumorCopy": APART})
    run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    before = tree(masks)
    with pytest.raises(ValueError) as caught:
        run.binary_to_instances(image, "TumorCopy", output_mask_id="TumorObjects")
    assert str(caught.value) == (
        f"TumorObjects/{image} exists in the masks folder and is not this conversion's output "
        "(no lineage line names this source, output and helper with the file as it is); "
        "choose another output mask ID: nothing is overwritten"
    )
    assert tree(masks) == before


def test_f11l_a_label_image_inside_an_ome_zarr_is_refused_before_any_read(tmp_path, monkeypatch):
    run, image, masks = run_with(tmp_path)
    recorded = dict(run._mask_entry(image, "tumor_region"))
    recorded.update(source="ome-zarr", path=f"{image}/labels/tumor_region")
    monkeypatch.setattr(RunInputs, "_mask_entry", lambda self, i, t: recorded)
    before = tree(masks)
    no_reads(monkeypatch)
    with pytest.raises(ValueError) as caught:
        run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    assert str(caught.value) == (
        f"'TumorRegion' for {image} is a label image inside an OME-Zarr; the conversion helpers "
        "convert masks in the masks folder only"
    )
    assert tree(masks) == before


def test_f11m_a_masks_folder_outside_the_experiment_folder(tmp_path, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "masks"
    run, image, masks = run_with(tmp_path, masks=elsewhere)
    result = run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    assert (elsewhere / result.outputs[0].path).is_file()
    line = result.outputs[0].record
    assert line["source"]["root"] == line["output"]["root"] == "masks"
    assert line["source"]["path"] == f"tumor_region/{image}"
    assert line["output"]["path"] == f"TumorObjects/{image}"


def test_f11n_a_type_suffix_source_gives_the_type_folder_output(tmp_path):
    run_a, image, masks_a = run_with(tmp_path / "a")
    a = run_a.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    run, image, masks = run_with(tmp_path / "n", suffix_layout=True)
    n = run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    assert n.outputs[0].path == f"TumorObjects/{image}"
    found, _ = discovered(run, masks)
    assert (image, "TumorObjects") in found
    np.testing.assert_array_equal(
        tifffile.imread(masks / n.outputs[0].path), tifffile.imread(masks_a / a.outputs[0].path)
    )
    left, right = a.outputs[0].record, n.outputs[0].record
    assert right["source"]["path"] == image.replace(".tif", "_tumor_region.tif")
    right["source"]["path"] = left["source"]["path"]
    assert left == right


def test_f11o_the_lineage_reader_refuses_another_root(tmp_path):
    folder = tmp_path / "TumorObjects"
    folder.mkdir()
    line = expected_line(
        file("TumorRegion", "tumor_region/a.tif", "YX", APART),
        file("TumorObjects", "TumorObjects/a.tif", "YX", APART.astype(np.int32)),
        "binary_to_instances",
    )
    line["source"]["root"] = "experiment"
    (folder / LINEAGE_FILENAME).write_text(json.dumps(line) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="has the source root 'experiment'"):
        read_lineage(folder)


def test_discovery_ignores_the_lineage_file_and_temporary_files(tmp_path):
    run, image, masks = run_with(tmp_path)
    run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    temporary = masks / "TumorObjects" / Path(image).parent / f".a.tif.123{TEMP_SUFFIX}"
    temporary.write_bytes(b"x")
    found, issues = discovered(run, masks)
    assert (image, "TumorObjects") in found
    assert not issues, [(i.code, i.paths) for i in issues]


def test_the_run_still_opens_after_a_conversion_but_not_with_other_new_masks(tmp_path):
    # The outputs of a type the run did not record carry their published lineage line: they are
    # not "unrecorded masks" of the run (no description of it declares them), so a retry can
    # open the run again. Any other new mask still refuses the run.
    run, image, masks = run_with(tmp_path)
    run.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
    again = RunInputs.open(run.output_folder)
    assert (
        not again.binary_to_instances(image, "TumorRegion", output_mask_id="TumorObjects")
        .outputs[0]
        .published
    )
    write(masks / "Other" / image, APART, "YX")
    with pytest.raises(UnrecordedFilesError, match="Other/"):
        RunInputs.open(run.output_folder)
    relative = f"TumorObjects/{image}"
    assert conversion_outputs(masks, [relative], set()) == {relative}
    # Once the run records the type, a new file of it is unrecorded like any other.
    assert conversion_outputs(masks, [relative], {"tumorobjects"}) == set()
