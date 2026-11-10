"""Images of a described run whose header names no channel (or not every one) are listed.

Owner decision 2026-11-08, on the P26 per-unit header checks: such an image passes (§4.3, fixture
v12: it needs no evidence, the user's confirmed layout is used) and is *listed* in the manifest's
``experiment_description`` entry and the report text, not warned about. A run without a
description records nothing of this.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
import tifffile

from described_tree import run_described
from description_tree import MAPPING, build_experiment
from morphobricks_preprocessing import RunInputs
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.discovery import discover_experiment
from morphobricks_preprocessing.manifest import ManifestFormatError, read_manifest
from morphobricks_preprocessing.metadata import inspect_images
from morphobricks_preprocessing.physical_check import UnitKey, check_physical
from spec_pin import fixtures

V01 = next(f for f in fixtures("valid") if f.name.startswith("v01_"))
DESCRIPTION = description_from_bytes(V01.data)
FIELD = "images_without_header_channel_names"
NAMES = ["DAPI", "Phalloidin-488"]  # the confirmed header names of v01's DNA and ACTIN


def _named(path, names):
    tifffile.imwrite(
        path,
        np.random.default_rng(3).integers(0, 200, (len(names), 8, 8), dtype=np.uint8),
        ome=True,
        photometric="minisblack",
        metadata={"axes": "CYX", "Channel": {"Name": names}},
    )


def _run(tmp_path, *, described=True):
    """One image per unit: CTRL/rep1 named, DrugA/rep1 partly, CTRL/rep2 none, DrugA/rep2 named."""
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir()
    paths = build_experiment(root, DESCRIPTION, images=1)
    by_unit = {p.rsplit("/", 1)[0]: p for p in paths}
    _named(root / by_unit["CTRL/rep1"], NAMES)
    _named(root / by_unit["DrugA/rep1"], [NAMES[0], ""])
    _named(root / by_unit["DrugA/rep2"], NAMES)
    if described:
        (out / "morphobricks.toml").write_bytes(V01.data)
    return root, out, by_unit


def _entry(out):
    return json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))[
        "experiment_description"
    ]


def _listed(entry):
    return {(u["condition"], u["repeat"]): u[FIELD] for u in entry["units"] if FIELD in u}


def test_the_run_lists_the_unnamed_and_the_partly_named_image_exactly(tmp_path):
    root, out, by_unit = _run(tmp_path)
    assert run_described(root, MAPPING, output_folder=out).written
    entry = _entry(out)
    assert len(entry["units"]) == 4
    listed = _listed(entry)
    assert listed == {
        ("DrugA", "rep1"): [
            {"image": by_unit["DrugA/rep1"], "header_names": "partial", "channel_positions": [1]}
        ],
        ("CTRL", "rep2"): [
            {"image": by_unit["CTRL/rep2"], "header_names": "none", "channel_positions": [0, 1]}
        ],
    }
    assert read_manifest(out / "run_manifest.json").experiment_description == entry
    inputs = RunInputs.open(out)
    assert inputs.images_without_header_channel_names() == {
        by_unit["DrugA/rep1"]: (1,),
        by_unit["CTRL/rep2"]: (0, 1),
    }


def test_the_report_text_and_json_show_them(tmp_path):
    root, out, by_unit = _run(tmp_path)
    run_described(root, MAPPING, output_folder=out)
    text = (out / "discovery_report.txt").read_text(encoding="utf-8")
    assert "Images without header channel names: 2 images in 2 units" in text
    assert f"{by_unit['CTRL/rep2']}: no channel named (channel positions 0, 1" in text
    assert f"{by_unit['DrugA/rep1']}: unnamed positions (channel positions 1" in text
    assert by_unit["CTRL/rep1"] not in text and by_unit["DrugA/rep2"] not in text
    data = json.loads((out / "discovery_report.json").read_text(encoding="utf-8"))
    assert {u["condition"] for u in data[FIELD]} == {"DrugA", "CTRL"}
    assert data["issues"] == []  # listed, not warned


def test_a_fully_named_run_lists_nothing_and_has_no_such_key(tmp_path):
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir()
    for path in build_experiment(root, DESCRIPTION, images=1):
        _named(root / path, NAMES)
    (out / "morphobricks.toml").write_bytes(V01.data)
    assert run_described(root, MAPPING, output_folder=out).written
    entry = _entry(out)
    assert not any(FIELD in unit for unit in entry["units"])  # omitted, never an empty list
    data = json.loads((out / "discovery_report.json").read_text(encoding="utf-8"))
    assert data[FIELD] == []
    assert "without header channel names" not in (out / "discovery_report.txt").read_text()
    assert RunInputs.open(out).images_without_header_channel_names() == {}


@pytest.mark.parametrize(
    "bad",
    [
        [],
        "x",
        [{"image": "a.tif"}],
        [{"image": "a.tif", "header_names": "some", "channel_positions": [0]}],
        [{"image": "a.tif", "header_names": "none", "channel_positions": [1, 0]}],
        [{"image": "a.tif", "header_names": "none", "channel_positions": [True]}],
        [{"image": "a.tif", "header_names": "none", "channel_positions": []}],
        [
            {"image": "b.tif", "header_names": "none", "channel_positions": [0]},
            {"image": "a.tif", "header_names": "none", "channel_positions": [0]},
        ],
    ],
)
def test_a_malformed_list_is_refused_on_read(tmp_path, bad):
    root, out, _ = _run(tmp_path)
    run_described(root, MAPPING, output_folder=out)
    data = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    data["experiment_description"]["units"][0][FIELD] = bad
    (out / "run_manifest.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ManifestFormatError, match=FIELD):
        read_manifest(out / "run_manifest.json")


def test_the_physical_report_gives_the_positions_per_unit(tmp_path):
    root, _, by_unit = _run(tmp_path)
    discovery = discover_experiment(root)
    files = inspect_images(root, discovery.images).files
    report = check_physical(
        DESCRIPTION, discovery, files, roles={f.relative_path: "CYX" for f in files}
    )
    assert report.ok
    assert dict(report.unnamed) == {
        UnitKey("DrugA", "rep1", "single"): {by_unit["DrugA/rep1"]: (1,)},
        UnitKey("CTRL", "rep2", "single"): {by_unit["CTRL/rep2"]: (0, 1)},
    }
