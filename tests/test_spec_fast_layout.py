"""Fast mode (Spec ADR 0006, owner decisions FC1-FC4): the description and its image checks.

The description part is the conformance plan section 2 row "Fast mode" (fixtures ``v41``-``v43``,
``i142``-``i151``, ``t16``-``t20``; experiment-v1 sections 4.3a, 4.4, 6, 9.1, 9.3, 10). The image
part is scenarios P37-P39 (experiment-v1 section 8, "Fast-mode checks"): every image of a unit
with ``channels_from = "experiment"`` is compared with ``channel_layout`` from its header only,
and every contradiction over all fast units is one ``R_FAST_LAYOUT_CONTRADICTION``.
"""

from __future__ import annotations

import json
import re

import numpy as np
import pytest
import tifffile

from described_tree import run_described
from description_tree import MAPPING
from morphobricks_preprocessing.description import DescriptionError, description_from_bytes
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME
from morphobricks_preprocessing.description_read import read_description_bytes
from morphobricks_preprocessing.description_record import (
    FAST_IMAGES,
    checked_fast_images,
    recorded_description,
)
from morphobricks_preprocessing.description_run_text import physical_issues
from morphobricks_preprocessing.description_write import canonical_bytes, section_digests
from morphobricks_preprocessing.discovery import discover_experiment
from morphobricks_preprocessing.fast_layout import (
    CHANNEL_COUNT,
    HEADER_NAMES,
    HEADER_ORDER,
    IMAGE_TYPE,
)
from morphobricks_preprocessing.metadata import inspect_images
from morphobricks_preprocessing.physical_check import UnitKey, check_physical
from morphobricks_preprocessing.run.description_file import publish_description
from morphobricks_preprocessing.testing_described import unit_folder
from spec_pin import fixtures

SPEC = {f.name.split("_")[0]: f for kind in ("valid", "invalid") for f in fixtures(kind)}
TRANSITIONS = {f.name.split("_")[0]: f for f in fixtures("transitions")}
V41, V42, V43 = SPEC["v41"].data, SPEC["v42"].data, SPEC["v43"].data
FAST = "R_FAST_LAYOUT_CONTRADICTION"
STRICT_CODES = {
    "R_CHANNEL_COUNT_MISMATCH",
    "R_HEADER_CONTRADICTION",
    "R_IMAGE_TYPE_CONTRADICTS_HEADER",
    "R_UNIT_NOT_HOMOGENEOUS",
}
MATCH = ["DAPI", "Phalloidin-488"]
PATHS = re.compile(rb"^# Paths: (.+)$", re.M)
FOV_KEY = {
    "condition": "CTRL",
    "biological_repeat": "rep1",
    "technical_repeat": "single",
    "position_id": "a",
}


# --- The description: reader, writer, digests (conformance plan section 2, "Fast mode") -------


@pytest.mark.parametrize("number", ["v41", "v42", "v43"])
def test_fast_mode_files_v41_to_v43_are_read_and_round_trip(number):
    description = description_from_bytes(SPEC[number].data)
    assert description.fast_mode
    assert all(
        unit.channels_from in ("experiment", "unit")
        for repeat in description.repeats.values()
        for member in repeat.members.values()
        for unit in member.technical_repeats.values()
    )
    written = canonical_bytes(description)
    assert description_from_bytes(written) == description
    assert canonical_bytes(description_from_bytes(written)) == written


def test_9_1_channel_layout_follows_channels_and_channels_from_follows_a_units_channels():
    text = canonical_bytes(description_from_bytes(V43)).decode("utf-8")
    heads = re.findall(r"^\[([^\]]+)\]$", text, flags=re.M)
    assert heads.index("channel_layout") == heads.index("channels.R") + 1
    block = text.split("[channel_layout]\n", 1)[1].split("\n\n", 1)[0]
    assert block.splitlines() == [
        'channels = ["R", "G", "B"]',
        'image_type = "rgb"',
        'confirmed_on = "2026-11-16"',
    ]
    unit = text.split("[repeats.rep1.members.CTRL.technical_repeats.single]\n", 1)[1]
    keys = [line.split(" = ")[0] for line in unit.split("\n\n", 1)[0].splitlines()]
    assert keys == ["source_folder", "channels", "channels_from", "image_type"] + [
        "rgb_header_override"
    ]


def test_4_3a_no_reader_supplies_channel_layout_for_a_strict_file():
    strict = description_from_bytes(SPEC["v01"].data)
    assert strict.channel_layout is None and not strict.fast_mode
    units = [
        u
        for r in strict.repeats.values()
        for m in r.members.values()
        for u in m.technical_repeats.values()
    ]
    assert units and all(u.channels_from is None and not u.follows_experiment for u in units)
    assert b"channel_layout" not in canonical_bytes(strict)
    assert b"channels_from" not in canonical_bytes(strict)


def _paths(data: bytes) -> set[tuple[str, str]]:
    found = PATHS.search(data)
    assert found is not None
    return {tuple(item.split(" ", 1)) for item in found.group(1).decode("utf-8").split("; ")}


@pytest.mark.parametrize("number", [f"i{n}" for n in range(142, 152)])
def test_i142_to_i151_give_their_codes_at_their_exact_paths(number):
    report = read_description_bytes(SPEC[number].data)
    assert {(issue.code.value, issue.path) for issue in report.errors} == _paths(SPEC[number].data)
    assert report.phase == SPEC[number].phase


def test_9_1_the_writer_refuses_the_i142_model_and_writes_nothing(tmp_path):
    from dataclasses import replace

    v41 = description_from_bytes(V41)
    member = v41.repeats["rep1"].members["DrugA"]
    unit = replace(member.technical_repeats["single"], channels=("ACTIN", "DNA"))
    repeat = v41.repeats["rep1"]
    members = {**repeat.members, "DrugA": replace(member, technical_repeats={"single": unit})}
    candidate = replace(v41, repeats={**v41.repeats, "rep1": replace(repeat, members=members)})
    with pytest.raises(DescriptionError) as refused:
        publish_description(tmp_path, candidate, base_sha256="absent")
    assert {i.code.value for i in refused.value.issues} == {"E_FAST_LAYOUT_MISMATCH"}
    assert not (tmp_path / DESCRIPTION_FILENAME).exists()


def test_9_3_t16_has_the_section_digests_of_v01_channels_from_is_in_no_digest():
    t16 = description_from_bytes(TRANSITIONS["t16"].data)
    assert section_digests(t16) == section_digests(description_from_bytes(SPEC["v01"].data))


def test_10_t18_only_the_statement_date_changed_is_refused_naming_channel_layout():
    from morphobricks_preprocessing.description_transition import transition_issues

    issues = transition_issues(
        description_from_bytes(V41), description_from_bytes(TRANSITIONS["t18"].data)
    )
    assert [(i.code.value, i.kind, i.old_id) for i in issues] == [
        ("R_CONFIRMED_ON_CHANGED", "channel_layout", "confirmed_on")
    ]


# --- The images (P37-P39) --------------------------------------------------------------------


def _ome(path, names, channels=None, axes="CYX"):
    """Write an OME-TIFF whose header states ``names`` (None: a TIFF without channel names).

    ``axes`` is ``CYX`` or ``CZYX`` (two Z planes), for the axes check of a fast unit.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    shape = (channels or len(names), *((2,) if "Z" in axes else ()), 8, 8)
    meta = {"axes": axes}
    if names is not None:
        meta["Channel"] = {"Name": list(names)}
    tifffile.imwrite(
        path,
        np.zeros(shape, np.uint8),
        ome=names is not None,
        photometric="minisblack",
        metadata=meta,
    )


def _rgb(path, photometric="rgb", shape=(8, 8, 3)):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.zeros(shape, np.uint8)
    options = {"photometric": photometric}
    if len(shape) == 3:
        options["planarconfig"] = "contig"
    tifffile.imwrite(path, data, **options)


def _tree(root, data, names=MATCH, images=2, writer=None):
    """Write ``images`` images per unit of ``data``; every header states ``names``."""
    description = description_from_bytes(data)
    for repeat_id, repeat in description.repeats.items():
        for condition_id, member in repeat.members.items():
            for unit in member.technical_repeats.values():
                folder = root / unit_folder(description, repeat_id, condition_id, unit)
                for index in range(images):
                    path = folder / f"FOV{index:03d}.ome.tif"
                    if writer is None:
                        _ome(path, names)
                    else:
                        writer(path, condition_id)
    return root


def _check(root, data):
    discovery = discover_experiment(root)
    assert not [i for i in discovery.issues if i.is_error]
    files = inspect_images(root, discovery.images).files
    return check_physical(description_from_bytes(data), discovery, files)


@pytest.fixture
def no_pixel_reads(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("pixel data was read")

    for cls in (tifffile.TiffFile, tifffile.TiffPage, tifffile.TiffFrame, tifffile.TiffPageSeries):
        monkeypatch.setattr(cls, "asarray", fail)


def _fast(report):
    return {i.path: i for i in report.issues if i.code == FAST}


def test_p37a_matching_headers_are_accepted_and_recorded_as_header_verified(tmp_path):
    root = _tree(tmp_path / "exp", V41)
    _ome(root / "CTRL/rep1/acquisition_1/FOV009.ome.tif", ["Hoechst 33342", "Phalloidin-488"])
    report = _check(root, V41)
    assert report.ok, report.issues
    unit = UnitKey("CTRL", "rep1", "acquisition_1")
    records = report.fast_images[unit]
    assert [r["path"] for r in records] == sorted(r["path"] for r in records)
    assert all(r["header_verified"] for r in records)
    last = records[-1]
    assert last["fov_key"] == {
        "condition": "CTRL",
        "biological_repeat": "rep1",
        "technical_repeat": "acquisition_1",
        "position_id": "FOV009",
    }
    assert last["channels"] == [
        {"c": 0, "channel": "DNA", "header_name": "Hoechst 33342", "evidence": "header"},
        {"c": 1, "channel": "ACTIN", "header_name": "Phalloidin-488", "evidence": "header"},
    ]
    # DrugA/rep2 is tagged on its own: strict records, no fast_layout_images
    assert UnitKey("DrugA", "rep2", "single") not in report.fast_images


def test_p37b_images_without_header_names_are_accepted_under_the_statement(tmp_path):
    root = _tree(tmp_path / "exp", V41)
    for index in range(2):
        _ome(root / f"CTRL/rep2/FOV{index:03d}.ome.tif", None, 2)
    report = _check(root, V41)
    assert report.ok, report.issues
    records = report.fast_images[UnitKey("CTRL", "rep2", "single")]
    assert not any(r["header_verified"] for r in records)
    assert [e["evidence"] for e in records[0]["channels"]] == ["statement", "statement"]
    assert [e["header_name"] for e in records[0]["channels"]] == [None, None]


def test_p37c_four_contradictions_are_one_refusal_listing_every_image(tmp_path, no_pixel_reads):
    root = _tree(tmp_path / "exp", V41)
    _ome(root / "DrugA/rep1/FOV010.ome.tif", [*MATCH, "Cy5"], 3)
    _ome(root / "CTRL/rep2/FOV010.ome.tif", ["Phalloidin-488", "DAPI"])
    _ome(root / "CTRL/rep1/acquisition_2/FOV010.ome.tif", ["DAPI", "phalloidin-488"])
    _rgb(root / "DrugA/rep1/FOV011.ome.tif")
    report = _check(root, V41)
    assert report.codes == {FAST}
    assert not report.codes & STRICT_CODES
    found = _fast(report)
    kinds = {path: [c.kind for c in issue.contradictions] for path, issue in found.items()}
    assert kinds == {
        "DrugA/rep1/FOV010.ome.tif": [CHANNEL_COUNT],
        "CTRL/rep2/FOV010.ome.tif": [HEADER_ORDER],
        "CTRL/rep1/acquisition_2/FOV010.ome.tif": [HEADER_NAMES],
        "DrugA/rep1/FOV011.ome.tif": [IMAGE_TYPE, CHANNEL_COUNT],
    }
    order = found["CTRL/rep2/FOV010.ome.tif"].contradictions[0]
    assert order.c_indices == (0, 1)
    assert "'Phalloidin-488'" in order.found and "DNA (DAPI, Hoechst 33342)" in order.expected
    issues = physical_issues(report.issues)
    assert [i.code for i in issues] == [FAST]  # one refusal, every image
    refusal = issues[0]
    assert set(refusal.paths) == set(found)
    for path in found:
        assert path in refusal.message
    assert "CTRL/rep2/single/FOV010" in refusal.message  # the FOV key
    assert 'channels_from = "unit"' in refusal.action and "run again" in refusal.action
    assert "3 channels" in refusal.message and "the list has" not in refusal.message


def test_p37c_a_run_stops_before_any_pixel_is_read_and_publishes_nothing(tmp_path, no_pixel_reads):
    root, out = _tree(tmp_path / "exp", V41), tmp_path / "out"
    out.mkdir()
    (out / DESCRIPTION_FILENAME).write_bytes(V41)
    _ome(root / "CTRL/rep2/FOV010.ome.tif", ["Phalloidin-488", "DAPI"])
    result = run_described(root, MAPPING, output_folder=out)
    assert not result.written
    codes = [i.code for i in result.issues if i.is_error]
    assert codes.count(FAST) == 1 and not set(codes) & STRICT_CODES
    assert not (out / "plane_histograms.csv").exists()


def test_p37d_a_unit_tagged_on_its_own_keeps_the_strict_codes(tmp_path):
    root = _tree(tmp_path / "exp", V41)
    for index in range(2):
        _ome(root / f"DrugA/rep2/FOV{index:03d}.ome.tif", ["DAPI", "phalloidin"])
    report = _check(root, V41)
    assert report.codes == {"R_HEADER_CONTRADICTION"}


def test_p37e_different_confirmed_aliases_in_one_fast_unit_are_accepted_o1(tmp_path):
    root = _tree(tmp_path / "exp", V41)
    _ome(root / "CTRL/rep1/acquisition_1/FOV001.ome.tif", ["Hoechst 33342", "Phalloidin-488"])
    report = _check(root, V41)
    assert report.ok, report.issues
    records = report.fast_images[UnitKey("CTRL", "rep1", "acquisition_1")]
    assert [r["channels"][0]["header_name"] for r in records] == ["DAPI", "Hoechst 33342"]


def test_p37f_the_v42_tree_is_processed_each_unit_against_its_own_list(tmp_path):
    root = _tree(tmp_path / "exp", V42)
    for index in range(2):
        _ome(root / f"DrugA/rep2/FOV{index:03d}.ome.tif", ["Phalloidin-488", "DAPI"])
    report = _check(root, V42)
    assert report.ok, report.issues
    root2, out = _tree(tmp_path / "exp2", V42), tmp_path / "out"
    for index in range(2):
        _ome(root2 / f"DrugA/rep2/FOV{index:03d}.ome.tif", ["Phalloidin-488", "DAPI"])
    out.mkdir()
    (out / DESCRIPTION_FILENAME).write_bytes(V42)
    result = run_described(root2, MAPPING, output_folder=out)
    assert result.written, [(i.code, i.message) for i in result.issues if i.is_error]
    assert "R_HETEROGENEOUS_LAYOUT_NOT_ENABLED" not in {i.code for i in result.issues}


def test_p37g_one_image_lists_every_kind_found_in_it(tmp_path, no_pixel_reads):
    root = _tree(tmp_path / "exp", V41)
    path = root / "DrugA/rep1/FOV010.ome.tif"
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"axes": "YXS", "Channel": {"Name": ["GFP", "Phalloidin-488", "DAPI"]}}
    tifffile.imwrite(
        path, np.zeros((8, 8, 3), np.uint8), ome=True, photometric="rgb", metadata=meta
    )
    report = _check(root, V41)
    issue = _fast(report)["DrugA/rep1/FOV010.ome.tif"]
    kinds = [c.kind for c in issue.contradictions]
    assert kinds == [IMAGE_TYPE, CHANNEL_COUNT, HEADER_NAMES]
    names = issue.contradictions[2]
    assert names.c_indices == (0,) and names.found == "'GFP'"
    assert names.expected == "DNA (DAPI, Hoechst 33342)"
    assert issue.contradictions[1].expected == "2 channels (DNA, ACTIN)"
    assert issue.contradictions[1].found == "3 channels"


def test_p37h_an_empty_stated_name_is_recorded_as_read_under_the_statement(tmp_path):
    root = _tree(tmp_path / "exp", V41)
    _ome(root / "CTRL/rep1/acquisition_1/FOV001.ome.tif", ["DAPI", ""])
    report = _check(root, V41)
    assert report.ok, report.issues
    record = report.fast_images[UnitKey("CTRL", "rep1", "acquisition_1")][1]
    assert record["header_verified"] is False
    assert record["channels"] == [
        {"c": 0, "channel": "DNA", "header_name": "DAPI", "evidence": "header"},
        {"c": 1, "channel": "ACTIN", "header_name": "", "evidence": "statement"},
    ]


def test_p38b_a_new_contradicting_fov_in_a_fast_unit_is_the_only_image_named(tmp_path):
    root = _tree(tmp_path / "exp", V41, images=5)
    _ome(root / "CTRL/rep2/FOV009.ome.tif", ["Phalloidin-488", "DAPI"])
    report = _check(root, V41)
    assert report.codes == {FAST}
    assert set(_fast(report)) == {"CTRL/rep2/FOV009.ome.tif"}


def _rh1(path, condition):
    """P39 (a): CTRL scanner files (MINISBLACK, three contiguous samples), DrugA RGB headers."""
    _rgb(path, "minisblack" if condition == "CTRL" else "rgb")


def test_p39a_rgb_fast_mode_with_the_ctrl_override_is_accepted(tmp_path):
    root = _tree(tmp_path / "exp", V43, writer=_rh1)
    report = _check(root, V43)
    assert report.ok, report.issues
    assert UnitKey("CTRL", "rep1", "single") in report.image_type_overridden
    record = report.fast_images[UnitKey("DrugA", "rep1", "single")][0]
    assert not record["header_verified"]  # R, G and B take no header names (4.4b)
    assert [e["evidence"] for e in record["channels"]] == ["statement"] * 3


def test_p39b_a_drug_image_with_three_minisblack_samples_names_the_override(tmp_path):
    root = _tree(tmp_path / "exp", V43, writer=_rh1)
    _rgb(root / "DrugA/rep1/FOV009.ome.tif", "minisblack")
    report = _check(root, V43)
    assert report.codes == {FAST}
    issue = _fast(report)["DrugA/rep1/FOV009.ome.tif"]
    assert [c.kind for c in issue.contradictions] == [IMAGE_TYPE]
    assert "rgb_header_override" in issue.contradictions[0].found
    refusal = physical_issues(report.issues)[0]
    assert "rgb_header_override" in refusal.message and "channels_from" in refusal.action


def test_p39c_the_override_refusal_keeps_its_priority_and_is_not_listed_twice(tmp_path):
    root = _tree(tmp_path / "exp", V43, writer=_rh1)
    _rgb(root / "CTRL/rep1/FOV009.ome.tif", "minisblack", shape=(8, 8))
    report = _check(root, V43)
    assert report.codes == {"R_RGB_OVERRIDE_NOT_THREE_SAMPLES"}
    assert [i.path for i in report.issues] == ["CTRL/rep1/FOV009.ome.tif"]


def test_fast_unit_axes_stay_a_homogeneity_rule(tmp_path):
    root = _tree(tmp_path / "exp", V41)
    _ome(root / "CTRL/rep2/FOV009.ome.tif", MATCH, axes="CZYX")
    report = _check(root, V41)
    assert "R_UNIT_NOT_HOMOGENEOUS" in report.codes
    assert not report.codes - {"R_UNIT_NOT_HOMOGENEOUS", FAST}


def test_the_run_manifest_records_fast_layout_images_per_fast_unit(tmp_path):
    root, out = _tree(tmp_path / "exp", V41), tmp_path / "out"
    out.mkdir()
    (out / DESCRIPTION_FILENAME).write_bytes(V41)
    for index in range(2):
        _ome(root / f"CTRL/rep2/FOV{index:03d}.ome.tif", None, 2)
    result = run_described(root, MAPPING, output_folder=out)
    assert result.written, [(i.code, i.message) for i in result.issues if i.is_error]
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    entry = recorded_description(manifest["experiment_description"])
    units = {(u["condition"], u["repeat"], u["technical_repeat"]): u for u in entry["units"]}
    tagged = units[("DrugA", "rep2", "single")]
    assert tagged["channels_from"] == "unit" and FAST_IMAGES not in tagged
    unnamed = units[("CTRL", "rep2", "single")][FAST_IMAGES]
    assert [item["header_verified"] for item in unnamed] == [False, False]
    named = units[("CTRL", "rep1", "acquisition_1")][FAST_IMAGES]
    assert [item["header_verified"] for item in named] == [True, True]


def test_a_malformed_fast_layout_images_record_is_refused():
    unit = {
        "channels": ["DNA", "ACTIN"],
        "channels_from": "experiment",
        FAST_IMAGES: [
            {
                "path": "a.tif",
                "fov_key": FOV_KEY,
                "header_verified": True,  # contradicts the statement evidence below
                "channels": [
                    {"c": 0, "channel": "DNA", "header_name": "DAPI", "evidence": "header"},
                    {"c": 1, "channel": "ACTIN", "header_name": None, "evidence": "statement"},
                ],
            }
        ],
    }
    with pytest.raises(ValueError, match="header_verified"):
        checked_fast_images(unit, "units[0]")
    item = unit[FAST_IMAGES][0]
    item["header_verified"] = False
    checked_fast_images(unit, "units[0]")  # well formed now
    item["fov_key"] = {}  # experiment-v1 3.4: four fields
    with pytest.raises(ValueError, match="fov_key"):
        checked_fast_images(unit, "units[0]")
    item["fov_key"] = {**FOV_KEY, "position_id": ""}
    with pytest.raises(ValueError, match="fov_key"):
        checked_fast_images(unit, "units[0]")
    item["fov_key"] = FOV_KEY
    item["channels"][1] = {"c": 1, "channel": "ACTIN", "header_name": None, "evidence": "header"}
    with pytest.raises(ValueError, match="malformed channel entry"):  # header evidence, no name
        checked_fast_images(unit, "units[0]")
    item["channels"][1]["header_name"] = ""
    with pytest.raises(ValueError, match="malformed channel entry"):
        checked_fast_images(unit, "units[0]")
    unit["channels_from"] = "unit"
    with pytest.raises(ValueError, match="channels_from"):
        checked_fast_images(unit, "units[0]")


def test_an_image_with_an_unknown_channel_count_gets_no_record_and_is_unchecked(tmp_path):
    """Open question 5 of the first review: no per-index statement for an unchecked count."""
    root = _tree(tmp_path / "exp", V41)
    plain = root / "CTRL/rep2/FOV009.tif"  # no axis names, no confirmed roles: count unknown
    tifffile.imwrite(plain, np.zeros((2, 8, 8), np.uint8), photometric="minisblack", metadata=None)
    report = _check(root, V41)
    assert "CTRL/rep2/FOV009.tif" in report.unchecked
    assert FAST not in report.codes
    records = report.fast_images.get(UnitKey("CTRL", "rep2", "single"), ())
    assert "CTRL/rep2/FOV009.tif" not in [r["path"] for r in records]
