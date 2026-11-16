"""RunInputs.objects_read_plan: the memory of an ``objects`` read, from the header (issue #316).

``read_objects`` returns ``int32`` labels whatever the stored pixel type, so the estimate of an
objects read counts 4 bytes per pixel (a ``uint8`` mask: 4 times its stored size), plus the
working memory of ``label_binary`` for a ``binary`` mask converted by ``connected_components``.
The region-read estimate (``mask_window_plan``) is unchanged: the stored pixel type.
"""

from __future__ import annotations

import tracemalloc

import numpy as np
import pytest
import tifffile

import morphobricks_preprocessing.run_inputs_mask as mask_reads
from morphobricks_preprocessing import MaskRequest, ObjectsReadPlan, Request, RunInputsError
from morphobricks_preprocessing.masks.binary import label_binary, labelling_bytes
from test_spec_binary_masks import APART, NUCLEI, _objects, _resolve, _v08

PIXELS = 8 * 8


def _plan(tmp_path, nuclei, mask_id, policy):
    out, masks, found, _ = _v08(tmp_path, APART, nuclei=nuclei)
    inputs, resolved = _resolve(out, _objects(policy, mask_id))
    (selection,) = resolved.require()
    return inputs, selection, found[0], masks


@pytest.mark.parametrize("dtype", [np.uint8, np.uint16, np.int32])
def test_an_instances_objects_read_counts_int32_bytes_whatever_the_stored_type(tmp_path, dtype):
    inputs, selection, image, _ = _plan(tmp_path, NUCLEI.astype(dtype), "Nuclei", None)
    plan = inputs.objects_read_plan(selection, "Nuclei")
    stored = PIXELS * np.dtype(dtype).itemsize
    assert plan == ObjectsReadPlan(
        shape=(8, 8),
        stored_dtype=np.dtype(dtype),
        dtype=np.dtype(np.int32),
        conversion=None,
        stored_bytes=stored,
        result_bytes=4 * PIXELS,
        conversion_bytes=0,
        memory_estimate=4 * PIXELS,
    )
    if dtype is np.uint8:
        assert plan.memory_estimate == 4 * stored  # the acceptance of #316
    objects = inputs.read_objects(selection, "Nuclei", 0, 0)
    assert (objects.dtype, objects.shape, objects.nbytes) == (plan.dtype, plan.shape, 4 * PIXELS)
    # The region read of the same mask still counts its stored bytes (pinned on main 37c583e).
    region = inputs.mask_window_plan(image, "nuclei", bounds=(0, 8, 0, 8))
    expected = {np.uint8: 192, np.uint16: 384, np.int32: 768}[dtype]
    assert (region.dtype, region.memory_estimate) == (np.dtype(dtype), expected)


def test_a_converted_binary_objects_read_adds_the_labelling_memory(tmp_path):
    inputs, selection, image, _ = _plan(tmp_path, NUCLEI, "TumorRegion", "connected_components")
    plan = inputs.objects_read_plan(selection, "TumorRegion")
    work = labelling_bytes((8, 8), np.dtype(np.uint8))
    assert (plan.stored_dtype, plan.dtype, plan.conversion) == (
        np.dtype(np.uint8),
        np.dtype(np.int32),
        "connected_components",
    )
    assert (plan.stored_bytes, plan.result_bytes) == (PIXELS, 4 * PIXELS)
    assert plan.conversion_bytes == work > 0
    assert plan.memory_estimate == 4 * PIXELS + work
    # The same mask as a region: its stored uint8 bytes, unchanged.
    region = inputs.mask_window_plan(image, "tumor_region", bounds=(0, 8, 0, 8))
    assert (region.dtype, region.memory_estimate) == (np.dtype(np.uint8), 192)


def test_the_plan_reads_no_pixel_and_refuses_as_read_objects(tmp_path, monkeypatch):
    inputs, selection, image, masks = _plan(tmp_path, NUCLEI, "Nuclei", None)
    reads = []
    monkeypatch.setattr(mask_reads, "read_mask_plane", lambda *a, **k: reads.append(a))
    inputs.objects_read_plan(selection, "Nuclei")
    assert reads == []
    with pytest.raises(ValueError, match="'TumorRegion' has no objects use in this selection"):
        inputs.objects_read_plan(selection, "TumorRegion")
    tifffile.imwrite(masks / "nuclei" / image, NUCLEI + 1, metadata={"axes": "YX"})
    with pytest.raises(RunInputsError):
        inputs.objects_read_plan(selection, "Nuclei")


def test_a_region_use_has_no_objects_plan(tmp_path):
    out, _, _, _ = _v08(tmp_path, APART)
    inputs, resolved = _resolve(out, Request((), (MaskRequest("TumorRegion", role="region"),)))
    (selection,) = resolved.require()
    with pytest.raises(ValueError, match="has no objects use"):
        inputs.objects_read_plan(selection, "TumorRegion")


def _isolated_pixels(shape, dtype):
    plane = np.zeros(shape, dtype)
    plane[::2, ::2] = 1  # the most components 8-connectivity allows: one per 2 x 2 block
    return plane


def _random(shape, dtype):
    return (np.random.default_rng(0).random(shape) > 0.5).astype(dtype)


@pytest.mark.parametrize("dtype", [np.bool_, np.uint8, np.uint16, np.int32, np.uint64])
@pytest.mark.parametrize("make", [_isolated_pixels, _random], ids=["isolated", "random"])
def test_labelling_bytes_bounds_what_label_binary_allocates_beside_its_result(make, dtype):
    shape = (300, 360)
    plane = make(shape, dtype)
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        labels = label_binary(plane)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak - labels.nbytes <= labelling_bytes(shape, np.dtype(dtype))
