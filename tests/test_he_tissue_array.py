"""``he_tissue_mask_array``: the H&E tissue mask of an array in memory (contract §15.4; K3).

- every input it cannot take, and an unknown rule, is refused before any computation, with
  nothing converted and no axis guessed;
- its mask is bitwise equal to the file ``RunInputs.he_tissue_mask`` writes for the same pixels
  (the synthetic image of design §7, four PI-style images, the §1 boundary colours with random
  pixels, and a 100 x 220 image whose parts sit on every threshold of the rule, issue #345), and
  its record equals the run route's lineage line in every key they share;
- on that threshold image the mask is the one its parts specify, so moving any threshold past
  the nearest 8-bit colour, or any clean-up size by one pixel, fails a test;
- the record is immutable, says ``provisional`` and states the code identities as found.

The core-only refusal (scikit-image missing) is in ``test_he_tissue_core_only.py``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json

import numpy as np
import pytest
import tifffile

pytest.importorskip("skimage")

import scipy  # noqa: E402
import skimage  # noqa: E402
from morphobricks_normalization import stain  # noqa: E402
from scipy import ndimage  # noqa: E402

from he_tissue_tree import (  # noqa: E402
    IDENTITY_PATCH,
    ON_THRESHOLD,
    PI_STYLE,
    UNIT,
    _disk,  # noqa: E402
    boundary_image,
    identities,
    pi_style,
    rgb_run,
    synthetic_he,
    threshold_image,
    unverified,
)
from morphobricks_preprocessing import (  # noqa: E402
    TissueMaskArray,
    TissueMaskArrayRecord,
    __version__,
    he_tissue_mask_array,
)
from morphobricks_preprocessing import he_tissue_mask as helper  # noqa: E402
from morphobricks_preprocessing.he_tissue_array import RECORD_KEYS  # noqa: E402
from morphobricks_preprocessing.lineage_schema import rule_sha256  # noqa: E402

SHARED = (
    "operation",
    "rule",
    "rule_sha256",
    "code",
    "numpy_version",
    "scikit_image_version",
    "scipy_version",
    "fractions",
)
"""The lineage keys that describe the computation, which the array record carries too."""


def images():
    found = {"synthetic.tif": synthetic_he()[0], "boundary.tif": boundary_image()}
    found["thresholds.tif"] = threshold_image()[0]  # 100 x 220: not square
    found.update({f"{name}.tif": pi_style(name) for name in PI_STYLE})
    return found


@pytest.fixture
def no_computation(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the rule was computed")

    monkeypatch.setattr(helper, "rule_on_plane", refuse)
    monkeypatch.setattr(stain, "to_scalar", refuse)


# Refusals before any computation ---------------------------------------------------------------

GOOD = np.zeros((8, 8, 3), np.uint8)


@pytest.mark.parametrize(
    ("value", "error", "message"),
    [
        (GOOD.tolist(), TypeError, "got list"),
        (None, TypeError, "got NoneType"),
        (np.ma.MaskedArray(GOOD), TypeError, "got MaskedArray, a subclass of it"),
        (np.ma.MaskedArray(GOOD), TypeError, "pass np.asarray(rgb)"),
        (np.matrix(np.zeros((8, 8), np.uint8)), TypeError, "pass np.asarray(rgb)"),
        (GOOD.astype(np.uint16), ValueError, "this array is uint16"),
        (GOOD.astype(np.int8), ValueError, "this array is int8"),
        (GOOD.astype(np.float32), ValueError, "this array is float32"),
        (GOOD.astype(bool), ValueError, "this array is bool"),
        (np.zeros((8, 8), np.uint8), ValueError, "the shape (8, 8)"),
        (np.zeros((3, 8, 8), np.uint8), ValueError, "the shape (3, 8, 8)"),
        (np.zeros((8, 8, 4), np.uint8), ValueError, "the shape (8, 8, 4)"),
        (np.zeros((1, 8, 8, 3), np.uint8), ValueError, "the shape (1, 8, 8, 3)"),
        (np.zeros((0, 8, 3), np.uint8), ValueError, "the shape (0, 8, 3)"),
        (np.zeros((8, 0, 3), np.uint8), ValueError, "the shape (8, 0, 3)"),
    ],
)
def test_an_input_it_cannot_take_is_refused_before_any_computation(
    value, error, message, no_computation
):
    with pytest.raises(error) as caught:
        he_tissue_mask_array(value)
    assert message in str(caught.value)
    assert type(caught.value) is error


def test_an_unknown_rule_is_refused_before_any_computation(no_computation):
    with pytest.raises(ValueError, match="'he_tissue_v2' is not known; the known rules are"):
        he_tissue_mask_array(GOOD, rule="he_tissue_v2")


@pytest.mark.parametrize("rule", [["he_tissue_v1"], ("he_tissue_v1",), None, 1])
def test_a_rule_that_is_not_one_id_is_a_clear_type_error(rule, no_computation):
    with pytest.raises(TypeError, match="The rule is one ID, a str such as he_tissue_v1; got "):
        he_tissue_mask_array(GOOD, rule=rule)


def test_its_parameters_are_the_run_routes_and_nothing_more():
    signature = inspect.signature(he_tissue_mask_array)
    assert (
        str(signature)
        == "(rgb: 'np.ndarray', *, rule: 'str' = 'he_tissue_v1') -> 'TissueMaskArray'"
    )
    run_route = inspect.signature(helper.he_tissue_mask).parameters["rule"]
    assert signature.parameters["rule"].default == run_route.default


# The same numerics as the run route -------------------------------------------------------------


@pytest.mark.parametrize("identity", ["verified", "unverified"])
def test_the_mask_and_record_equal_the_run_routes(tmp_path, monkeypatch, identity):
    monkeypatch.setattr(IDENTITY_PATCH, identities if identity == "verified" else unverified)
    pixels = images()
    run = rgb_run(tmp_path, pixels)
    for name, rgb in pixels.items():
        before = rgb.copy()
        found = he_tissue_mask_array(rgb)
        assert np.array_equal(rgb, before)  # the input is not changed
        result = run.he_tissue_mask(f"{UNIT}/{name}", output_mask_id="HETissue")
        written = tifffile.imread(run.masks_root / result.path)
        assert found.mask.dtype == bool and found.mask.shape == rgb.shape[:2]
        assert np.array_equal(written, found.mask.astype(np.uint8)), name
        assert np.array_equal(written.astype(bool), found.mask), name
        line = result.record
        mine = found.record.to_dict()
        assert {key: mine[key] for key in SHARED} == {key: line[key] for key in SHARED}, name
        assert found.record.code_verified is line["reusable"] is (identity == "verified")


def test_the_threshold_image_gives_the_mask_its_parts_specify():
    # Written from the rule's design, not its record: every literal here is a threshold side.
    rgb, parts = threshold_image()
    assert rgb.shape[:2] == (100, 220)
    ins = {name: parts[f"{name} in"] for name in ON_THRESHOLD}
    nuclei = ins["nuclei H >= 0.05"] | parts["nuclei 15"]
    red_cells = parts["red_cells 15"]
    for name in ON_THRESHOLD:
        if name.startswith("red "):
            red_cells = red_cells | ins[name]
    background = ins["background max OD < 0.08"] | parts["background 1000"]
    for name in ("background max OD < 0.08 in", "background 1000"):  # kept by the opening
        assert np.array_equal(ndimage.binary_opening(parts[name], _disk(2)), parts[name])
    excluded = (
        ndimage.binary_dilation(nuclei, _disk(2))
        | ndimage.binary_dilation(red_cells, _disk(1))
        | background
    )
    found = he_tissue_mask_array(rgb)
    assert found.mask.shape == (100, 220)
    assert np.array_equal(found.mask, ~excluded)
    for name in ON_THRESHOLD:  # one side of each threshold excluded, the other kept
        assert not found.mask[parts[f"{name} in"]].any(), name
        assert found.mask[parts[f"{name} out"]].all(), name
    for kept in ("nuclei 14", "red_cells 14", "background 999"):
        assert found.mask[parts[kept]].all(), kept


def test_no_8_bit_colour_has_a_max_od_between_the_background_pair():
    # So the background threshold can move within (OD(213), OD(212)] with no mask changed;
    # test_he_tissue_rule.py pins the record's 0.08 itself. Any colour's max OD is that of its
    # darkest channel, so the grey levels give every value a colour can have.
    from morphobricks_normalization.optical_density import check_white, optical_density

    greys = np.repeat(np.arange(256, dtype=np.uint8)[None, :, None], 3, axis=2)  # (1, 256, 3)
    od = optical_density(greys, check_white((255, 255, 255)))[0].max(axis=-1)[0]
    assert od[213] < 0.08 <= od[212]
    assert not ((od > od[213]) & (od < od[212])).any()


def test_a_strided_view_gives_the_mask_of_its_pixels():
    rgb = pi_style("pi2")
    view = np.moveaxis(np.ascontiguousarray(np.moveaxis(rgb, -1, 0)), 0, -1)[::-1, ::2]
    assert not view.flags.c_contiguous
    found = he_tissue_mask_array(view)
    assert np.array_equal(found.mask, he_tissue_mask_array(view.copy()).mask)


# The record -----------------------------------------------------------------------------------


def test_the_record_states_the_rule_its_status_and_the_code_identities_as_found():
    found = he_tissue_mask_array(synthetic_he()[0])
    assert isinstance(found, TissueMaskArray)
    record = found.record
    assert isinstance(record, TissueMaskArrayRecord)
    assert (record.rule_id, record.rule_version, record.status, record.provisional) == (
        "he_tissue_v1",
        1,
        "provisional",
        True,
    )
    assert record.operation == "he_tissue_mask"
    assert record.rule == helper.current_rule("he_tissue_v1")
    assert record.rule_sha256 == rule_sha256(record.rule)
    # rule_text is the canonical form itself: the exact text its digest is of (issue #345).
    assert hashlib.sha256(record.rule_text.encode("utf-8")).hexdigest() == record.rule_sha256
    assert record.rule_text == json.dumps(record.rule, sort_keys=True, separators=(",", ":"))
    assert record.code == helper.code_identities()
    assert record.code_verified is all(
        record.code[name]["verified"] for name in ("preprocessing", "normalization")
    )
    for name in ("preprocessing", "normalization"):
        assert tuple(record.code[name]) == (
            "distribution",
            "version",
            "verified",
            "record_sha256",
            "vcs_commit",
            "reason",
        )
        assert (record.code[name]["reason"] is None) is record.code[name]["verified"]
    assert (record.numpy_version, record.scikit_image_version, record.scipy_version) == (
        np.__version__,
        skimage.__version__,
        scipy.__version__,
    )
    assert set(record.fractions) == {"kept", "nuclei", "red_cells", "background", "od_floored"}
    assert record.fractions["kept"] == float(np.mean(found.mask))
    assert record.helper == "morphobricks_preprocessing.he_tissue_mask_array"
    assert record.helper_version == __version__
    data = record.to_dict()
    assert tuple(data) == RECORD_KEYS
    assert json.loads(json.dumps(data)) == data


def test_the_record_is_immutable():
    record = he_tissue_mask_array(synthetic_he()[0]).record
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.rule_id = "he_tissue_v2"
    with pytest.raises(TypeError):
        record.fractions["kept"] = 1.0
    with pytest.raises(TypeError):
        record.code["preprocessing"]["verified"] = True
    rule = record.rule
    rule["status"] = "frozen"
    assert record.status == "provisional" and record.rule["status"] == "provisional"
    data = record.to_dict()
    data["code"]["preprocessing"]["verified"] = True
    data["fractions"]["kept"] = 2.0
    assert record.to_dict() != data


def test_a_non_editable_install_states_verified_identities_for_real():
    # No identity is patched: the installed preprocessing and normalization are checked, as in
    # test_he_tissue_mask.py; CI's wheel-identity job runs this file from a wheel.
    from importlib import metadata

    direct = json.loads(metadata.distribution("morphobricks-preprocessing").read_text(
        "direct_url.json") or "{}")  # fmt: skip
    if direct.get("dir_info", {}).get("editable"):
        pytest.skip("preprocessing is an editable install here: it is never verified")
    helper.code_identities.cache_clear()
    record = he_tissue_mask_array(synthetic_he()[0]).record
    assert record.code["preprocessing"]["verified"], record.code["preprocessing"]["reason"]
    assert record.code["normalization"]["verified"], record.code["normalization"]["reason"]
    assert record.code_verified
