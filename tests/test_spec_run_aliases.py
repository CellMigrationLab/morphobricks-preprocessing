"""A run with a published description: conformance P26 (per-unit header evidence), Stage 1 PR 13.

Experiment-v1 section 4.3 and 8. For a described run the experiment-wide v0.4 checks
``channel_names_differ`` and ``different_channel_counts`` are replaced by per-unit checks: each
header name at C index *i* must be, after NFC of both sides and with no trimming, a confirmed
``header_names`` entry of the unit's channel at *i* (``R_HEADER_CONTRADICTION``); an image
without header names needs no evidence (``v12``). Without a description the v0.4 checks stay.
"""

from __future__ import annotations

import numpy as np
import pytest
import tifffile

from description_tree import MAPPING, unit_folder
from morphobricks_preprocessing import run_experiment
from morphobricks_preprocessing.description import description_from_bytes
from spec_pin import fixtures

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}
CONTRADICTION = "R_HEADER_CONTRADICTION"
COUNT = "R_CHANNEL_COUNT_MISMATCH"
HOMOGENEOUS = "R_UNIT_NOT_HOMOGENEOUS"
GLOBAL = {"channel_names_differ", "different_channel_counts"}
COMPOSED, DECOMPOSED = "Café", "Café"


def _image(path, names, channels=None):
    """Write a small CYX image whose header carries ``names`` (None: no names)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.zeros((channels or len(names), 8, 8), np.uint8)
    if names is None:
        tifffile.imwrite(path, data, photometric="minisblack", metadata={"axes": "CYX"})
    else:
        meta = {"axes": "CYX", "Channel": {"Name": list(names)}}
        tifffile.imwrite(path, data, ome=True, photometric="minisblack", metadata=meta)


def _setup(tmp_path, fixture, names_by_unit, *, described=True, images=2):
    """Build the fixture's folders with the header names given per acquisition folder."""
    description = description_from_bytes(VALID[fixture].data)
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir(parents=True)
    if described:
        (out / "morphobricks.toml").write_bytes(VALID[fixture].data)
    for repeat_id, repeat in description.repeats.items():
        for condition_id, member in repeat.members.items():
            for unit in member.technical_repeats.values():
                folder = root / unit_folder(description, repeat_id, condition_id, unit)
                names = names_by_unit.get(unit.source_folder, names_by_unit.get("*"))
                for index in range(images):
                    _image(folder / f"img{index}.ome.tif", names, len(unit.channels))
    return root, out


def _run(root, out):
    return run_experiment(root, MAPPING, output_folder=out)


def _codes(result):
    return sorted(i.code for i in result.issues)


def test_p26_units_spelling_headers_differently_through_declared_aliases_are_accepted(tmp_path):
    root, out = _setup(
        tmp_path,
        "v11",
        {"acquisition_1": ["DAPI", "ACTIN"], "acquisition_2": ["Hoechst 33342", "ACTIN"]},
    )
    result = _run(root, out)
    assert result.written, [(i.code, i.message) for i in result.issues]
    assert not [i for i in result.issues if i.code in GLOBAL]


def test_the_same_folder_without_a_description_is_still_refused_by_the_v0_4_check(tmp_path):
    root, out = _setup(
        tmp_path,
        "v11",
        {"acquisition_1": ["DAPI", "ACTIN"], "acquisition_2": ["Hoechst 33342", "ACTIN"]},
        described=False,
    )
    with pytest.warns(UserWarning, match="No experiment description"):
        result = _run(root, out)
    assert not result.written
    assert _codes(result) == ["channel_names_differ"]


def test_p26_a_name_that_matches_no_declared_name_is_refused_naming_unit_image_and_names(
    tmp_path,
):
    root, out = _setup(
        tmp_path,
        "v11",
        {"acquisition_1": ["DAPI", "ACTIN"], "acquisition_2": ["Hoechst-33342", "ACTIN"]},
    )
    result = _run(root, out)
    assert not result.written
    assert _codes(result) == [CONTRADICTION, CONTRADICTION]  # one per image, nothing global
    found = result.issues[0]
    assert found.paths[0].startswith("CTRL/rep1/acquisition_2/img")
    for text in (
        "acquisition folder 'acquisition_2'",
        "repeat folder 'rep1'",
        "condition folder 'CTRL'",
        "'Hoechst-33342'",
        "channel position 0",
        "['Hoechst-33342', 'ACTIN']",  # the image's header names
        "'DNA'",
        "['DAPI', 'Hoechst 33342']",  # the declared names
    ):
        assert text in found.message
    assert not (out / "plane_histograms.csv").exists()


@pytest.mark.parametrize("name", ["dapi", "DAPI ", " DAPI", "Hoechst  33342", "DAPI "])
def test_p26_no_case_folding_trimming_or_similarity(tmp_path, name):
    root, out = _setup(tmp_path, "v11", {"*": [name, "ACTIN"]})
    result = _run(root, out)
    assert not result.written
    assert set(_codes(result)) == {CONTRADICTION}


def test_p26_outer_whitespace_needs_its_own_confirmed_alias_v31(tmp_path):
    root, out = _setup(tmp_path, "v31", {"*": [" DAPI", f"{COMPOSED} 488"]})
    result = _run(root, out)
    assert result.written, [(i.code, i.message) for i in result.issues]
    refused = _setup(tmp_path / "again", "v31", {"*": ["  DAPI", f"{COMPOSED} 488"]})
    assert set(_codes(_run(*refused))) == {CONTRADICTION}


@pytest.mark.parametrize("header", [COMPOSED, DECOMPOSED])
def test_p26_names_are_compared_after_nfc_of_both_sides(tmp_path, header):
    root, out = _setup(tmp_path, "v31", {"*": ["DAPI", f"{header} 488"]})
    assert _run(root, out).written  # the fixture's alias is decomposed, the header either form


def test_p26_the_check_is_by_c_index_a_name_of_another_channel_is_refused(tmp_path):
    root, out = _setup(tmp_path, "v11", {"*": ["ACTIN", "DAPI"]})
    result = _run(root, out)
    assert set(_codes(result)) == {CONTRADICTION}
    assert "channel position 0" in result.issues[0].message


def test_p26_images_without_header_names_need_no_evidence_v12(tmp_path):
    root, out = _setup(tmp_path, "v12", {"*": None})
    result = _run(root, out)
    assert result.written, [(i.code, i.message) for i in result.issues]


def test_p26_a_unit_with_named_and_unnamed_images_is_not_homogeneous(tmp_path):
    root, out = _setup(tmp_path, "v11", {"*": ["DAPI", "ACTIN"]})
    _image(root / "CTRL" / "rep1" / "acquisition_1" / "img0.ome.tif", None, 2)
    result = _run(root, out)
    assert set(_codes(result)) == {HOMOGENEOUS}


def test_p26_a_unit_whose_images_have_the_wrong_count_is_a_count_mismatch_not_the_global_one(
    tmp_path,
):
    root, out = _setup(tmp_path, "v11", {"*": ["DAPI", "ACTIN"]})
    folder = root / "CTRL" / "rep1" / "acquisition_2"
    for path in folder.glob("*.tif"):
        _image(path, ["DAPI", "ACTIN", "Extra"])
    result = _run(root, out)
    assert set(_codes(result)) == {COUNT}
    assert "'DNA', 'ACTIN'" not in result.issues[0].message  # plain ids, no quotes
    assert "DNA, ACTIN" in result.issues[0].message
    assert "acquisition folder 'acquisition_2'" in result.issues[0].message
