"""Spec ADR 0005 ("one policy for binary masks"), step 2: preprocessing's parts.

The ADR's conformance fixtures are to-do in the Spec (they need pixels), so they are written here
on described ``v08`` runs (``TumorRegion`` declared ``binary``, ``Nuclei`` ``instances``):

- F01, F02: 8-connected labelling by ``RunInputs.read_objects`` and the resolver's record
  ``Provenance.mask_conversions`` (resolver contract version 3);
- F07: the in-memory object-set refusal ``A_MASK_NOT_INSTANCES`` (``masks.check_object_set``),
  array and non-array branches;
- F08c (the resolver's part): one entry per use, repeated entries collapsed, the two-conversions
  ``ValueError``; coverage and refusals once per mask ID;
- F09: a conversion on a Z-stack is ``A_VOLUME_NOT_SUPPORTED``;
- F10: strict binary at the mask step (BM11): {0, v} over the whole file, integers or booleans;
- every message equals its one constructor's output, and occurs in the source only there.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import numpy as np
import pytest
import tifffile

import morphobricks_preprocessing.run_inputs_mask as mask_reads
from described_tree import run_described
from description_tree import MAPPING, build_experiment
from morphobricks_preprocessing import (
    Capabilities,
    MaskContradictsRepresentationError,
    MaskConversion,
    MaskNotInstancesError,
    MaskRequest,
    MaskUncheckedError,
    Request,
    RunInputs,
    VolumeNotSupportedError,
    prefill_mapping,
)
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.mask_representation import read_checked
from morphobricks_preprocessing.mask_rules import (
    check_binary_values,
    refuse_binary_values,
    refuse_not_instances,
)
from morphobricks_preprocessing.masks import check_object_set
from morphobricks_preprocessing.masks.binary import CONNECTIVITY
from morphobricks_preprocessing.run.mask_step import describe_masks, prepare_mask_step
from morphobricks_preprocessing.run_inputs_request import LABELLER_CONNECTIVITY
from spec_pin import fixtures

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}
POLICY = "connected_components"
BAD = "R_MASK_CONTRADICTS_REPRESENTATION"
SRC = Path(__file__).resolve().parents[1] / "src" / "morphobricks_preprocessing"
RECORD = {
    "mask_id": "TumorRegion",
    "conversion": "connected_components",
    "connectivity": 8,
    "labeller": "morphobricks_preprocessing.RunInputs.read_objects",
}


def _blobs(*boxes, dtype=np.uint8, value=1):
    pixels = np.zeros((8, 8), dtype)
    for y0, y1, x0, x1 in boxes:
        pixels[y0:y1, x0:x1] = value
    return pixels


CORNER = _blobs((1, 3, 1, 3), (3, 5, 3, 5))  # F01: two 2 x 2 blobs touching at one corner
APART = _blobs((1, 3, 1, 3), (5, 7, 5, 7))  # F02: two disconnected blobs
NUCLEI = _blobs((1, 3, 1, 3), dtype=np.uint16, value=5) + _blobs(
    (5, 7, 5, 7), dtype=np.uint16, value=9
)


def _write(path, pixels, axes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, pixels, photometric="minisblack", metadata={"axes": axes})


def _v08(tmp_path, tumor, *, images=1, t=1, mask_step=True):
    """A described ``v08`` run whose ``TumorRegion`` files hold ``tumor`` (one per image)."""
    data = VALID["v08"].data
    root, out, masks = tmp_path / "exp", tmp_path / "out", tmp_path / "masks"
    out.mkdir()
    (out / "morphobricks.toml").write_bytes(data)
    found = build_experiment(root, description_from_bytes(data), images)
    mapping = MAPPING
    if t > 1:  # a time-lapse: TCYX images, TYX masks
        mapping = prefill_mapping("TCYX")
        for image in found:
            pixels = np.random.default_rng(1).integers(0, 200, (t, 2, 8, 8), dtype=np.uint8)
            _write(root / image, pixels, "TCYX")
    nuclei = np.stack([NUCLEI] * t) if t > 1 else NUCLEI
    for image in found:
        _write(masks / "nuclei" / image, nuclei, "TYX" if t > 1 else "YX")
        _write(masks / "tumor_region" / image, tumor, "TYX" if t > 1 else "YX")
    result = run_described(root, mapping, output_folder=out, masks_folder=masks)
    assert result.written, [(i.code, i.message) for i in result.issues]
    step = describe_masks(prepare_mask_step(None, out)) if mask_step else None
    return out, masks, found, step


def _objects(policy=POLICY, mask_id="TumorRegion"):
    return Request((), (MaskRequest(mask_id, binary_mask_policy=policy),))


def _spy(monkeypatch):
    """Count the pixel reads of every ``RunInputs`` (the ADR's pre-read test (2))."""
    reads = []
    for name in ("read_plane", "read_mask", "read_mask_window", "read_objects"):
        monkeypatch.setattr(RunInputs, name, lambda *a, _n=name, **k: reads.append(_n))
    return reads


def _resolve(out, request):
    inputs = RunInputs.open(out)
    return inputs, inputs.resolve_request(request)


# --- F01, F02: one labeller, one record (BM1, BM2, BM6, BM10) -------------------------------


def test_f01_corner_touching_blobs_are_one_object_and_the_record_states_8(tmp_path):
    out, _, _, step = _v08(tmp_path, CORNER)
    assert step.issues == ()
    inputs, resolved = _resolve(out, _objects())
    (selection,) = resolved.require()
    labels = inputs.read_objects(selection, "TumorRegion", 0, 0)
    assert labels.dtype == np.int32 and labels.shape == (8, 8)
    np.testing.assert_array_equal(labels, CORNER.astype(np.int32))  # one object, label 1
    (record,) = resolved.provenance.mask_conversions
    assert record.connectivity == 8 == CONNECTIVITY == LABELLER_CONNECTIVITY


def test_f02a_a_binary_mask_as_objects_without_the_policy_is_refused(tmp_path, monkeypatch):
    out, _, _, _ = _v08(tmp_path, APART)
    reads = _spy(monkeypatch)
    _, resolved = _resolve(out, _objects(None))
    (refusal,) = resolved.refusals
    expected = refuse_not_instances("TumorRegion", declared="binary")
    assert (refusal.code, refusal.subject) == ("A_MASK_NOT_INSTANCES", "TumorRegion")
    assert refusal.message == str(expected)  # the one constructor, unchanged
    assert resolved.selections == () and resolved.provenance.mask_conversions == ()
    with pytest.raises(MaskNotInstancesError) as raised:
        resolved.require()
    assert raised.value.code == expected.code and str(raised.value) == str(expected)
    assert reads == []


def test_f02b_the_policy_gives_labels_1_and_2_and_exactly_one_record(tmp_path):
    out, _, _, _ = _v08(tmp_path, APART)
    inputs, resolved = _resolve(out, _objects())
    (selection,) = resolved.require()
    labels = inputs.read_objects(selection, "TumorRegion", t=0, z=0)
    expected = _blobs((1, 3, 1, 3), dtype=np.int32) + _blobs((5, 7, 5, 7), dtype=np.int32, value=2)
    np.testing.assert_array_equal(labels, expected)  # 1 top-left, then 2
    assert resolved.provenance.mask_conversions == (MaskConversion(**RECORD),)
    records = [c.as_record() for c in resolved.provenance.mask_conversions]
    assert json.dumps(records) == json.dumps([RECORD])  # the keys in the record's order
    # read_objects equals the labeller on the raw mask; the raw read is unchanged (0/1).
    raw = inputs.read_mask(selection.relative_path, "tumor_region", z=0, t=0)
    np.testing.assert_array_equal(raw, APART)


def test_an_instances_object_set_is_read_as_stored_and_has_no_record(tmp_path):
    out, _, _, _ = _v08(tmp_path, APART)
    inputs, resolved = _resolve(out, _objects(None, "Nuclei"))
    (selection,) = resolved.require()
    labels = inputs.read_objects(selection, "Nuclei", 0, 0)
    assert labels.dtype == np.int32
    np.testing.assert_array_equal(labels, NUCLEI)  # 5 and 9: never relabelled
    assert resolved.provenance.mask_conversions == ()


def test_read_objects_refuses_a_mask_without_an_objects_use(tmp_path):
    out, _, _, _ = _v08(tmp_path, APART)
    inputs, resolved = _resolve(out, Request((), (MaskRequest("TumorRegion", role="region"),)))
    (selection,) = resolved.require()
    with pytest.raises(ValueError, match="'TumorRegion' has no objects use in this selection"):
        inputs.read_objects(selection, "TumorRegion", 0, 0)
    forged = dataclasses.replace(
        selection, masks=(dataclasses.replace(selection.masks[0], role="objects"),)
    )
    with pytest.raises(ValueError, match="declared binary with the conversion None"):
        inputs.read_objects(forged, "TumorRegion", 0, 0)
    with pytest.raises(TypeError, match="whole numbers"):
        inputs.read_objects(selection, "TumorRegion", 0.0, 0)


def test_the_read_guard_refuses_a_plane_with_two_values_through_label_binary(tmp_path, monkeypatch):
    # F10: a stubbed read of {0, 3, 7}: the same message, from the same constructor.
    out, _, _, _ = _v08(tmp_path, APART)
    inputs, resolved = _resolve(out, _objects())
    (selection,) = resolved.require()
    plane = _blobs((1, 3, 1, 3), value=3) + _blobs((5, 7, 5, 7), value=7)
    monkeypatch.setattr(mask_reads, "read_mask_plane", lambda *a, **k: plane)
    with pytest.raises(MaskContradictsRepresentationError) as raised:
        inputs.read_objects(selection, "TumorRegion", 0, 0)
    path = f"tumor_region/{selection.relative_path}"
    assert str(raised.value) == str(refuse_binary_values(path, values=[3, 7]))
    assert raised.value.code == BAD


# --- F07: in memory, an object set is an integer instance array (BM4) ------------------------


ARRAY = (
    "A_MASK_NOT_INSTANCES: 'cells' is a {} array, not an object set. An object set is an "
    "integer instance array (0 is background, each positive value one object). A boolean array "
    "is used only as a region. To measure the connected pieces of a binary mask as objects, "
    "describe it as binary and request binary_mask_policy='connected_components' in a run."
)
NOT_ARRAY = (
    "A_MASK_NOT_INSTANCES: 'cells' is a {} (not a plain NumPy array), not an object set. An "
    "object set is an integer instance array (0 is background, each positive value one object). "
    "A boolean array is used only as a region. To measure the connected pieces of a binary mask "
    "as objects, describe it as binary and request binary_mask_policy='connected_components' in "
    "a run."
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (APART.astype(bool), ARRAY.format("bool")),
        (APART.astype(np.float32), ARRAY.format("float32")),
        ([[0, 1]], NOT_ARRAY.format("list")),
        (None, NOT_ARRAY.format("NoneType")),
        (np.ma.MaskedArray(APART.astype(np.int32)), NOT_ARRAY.format("MaskedArray")),
    ],
    ids=["bool", "float32", "list", "None", "masked"],
)
def test_f07_an_in_memory_object_set_that_is_not_integer_instances_is_refused(value, expected):
    with pytest.raises(MaskNotInstancesError) as raised:
        check_object_set(value, name="cells")
    assert raised.value.code == "A_MASK_NOT_INSTANCES"
    assert str(raised.value) == expected
    assert str(raised.value) == str(refuse_not_instances("cells", value=value))


@pytest.mark.parametrize("dtype", [np.uint8, np.uint16, np.int32, np.int64])
def test_f07_an_integer_instance_array_is_an_object_set(dtype):
    labels = APART.astype(dtype)
    assert check_object_set(labels, name="cells") is None
    np.testing.assert_array_equal(labels, APART)  # nothing changed


def test_the_constructors_take_exactly_one_subject_kind():
    with pytest.raises(ValueError, match="not both"):
        refuse_not_instances("x", declared="binary", value=np.zeros(1))
    with pytest.raises(ValueError, match="nothing to refuse"):
        refuse_not_instances("x", declared="instances")
    with pytest.raises(ValueError, match="exactly one of values and dtype"):
        refuse_binary_values("x")
    with pytest.raises(ValueError, match="exactly one of values and dtype"):
        refuse_binary_values("x", values=[1], dtype=np.float32)


# --- F08c (the resolver's part): one mask, several uses (BM2, BM5) ----------------------------

OBJECTS_CC = MaskRequest("TumorRegion", "objects", POLICY)
REGION = MaskRequest("TumorRegion", "region", None)


def test_f08c_one_mask_as_converted_objects_and_as_a_region_in_one_request(tmp_path):
    out, _, _, _ = _v08(tmp_path, APART, images=2)
    for entries in [(OBJECTS_CC, REGION), (OBJECTS_CC, REGION, OBJECTS_CC)]:  # (i) collapsed
        inputs, resolved = _resolve(out, Request((), entries))
        assert not resolved.refused, resolved.refusals
        for selection in resolved.selections:
            objects, region = selection.masks
            assert (objects.mask_id, objects.role, objects.conversion) == (
                "TumorRegion",
                "objects",
                POLICY,
            )
            assert (region.mask_id, region.role, region.conversion) == (
                "TumorRegion",
                "region",
                None,
            )
            np.testing.assert_array_equal(
                inputs.read_objects(selection, "TumorRegion", 0, 0) != 0,
                inputs.read_mask(selection.relative_path, "tumor_region", 0, 0) != 0,
            )  # labelling keeps the foreground: the objects' area is the region
        assert [c.as_record() for c in resolved.provenance.mask_conversions] == [RECORD]
        covered = [c for c in resolved.coverage if c.subject == "TumorRegion"]
        assert [(c.status, c.present, c.total) for c in covered] == [("full", 2, 2)]  # once


def test_f08c_ii_two_conversions_of_one_objects_use_are_refused(tmp_path):
    out, _, _, _ = _v08(tmp_path, APART)
    inputs = RunInputs.open(out)
    request = Request((), (OBJECTS_CC, REGION, MaskRequest("TumorRegion")))
    with pytest.raises(ValueError) as raised:
        inputs.resolve_request(request)
    assert str(raised.value) == (
        "'TumorRegion' is requested as objects with two different conversions "
        "('connected_components' and None); one run converts a mask at most one way"
    )
    reverse = Request((), (MaskRequest("TumorRegion"), OBJECTS_CC))
    with pytest.raises(ValueError, match=r"\(None and 'connected_components'\)"):
        inputs.resolve_request(reverse)  # a and b in request order


def test_f08c_the_mask_refusals_are_computed_once_per_mask_id(tmp_path):
    out, _, _, _ = _v08(tmp_path, APART, images=2, mask_step=False)
    entries = (OBJECTS_CC, REGION, OBJECTS_CC, REGION)
    _, resolved = _resolve(out, Request((), entries))
    assert [(r.code, r.subject) for r in resolved.refusals] == [("A_MASK_UNCHECKED", "TumorRegion")]
    assert resolved.provenance.mask_conversions == ()  # refused: nothing selected
    covered = [c for c in resolved.coverage if c.subject == "TumorRegion"]
    assert len(covered) == 1


def test_f08c_a_policy_on_a_region_use_stays_a_malformed_request(tmp_path):
    out, _, _, _ = _v08(tmp_path, APART)
    request = Request((), (OBJECTS_CC, MaskRequest("TumorRegion", "region", POLICY)))
    with pytest.raises(ValueError, match="applies to a binary mask used as objects"):
        RunInputs.open(out).resolve_request(request)


# --- F09: no conversion of a Z-stack (BM5) ----------------------------------------------------


def _z_stack(monkeypatch, out, images=1):
    """Give the first position two Z planes (preprocessing runs are 2-D: a stub, the resolver
    reads only ``Fov.z_count``)."""
    real = RunInputs.fovs

    def fovs(self):
        found = real(self)
        return (dataclasses.replace(found[0], z_count=2), *found[1:])

    monkeypatch.setattr(RunInputs, "fovs", fovs)
    return RunInputs.open(out).fovs()[0].relative_path


def _z_message(path):
    return (
        f"'TumorRegion' would be converted by connected_components, which labels 2-D planes only; "
        f"the images of {path} have 2 Z planes. Converting a Z-stack is not supported "
        "(A_VOLUME_NOT_SUPPORTED)."
    )


@pytest.mark.parametrize(
    "request_",
    [
        _objects(),
        Request((), (OBJECTS_CC,), planes="volume", capabilities=Capabilities(volume_support=True)),
    ],
    ids=["plane", "volume_with_support"],
)
def test_f09_a_conversion_on_a_z_stack_is_refused(tmp_path, monkeypatch, request_):
    out, _, _, _ = _v08(tmp_path, APART, images=2)
    stack = _z_stack(monkeypatch, out)
    reads = _spy(monkeypatch)
    _, resolved = _resolve(out, request_)
    assert [(r.code, r.subject, r.files) for r in resolved.refusals] == [
        ("A_VOLUME_NOT_SUPPORTED", "TumorRegion", (stack,))
    ]
    assert resolved.refusals[0].message == _z_message(stack)
    assert resolved.selections == ()
    with pytest.raises(VolumeNotSupportedError) as raised:
        resolved.require()
    assert raised.value.code == "A_VOLUME_NOT_SUPPORTED"
    assert str(raised.value) == _z_message(stack) and reads == []


def test_f09_a_volume_without_support_and_a_conversion_are_both_refused(tmp_path, monkeypatch):
    out, _, _, _ = _v08(tmp_path, APART)
    stack = _z_stack(monkeypatch, out)
    _, resolved = _resolve(out, Request((), (OBJECTS_CC,), planes="volume"))
    assert [(r.code, r.subject) for r in resolved.refusals] == [
        ("A_VOLUME_NOT_SUPPORTED", "volume"),
        ("A_VOLUME_NOT_SUPPORTED", "TumorRegion"),
    ]
    assert resolved.refusals[1].message == _z_message(stack)


def test_f09_a_region_on_a_z_stack_and_a_conversion_on_2d_planes_are_accepted(
    tmp_path, monkeypatch
):
    out, _, _, _ = _v08(tmp_path, APART, images=2)
    _, accepted = _resolve(out, _objects())
    assert not accepted.refused  # every position has one Z plane
    _z_stack(monkeypatch, out)
    _, region = _resolve(out, Request((), (REGION,)))
    assert not region.refused
    assert len(region.selections[0].planes) == 2  # both slices, read raw


# --- F10: strict binary at the mask step (BM11, experiment-v1 4.5) ---------------------------


def _int16(values):
    return np.resize(np.array(values, dtype=np.int16), (8, 8))


REFUSED = {
    "a_0_3_7": (_blobs((1, 3, 1, 3), value=3) + _blobs((5, 7, 5, 7), value=7), [3, 7]),
    "b_3_7": (np.where(APART == 1, 3, 7).astype(np.uint8), [3, 7]),
    "c_0_minus_1": (_int16([0, -1]), [-1]),
}


def _status(out, path):
    files = read_checked(out)["TumorRegion"]["files"]
    return "checked" if path in {f["path"] for f in files} else "not yet checked"


def _assert_refused_then_unchecked(out, step, path, expected, monkeypatch):
    (issue,) = step.issues
    assert (issue.code, issue.message, issue.paths, issue.action) == (
        BAD,
        str(expected),
        (path,),
        "",
    )
    assert _status(out, path) == "not yet checked"
    assert step.report is not None and step.path.is_file()  # the report is written all the same
    reads = _spy(monkeypatch)
    _, resolved = _resolve(out, _objects())
    assert [(r.code, r.subject) for r in resolved.refusals] == [("A_MASK_UNCHECKED", "TumorRegion")]
    with pytest.raises(MaskUncheckedError):
        resolved.require()
    assert reads == []


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_f10_a_binary_file_with_a_negative_or_two_positive_values_is_refused(
    tmp_path, monkeypatch, case
):
    pixels, values = REFUSED[case]
    out, _, (image,), step = _v08(tmp_path, pixels)
    path = f"tumor_region/{image}"
    expected = refuse_binary_values(path, values=values)
    _assert_refused_then_unchecked(out, step, path, expected, monkeypatch)
    report = json.loads(step.path.read_text(encoding="utf-8"))
    assert path not in json.dumps(report)  # left out of the scan; Nuclei still described


def test_f10h_the_check_is_file_wide_over_every_plane(tmp_path, monkeypatch):
    planes = [_blobs((1, 3, 1, 3), value=3), _blobs((5, 7, 5, 7), value=7)]
    for plane in planes:  # each plane alone would pass
        check_binary_values(np.unique(plane), dtype=plane.dtype, path="one plane")
    out, _, (image,), step = _v08(tmp_path, np.stack(planes), t=2)
    path = f"tumor_region/{image}"
    expected = refuse_binary_values(path, values=[3, 7])
    _assert_refused_then_unchecked(out, step, path, expected, monkeypatch)


def test_f10i_a_float_file_is_refused_by_its_pixel_type_even_with_whole_values(tmp_path):
    out, masks, (image,), step = _v08(tmp_path, APART)
    assert step.issues == ()
    path = f"tumor_region/{image}"
    _write(masks / path, APART.astype(np.float32), "YX")  # 0.0 and 1.0 only
    replaced = describe_masks(prepare_mask_step(None, out))
    (issue,) = replaced.issues
    expected = refuse_binary_values(path, dtype=np.float32)
    assert (issue.code, issue.message, issue.action) == (BAD, str(expected), "")
    assert "has pixel type float32;" in issue.message
    assert _status(out, path) == "not yet checked"
    # The run itself refuses a float mask from its header, whatever its representation.
    again = run_described(None, MAPPING, output_folder=out, masks_folder=masks)
    assert not again.written and "mask_pixel_type" in {i.code for i in again.issues}


def test_f10i_a_float_instances_mask_still_stops_the_step_with_its_header_error(tmp_path):
    out, masks, (image,), _ = _v08(tmp_path, APART)
    _write(masks / "nuclei" / image, NUCLEI.astype(np.float32), "YX")
    step = prepare_mask_step(None, out)
    assert not step.ready
    assert "mask_pixel_type" in {i.code for i in step.issues}


ACCEPTED = {
    "d_all_zero": (np.zeros((8, 8), np.uint8), np.zeros((8, 8), np.int32)),
    "e_all_255": (np.full((8, 8), 255, np.uint8), np.ones((8, 8), np.int32)),
    "f_0_255": (
        APART * 255,
        _blobs((1, 3, 1, 3), dtype=np.int32) + _blobs((5, 7, 5, 7), dtype=np.int32, value=2),
    ),
    "g_bool": (
        APART.astype(bool),
        _blobs((1, 3, 1, 3), dtype=np.int32) + _blobs((5, 7, 5, 7), dtype=np.int32, value=2),
    ),
}


@pytest.mark.parametrize("case", sorted(ACCEPTED))
def test_f10_all_zero_all_foreground_and_one_value_files_are_checked(tmp_path, case):
    pixels, objects = ACCEPTED[case]
    out, _, (image,), step = _v08(tmp_path, pixels)
    assert step.issues == ()
    assert _status(out, f"tumor_region/{image}") == "checked"
    inputs, resolved = _resolve(out, _objects())
    (selection,) = resolved.require()
    np.testing.assert_array_equal(inputs.read_objects(selection, "TumorRegion", 0, 0), objects)
    region = inputs.read_mask(selection.relative_path, "tumor_region", 0, 0) != 0
    assert region.sum() == (pixels != 0).sum()  # (d): an empty region, not an error


def test_f10_a_time_lapse_with_one_value_over_every_plane_is_checked(tmp_path):
    out, _, (image,), step = _v08(tmp_path, np.stack([APART * 4, CORNER * 4]), t=2)
    assert step.issues == ()
    assert _status(out, f"tumor_region/{image}") == "checked"
    inputs, resolved = _resolve(out, _objects())
    (selection,) = resolved.require()
    assert int(inputs.read_objects(selection, "TumorRegion", t=0, z=0).max()) == 2
    assert int(inputs.read_objects(selection, "TumorRegion", t=1, z=0).max()) == 1


# --- construction: each message only in its constructor (ADR 0005 test (4)) ------------------

FIXED_WORDS = {
    "is declared binary but holds the values": "mask_rules.py",
    "is declared binary but has pixel type": "mask_rules.py",
    "An object set is an integer instance array": "mask_rules.py",
    "(not a plain NumPy array)": "mask_rules.py",
    "is declared parent_labels: each non-zero value is one": "mask_rules.py",
    "so it has no object identity and cannot": "mask_rules.py",
    "2-D planes only; the images of": "run_inputs_request.py",
    "is requested as objects with two different conversions": "mask_requests.py",
    "older than the binary value rule": "mask_rules.py",
}


@pytest.mark.parametrize("words", sorted(FIXED_WORDS))
def test_each_message_occurs_only_in_its_constructor(words):
    found = sorted(
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if words in path.read_text(encoding="utf-8")
    )
    assert found == [FIXED_WORDS[words]]
