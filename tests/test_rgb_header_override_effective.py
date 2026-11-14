"""The RGB header override, declared and effective: owner answers of 14 November 2026 (RH1).

Spec experiment-v1 4.4b, 8 and 10 as amended at Spec commit 179dc5c, conformance P36 (d)-(h):

- homogeneity compares the effective type, so a unit whose headers differ only in colour
  interpretation is homogeneous under the override, each image's header kept as evidence (e);
- the override is declared but unused for a header that already says RGB (d) and for a format
  that states no colour interpretation, such as OME-Zarr (f);
- ``R_RGB_OVERRIDE_NOT_THREE_SAMPLES`` is reported before ``R_UNIT_NOT_HOMOGENEOUS`` (g);
- the key is part of the confirmed layout: adding, removing or changing it reopens the N35
  confirmation (h, Spec transitions ``t14`` and ``t15``).

The Spec transitions ``t14`` and ``t15`` are not vendored yet (the pin moves only to a merged Spec
commit): the candidates here are built locally from the vendored ``v28`` the way the Spec files
describe them. Codes are asserted as the literal strings of the Spec text.
"""

from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing.axes import prefill_mapping
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_reconcile import (
    UnitFolders,
    layout_changed,
    reconcile,
)
from morphobricks_preprocessing.description_record import (
    description_record,
    recorded_description,
    unit_records,
)
from morphobricks_preprocessing.description_rules import Code
from morphobricks_preprocessing.description_transition import transition_issues
from morphobricks_preprocessing.discovery import discover_experiment
from morphobricks_preprocessing.draft import draft_from_discovery
from morphobricks_preprocessing.metadata import inspect_images
from morphobricks_preprocessing.physical_check import (
    IMAGE_TYPE_CONTRADICTS_HEADER,
    RGB_OVERRIDE_NOT_THREE_SAMPLES,
    UNIT_NOT_HOMOGENEOUS,
)
from morphobricks_preprocessing.published_answers import published_answers, reopened
from morphobricks_preprocessing.reconcile_observe import observe_folders
from morphobricks_preprocessing.testing_described import publish_and_run, write_described_tree
from spec_pin import fixtures
from test_rgb_header_override import (
    CTRL,
    DRUG,
    SHAPE,
    V28,
    V40,
    _with_override,
    build,
    check,
    rgb_tiff,
    scanner_tiff,
)

V28_BYTES = V28.encode("utf-8")
DAY, NEXT_DAY = 'confirmed_on = "2026-11-06"', 'confirmed_on = "2026-11-07"'
T14 = V40.decode("utf-8").replace(DAY, NEXT_DAY).encode("utf-8")
T15 = _with_override({"rep1": "true"}).replace(DAY, NEXT_DAY).encode("utf-8")
MIXED = "CTRL/rep1/FOV001.tif"


def _uses(report, key):
    return {
        path: (u.use, u.reason, u.photometric) for path, u in report.override_images[key].items()
    }


def _mixed(root):
    """Scanner CTRL images, one of rep1's with a photometric RGB header (P36 e)."""
    build(root)
    rgb_tiff(root / MIXED)
    return root


# B4: the codes are the Spec's literals --------------------------------------------------------


def test_the_emitted_codes_are_the_spec_literals(tmp_path):
    assert RGB_OVERRIDE_NOT_THREE_SAMPLES == "R_RGB_OVERRIDE_NOT_THREE_SAMPLES"
    assert IMAGE_TYPE_CONTRADICTS_HEADER == "R_IMAGE_TYPE_CONTRADICTS_HEADER"
    assert Code.E_RGB_OVERRIDE_WITHOUT_RGB.value == "E_RGB_OVERRIDE_WITHOUT_RGB"
    root = build(tmp_path / "exp")
    assert {i.code for i in check(root, V28_BYTES).issues} == {"R_IMAGE_TYPE_CONTRADICTS_HEADER"}
    scanner_tiff(root / "CTRL/rep1/FOV000.tif", np.zeros((6, 7), np.uint8))
    assert {i.code for i in check(root, V40).issues} == {"R_RGB_OVERRIDE_NOT_THREE_SAMPLES"}


# P36 (d), (e), (f): declared, used and unused ----------------------------------------------


def test_p36d_a_header_that_already_says_rgb_leaves_the_override_unused(tmp_path):
    report = check(build(tmp_path / "exp", ctrl=rgb_tiff), V40)
    assert report.ok and not report.image_type_overridden
    assert set(report.override_images) == set(CTRL)  # declared; DrugA does not declare it
    for key in CTRL:
        assert set(_uses(report, key).values()) == {("unused", "header_already_rgb", "rgb")}


def test_p36e_headers_differing_only_in_colour_interpretation_are_homogeneous(tmp_path):
    report = check(_mixed(tmp_path / "exp"), V40)
    assert report.ok, report.issues
    assert "R_UNIT_NOT_HOMOGENEOUS" not in report.codes
    assert report.layouts[CTRL[0]].rgb is True and report.layouts[CTRL[0]].channels == 3
    assert report.layouts[CTRL[0]] == report.layouts[DRUG[0]]  # one effective layout
    assert dict(report.image_type_overridden) == dict.fromkeys(CTRL, "minisblack")
    assert report.image_type_verified[CTRL[0]] is False  # one header was lifted
    assert _uses(report, CTRL[0]) == {  # each image's own header kept as evidence
        "CTRL/rep1/FOV000.tif": ("used", None, "minisblack"),
        MIXED: ("unused", "header_already_rgb", "rgb"),
    }


def test_p36e_without_the_key_the_mixed_unit_is_still_not_homogeneous(tmp_path):
    report = check(_mixed(tmp_path / "exp"), V28_BYTES)
    assert report.codes == {"R_UNIT_NOT_HOMOGENEOUS", "R_IMAGE_TYPE_CONTRADICTS_HEADER"}
    (issue,) = [i for i in report.issues if i.code == UNIT_NOT_HOMOGENEOUS]
    assert issue.unit == CTRL[0] and "colour interpretation" in issue.detail
    assert not report.override_images


def test_p36e_a_run_records_the_mixed_unit_image_by_image(tmp_path):
    root = tmp_path / "run"
    experiment = root / "experiment"
    write_described_tree(experiment, None, description_from_bytes(V40))
    stored = tifffile.imread(experiment / "CTRL/rep1/img0.tif")
    tifffile.imwrite(experiment / "CTRL/rep1/img0.tif", stored, photometric="rgb")
    run = publish_and_run(root, experiment, None, prefill_mapping("YXS"), V40)
    assert run.result.written
    manifest = json.loads((run.output / "run_manifest.json").read_text("utf-8"))
    (unit,) = [
        u
        for u in manifest["experiment_description"]["units"]
        if (u["condition"], u["repeat"]) == ("CTRL", "rep1")
    ]
    assert (unit["rgb_header_override"], unit["image_type_header_overridden"]) == (True, True)
    assert unit["header_photometric"] == "minisblack"
    (first, *others) = unit["rgb_header_override_images"]
    assert first == {
        "image": "CTRL/rep1/img0.tif",
        "override": "unused",
        "header_photometric": "rgb",
        "unused_reason": "header_already_rgb",
    }
    assert others and all(u["override"] == "used" for u in others)
    assert recorded_description(manifest["experiment_description"]) is not None


def test_p36f_ome_zarr_with_the_key_is_declared_unused_and_not_header_verified(tmp_path):
    pytest.importorskip("zarr")
    import zarr_tree

    root = tmp_path / "exp"
    for condition in ("CTRL", "DrugA"):
        for rep in ("rep1", "rep2"):
            for index in range(2):
                path = root / condition / rep / f"FOV{index:03d}"
                if condition == "CTRL":
                    zarr_tree.write_ngff(
                        path.with_suffix(".ome.zarr"), np.zeros((3, 6, 7), np.uint8), "cyx"
                    )
                else:
                    rgb_tiff(path.with_suffix(".tif"))
    report = check(root, V40)
    assert report.ok, report.issues
    assert not report.image_type_overridden  # nothing was lifted
    assert {key: report.image_type_verified[key] for key in CTRL} == dict.fromkeys(CTRL, False)
    for key in CTRL:
        uses = _uses(report, key)
        assert len(uses) == 2
        assert set(uses.values()) == {("unused", "no_colour_interpretation", None)}
    units = unit_records(
        description_from_bytes(V40),
        {tuple(vars(k).values()): v for k, v in report.image_type_verified.items()},
        None,
        {},
        {tuple(vars(k).values()): v for k, v in report.override_images.items()},
    )
    ctrl = [u for u in units if u["condition"] == "CTRL"]
    for unit in ctrl:
        assert unit["rgb_header_override"] is True
        assert unit["image_type_header_verified"] is False
        assert "image_type_header_overridden" not in unit
        for item in unit["rgb_header_override_images"]:
            assert item["override"] == "unused"
            assert item["unused_reason"] == "no_colour_interpretation"
            assert item["header_photometric"] is None


# P36 (g): the priority of the three-sample refusal ---------------------------------------------


@pytest.mark.parametrize(
    "options",
    [
        {"photometric": "minisblack", "extrasamples": ["unspecified"] * 3},
        {"photometric": "rgb", "extrasamples": ["unassalpha"]},
    ],
    ids=["minisblack", "rgb-with-alpha"],
)
def test_p36g_four_samples_beside_three_is_the_override_refusal_not_homogeneity(tmp_path, options):
    root = build(tmp_path / "exp")
    odd = root / "CTRL/rep1/FOV001.tif"
    tifffile.imwrite(odd, np.zeros((*SHAPE[:2], 4), np.uint8), planarconfig="contig", **options)
    report = check(root, V40)
    assert report.codes == {"R_RGB_OVERRIDE_NOT_THREE_SAMPLES"}
    (issue,) = report.issues
    assert (issue.unit, issue.path) == (CTRL[0], "CTRL/rep1/FOV001.tif")
    assert "4 interleaved samples" in issue.detail
    # Without the key the same unit is not homogeneous (4 channels beside 3) and so reported.
    assert "R_UNIT_NOT_HOMOGENEOUS" in check(root, V28_BYTES).codes


# The manifest's records ------------------------------------------------------------------------


def _record(tmp_path):
    report = check(_mixed(tmp_path / "exp"), V40)
    key = {tuple(vars(k).values()): v for k, v in report.override_images.items()}
    units = unit_records(description_from_bytes(V40), {}, None, {}, key)
    return description_record(description_from_bytes(V40), "0" * 64, units, {}, ())


def test_a_unit_not_declared_rgb_records_no_override_key():
    plain = next(f for f in fixtures("valid") if b"image_type" not in f.data)
    for unit in unit_records(description_from_bytes(plain.data), {}):
        assert set(unit) == {
            "repeat",
            "condition",
            "technical_repeat",
            "channels",
            "image_type_header_verified",
        }


def test_a_mixed_unit_record_reads_back(tmp_path):
    record = _record(tmp_path)
    assert recorded_description(record) is record


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda items: items[0].update(override="maybe"),
        lambda items: items[0].update(unused_reason="header_already_rgb"),  # a used item
        lambda items: items[1].pop("unused_reason"),
        lambda items: items[1].update(header_photometric=None),
        lambda items: items.reverse(),
        lambda items: items.clear(),
    ],
)
def test_a_malformed_override_record_is_refused(tmp_path, corrupt):
    record = _record(tmp_path)
    (unit,) = [u for u in record["units"] if (u["condition"], u["repeat"]) == ("CTRL", "rep1")]
    corrupt(unit["rgb_header_override_images"])
    with pytest.raises(ValueError, match="rgb_header_override_images"):
        recorded_description(record)


def test_a_declared_key_that_is_not_a_boolean_is_refused(tmp_path):
    record = _record(tmp_path)
    record["units"][0]["rgb_header_override"] = "true"
    with pytest.raises(ValueError, match="rgb_header_override is not true or false"):
        recorded_description(record)


# Reconciliation agrees with the run -----------------------------------------------------------


def _seen(root):
    discovery = discover_experiment(root)
    return observe_folders(discovery, metadata=inspect_images(root, discovery.images).files)


def test_a_mixed_unit_is_unchanged_under_the_override_and_changed_without_it(tmp_path):
    root = build(tmp_path / "exp", images=8)
    rgb_tiff(root / MIXED)
    seen = _seen(root)
    mixed = seen[UnitFolders("CTRL", "rep1", "single")]
    assert (mixed.uniform, mixed.uniform_as_rgb, mixed.three_samples) == (False, True, True)
    assert reconcile(description_from_bytes(V40), seen).reopened == {}
    assert set(reconcile(description_from_bytes(V28_BYTES), seen).reopened) == {"rep1", "rep2"}


def test_a_four_sample_image_is_not_uniform_as_rgb(tmp_path):
    root = build(tmp_path / "exp", images=8)
    tifffile.imwrite(root / MIXED, np.zeros((6, 7, 4), np.uint8), extrasamples=["unspecified"])
    mixed = _seen(root)[UnitFolders("CTRL", "rep1", "single")]
    assert (mixed.uniform, mixed.uniform_as_rgb) == (False, False)
    assert "rep1" in reconcile(description_from_bytes(V40), _seen(root)).reopened


# P36 (h): the key is part of the confirmed layout (Spec t14, t15) --------------------------------


def _codes(old: bytes, new: bytes) -> set[tuple[str, str]]:
    found = transition_issues(description_from_bytes(old), description_from_bytes(new))
    return {(issue.code.value, issue.old_id) for issue in found}


def test_t14_the_key_added_to_both_repeats_reopens_both():
    assert _codes(V28_BYTES, T14) == set()
    assert layout_changed(description_from_bytes(V28_BYTES), description_from_bytes(T14)) == (
        "rep1",
        "rep2",
    )


def test_t15_only_rep1_changed_so_rep2s_new_date_is_refused():
    assert _codes(V28_BYTES, T15) == {("R_CONFIRMED_ON_CHANGED", "rep2")}


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (V40, V28_BYTES),  # removed
        (V28_BYTES, _with_override({"rep1": "false", "rep2": "false"}).encode("utf-8")),  # added
        (V40, _with_override({"rep1": "false", "rep2": "false"}).encode("utf-8")),  # changed
    ],
    ids=["removed", "added-false", "changed"],
)
def test_removing_adding_or_changing_the_key_reopens(old, new):
    dated = new.decode("utf-8").replace(DAY, NEXT_DAY).encode("utf-8")
    assert _codes(old, dated) == set()
    assert _codes(old, new) == set()  # the dates may also stay


def test_an_unchanged_key_keeps_the_date():
    assert _codes(V40, T14) == {
        ("R_CONFIRMED_ON_CHANGED", "rep1"),
        ("R_CONFIRMED_ON_CHANGED", "rep2"),
    }


def test_the_notebook_reopens_a_repeat_whose_key_changed(tmp_path):
    published = description_from_bytes(V28_BYTES)
    root = tmp_path / "exp"
    write_described_tree(root, None, published)
    draft = draft_from_discovery(discover_experiment(root))
    answers = published_answers(published, "0" * 64)
    key = ("CTRL", "rep1", "single")
    units = {**answers.units, key: replace(answers.units[key], rgb_header_override=True)}
    _, changed = reopened(draft, replace(answers, units=units), published)
    assert changed == ("rep1",)
    assert reopened(draft, answers, published)[1] == ()
