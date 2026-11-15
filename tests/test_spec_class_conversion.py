"""Spec ADR 0005 step 2b: ``RunInputs.classes_to_binary`` (BM12), fixture F12 (a)-(n2).

One ``uint8`` binary output per class present, in ascending order, each with its lineage line;
the input table's refusals with their exact messages, before anything is written; an all-zero
image; a failed publication of one class and the retry that skips the classes published.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest
import tifffile

import morphobricks_preprocessing.mask_conversion as conversion
from conversion_tree import (
    CLASSES,
    expected_line,
    file,
    lines,
    no_reads,
    run_with,
    tree,
    unchecked,
    write,
)
from morphobricks_preprocessing import MaskUncheckedError
from morphobricks_preprocessing.mask_conversion import NO_CLASS_PRESENT, check_class_image
from morphobricks_preprocessing.mask_lineage import (
    LINEAGE_FILENAME,
    TEMP_SUFFIX,
)
from morphobricks_preprocessing.run.mask_step import describe_masks, prepare_mask_step

# --- F12: class labels -> one binary mask per class ---------------------------------------


def test_f12a_one_binary_output_per_class_with_its_line(tmp_path):
    run, image, masks = run_with(tmp_path, extra={"Classes": CLASSES})
    result = run.classes_to_binary(image, "Classes", output_prefix="Tissue")
    assert [o.mask_id for o in result.outputs] == ["Tissue_class1", "Tissue_class2"]
    assert result.representation == "binary" and result.note == ""
    planes = [tifffile.imread(masks / o.path) for o in result.outputs]
    assert all(p.dtype == np.uint8 for p in planes)
    assert not np.any(planes[0] & planes[1])
    np.testing.assert_array_equal(planes[0] | planes[1], (CLASSES != 0).astype(np.uint8))
    for value, output, plane in zip((1, 2), result.outputs, planes, strict=True):
        assert output.path == f"Tissue_class{value}/{image}"
        expected = expected_line(
            file("Classes", f"classes/{image}", "YX", CLASSES),
            file(f"Tissue_class{value}", output.path, "YX", plane),
            "classes_to_binary",
            value,
        )
        assert lines(masks, f"Tissue_class{value}") == json.dumps(expected) + "\n"
        np.testing.assert_array_equal(plane, (CLASSES == value).astype(np.uint8))


def test_f12b_int32_classes_0_2_5(tmp_path):
    image5 = CLASSES.astype(np.int32)
    image5[image5 == 1] = 5
    run, image, masks = run_with(tmp_path, extra={"Classes": image5})
    result = run.classes_to_binary(image, "Classes", output_prefix="Tissue")
    assert [o.mask_id for o in result.outputs] == ["Tissue_class2", "Tissue_class5"]
    assert [o.class_value for o in result.outputs] == [2, 5]


CLASS_MESSAGES = {
    "c": (
        np.full((8, 8), -1, np.int16),
        "holds negative values (smallest -1); class 0 is the background and classes are positive",
    ),
    "d": (
        np.zeros((8, 8), np.float32),
        "has pixel type float32; a class image holds non-negative integer class values",
    ),
    "e": (
        np.zeros((8, 8), bool),
        "has pixel type bool; a class image holds non-negative integer class values",
    ),
    "f": (
        np.zeros((2, 8, 8), np.uint16),
        "has 3 dimensions (shape (2, 8, 8)); a class image is one 2-D plane of class values; a "
        "one-hot or multi-channel stack is not converted",
    ),
    "g": (
        np.zeros((8, 8, 3), np.uint8),
        "has 3 dimensions (shape (8, 8, 3)); a class image is one 2-D plane of class values; a "
        "one-hot or multi-channel stack is not converted",
    ),
    "n2": (
        np.full((8, 8), 2147483648, np.int64),
        "holds class values above 2147483647 (largest 2147483648); a class value must fit an "
        "int32 label",
    ),
}


@pytest.mark.parametrize("case", sorted(CLASS_MESSAGES))
def test_f12_the_pixel_validation_messages(case):
    pixels, text = CLASS_MESSAGES[case]
    with pytest.raises(ValueError) as caught:
        check_class_image(pixels, mask_id="Classes", path="classes/a.tif")
    assert str(caught.value) == f"'Classes' (classes/a.tif) {text}"


@pytest.mark.parametrize("case", ["c", "d", "e", "g", "n2"])
def test_f12_rejected_pixels_through_the_helper_write_nothing(tmp_path, monkeypatch, case):
    # The run and the mask step refuse these files before the helper (check_labels and the
    # header checks), so the read is stubbed to return them from a checked source.
    run, image, masks = run_with(tmp_path, extra={"Classes": CLASSES})
    pixels, text = CLASS_MESSAGES[case]
    monkeypatch.setattr(conversion, "read_mask_plane", lambda *a, **k: pixels)
    before = tree(masks)
    with pytest.raises(ValueError) as caught:
        run.classes_to_binary(image, "Classes", output_prefix="Tissue")
    assert str(caught.value) == f"'Classes' (classes/{image}) {text}"
    assert tree(masks) == before


def test_f12f_a_time_stack_is_refused_from_its_header(tmp_path, monkeypatch):
    stack = np.stack([CLASSES, CLASSES])
    run, image, masks = run_with(tmp_path, t=2, extra={"Classes": stack})
    before = tree(masks)
    no_reads(monkeypatch)
    with pytest.raises(ValueError) as caught:
        run.classes_to_binary(image, "Classes", output_prefix="Tissue")
    assert str(caught.value) == f"'Classes' (classes/{image}) {CLASS_MESSAGES['f'][1]}"
    assert tree(masks) == before


def test_f12h_an_all_zero_image_gives_no_output(tmp_path):
    run, image, masks = run_with(tmp_path, extra={"Classes": np.zeros((8, 8), np.uint16)})
    before = tree(masks)
    result = run.classes_to_binary(image, "Classes", output_prefix="Tissue")
    assert result.outputs == () and result.note == NO_CLASS_PRESENT == "no class present"
    assert tree(masks) == before


def test_f12i_a_failed_class_2_then_the_retry_skips_class_1(tmp_path, monkeypatch):
    reference, image, _ = run_with(tmp_path / "a", extra={"Classes": CLASSES})
    expected = reference.classes_to_binary(image, "Classes", output_prefix="Tissue")
    run, image, masks = run_with(tmp_path / "b", extra={"Classes": CLASSES})
    real = os.replace
    calls = []

    def replace(src, dst, *args, **kwargs):
        if Path(dst).name == LINEAGE_FILENAME:
            calls.append(dst)
            if len(calls) == 2:
                raise OSError("injected")
        return real(src, dst, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", replace)
        with pytest.raises(OSError, match="injected"):
            run.classes_to_binary(image, "Classes", output_prefix="Tissue")
    assert (masks / f"Tissue_class1/{image}").is_file()
    assert lines(masks, "Tissue_class1") == expected.outputs[0].line
    assert not (masks / "Tissue_class2").exists()
    assert not list(masks.rglob(f"*{TEMP_SUFFIX}"))
    again = run.classes_to_binary(image, "Classes", output_prefix="Tissue")
    assert [o.published for o in again.outputs] == [False, True]
    assert [o.line for o in again.outputs] == [o.line for o in expected.outputs]


def test_f12k_a_binary_mask_is_refused(tmp_path, monkeypatch):
    run, image, masks = run_with(tmp_path)
    before = tree(masks)
    no_reads(monkeypatch)
    with pytest.raises(ValueError) as caught:
        run.classes_to_binary(image, "TumorRegion", output_prefix="Tissue")
    assert str(caught.value) == (
        f"'TumorRegion' (tumor_region/{image}) is declared binary: it has one class already; "
        "declare or use it as it is"
    )
    assert tree(masks) == before


def test_f12l_a_source_changed_since_its_check_is_a_mask_unchecked(tmp_path, monkeypatch):
    run, image, masks = run_with(tmp_path, extra={"Classes": CLASSES})
    write(masks / "classes" / image, CLASSES * 2, "YX")
    before = tree(masks)
    no_reads(monkeypatch)
    with pytest.raises(MaskUncheckedError) as caught:
        run.classes_to_binary(image, "Classes", output_prefix="Tissue")
    assert str(caught.value) == unchecked(image, "Classes")
    assert tree(masks) == before


def test_f12m_a_declared_output_id_is_refused_before_anything_is_written(tmp_path):
    run, image, masks = run_with(
        tmp_path,
        extra={"Classes": CLASSES},
        declared=(("Tissue_class2", "tissue_class2", "binary"),),
    )
    before = tree(masks)
    with pytest.raises(ValueError) as caught:
        run.classes_to_binary(image, "Classes", output_prefix="Tissue")
    assert str(caught.value) == (
        "The output mask ID 'Tissue_class2' is declared in the description already; choose "
        "another ID: an output never replaces a declared mask"
    )
    assert tree(masks) == before


def test_f12n1_a_class_value_above_int32_is_refused_by_the_mask_step(tmp_path, monkeypatch):
    big = np.zeros((8, 8), np.int64)
    big[0, 0] = 2147483648
    run, image, masks = run_with(tmp_path, extra={"Classes": big}, mask_step=False)
    with pytest.raises(ValueError) as refused:  # check_labels' existing message
        describe_masks(prepare_mask_step(None, run.output_folder))
    assert str(refused.value) == (
        f"the mask classes/{image} holds labels above 2147483647 (largest 2147483648); "
        "MorphoBricks stores labels as 32-bit signed numbers (int32), so relabel the objects "
        "from 1."
    )
    assert run.mask_check_status("Classes", image) == "not yet checked"
    no_reads(monkeypatch)
    with pytest.raises(MaskUncheckedError) as caught:
        run.classes_to_binary(image, "Classes", output_prefix="Tissue")
    assert str(caught.value) == unchecked(image, "Classes")
