"""Phase-aware progress of the folder check, and the raw header names (Stage 1 design, PR 6)."""

import json
from dataclasses import replace

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    prefill_mapping,
    preflight,
    read_manifest,
    run_experiment,
)
from morphobricks_preprocessing.manifest import FileEntry
from morphobricks_preprocessing.run.phases import PHASES, progress_text
from tiff_tree import write_data, write_tiff
from zarr_tree import write_ngff


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    for rel in ("ctrl/rep1/a.tif", "ctrl/rep1/b.tif", "drug/rep1/a.tif"):
        write_tiff(root / rel)
    return root


def collect(root, **kwargs):
    calls = []
    pre = preflight(root, progress=lambda *call: calls.append(call), **kwargs)
    return pre, calls


def test_the_phases_are_announced_in_order(experiment):
    _, calls = collect(experiment)
    order = [phase for phase, *_ in calls]
    assert [p for p in PHASES if p in order] == list(PHASES)
    assert order == sorted(order, key=PHASES.index)
    for phase in PHASES:
        mine = [call for call in calls if call[0] == phase]
        assert mine[0][1] == 0 and mine[-1][1] == mine[-1][2]


def test_the_headers_phase_counts_every_image_with_its_path(experiment):
    _, calls = collect(experiment)
    headers = [call for call in calls if call[0] == "headers"]
    reading = [(done, total, path) for _, done, total, path in headers if path]
    assert reading == [
        (0, 3, "ctrl/rep1/a.tif"),
        (1, 3, "ctrl/rep1/b.tif"),
        (2, 3, "drug/rep1/a.tif"),
    ]
    assert headers[-1] == ("headers", 3, 3, "")


def test_excluded_images_are_not_counted(experiment):
    _, calls = collect(experiment, exclude=["drug/rep1/a.tif"])
    assert {total for phase, _, total, _ in calls if phase == "headers"} == {2}


def test_progress_text_words_each_phase():
    assert progress_text("folders", 1, 1) == "1/4 folders"
    assert progress_text("headers", 120, 480) == "2/4 headers 120/480"
    assert progress_text("masks", 0, 1) == "3/4 masks"
    assert progress_text("acquisitions", 1, 1) == "4/4 acquisitions"
    with pytest.raises(ValueError, match="unknown phase"):
        progress_text("pixels", 0, 1)


@pytest.mark.parametrize("bind", [False, True])
def test_progress_changes_nothing_about_the_result(experiment, bind):
    plain = preflight(experiment, bind_headers=bind)
    with_progress, _ = collect(experiment, bind_headers=bind)
    assert with_progress.metadata == plain.metadata
    assert with_progress.discovery == plain.discovery
    assert with_progress.issues == plain.issues
    assert with_progress.header_fingerprints == plain.header_fingerprints
    assert with_progress.dataset == plain.dataset


def test_the_masks_phase_runs_with_a_masks_folder(experiment, tmp_path):
    masks = tmp_path / "masks"
    write_data(masks / "Nuclei" / "ctrl" / "rep1" / "a.tif", np.ones((8, 8), np.uint16))
    pre, calls = collect(experiment, masks_folder=masks)
    assert pre.masks is not None and [c for c in calls if c[0] == "masks"]


# Raw header names ---------------------------------------------------------------------


def write_named(path, names):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(
        path,
        np.zeros((len(names), 6, 7), np.uint16),
        ome=True,
        photometric="minisblack",
        metadata={"axes": "CYX", "Channel": {"Name": names}},
    )


def test_an_image_without_names_has_no_raw_names_and_no_manifest_key(experiment, tmp_path):
    out = tmp_path / "out"
    run_experiment(experiment, prefill_mapping("YX"), output_folder=out)
    data = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert all("raw_channel_names" not in entry for entry in data["files"])
    assert all(f.raw_channel_names == () for f in read_manifest(out / "run_manifest.json").files)


def test_raw_names_keep_outer_whitespace_and_unnamed_channels(tmp_path):
    root = tmp_path / "exp"
    write_named(root / "ctrl" / "a.ome.tif", ["DAPI ", " GFP"])
    pre = preflight(root)
    header = pre.metadata.files[0]
    assert header.raw_channel_names == ("DAPI ", " GFP")
    assert header.channel_names == ("DAPI", "GFP")


def test_raw_names_round_trip_through_the_manifest(tmp_path):
    root = tmp_path / "exp"
    write_named(root / "ctrl" / "a.ome.tif", ["DAPI ", " GFP"])
    out = tmp_path / "out"
    run_experiment(root, prefill_mapping("CYX"), output_folder=out)
    data = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert data["files"][0]["raw_channel_names"] == ["DAPI ", " GFP"]
    assert data["files"][0]["channel_names"] == ["DAPI", "GFP"]
    assert read_manifest(out / "run_manifest.json").files[0].raw_channel_names == ("DAPI ", " GFP")


def test_a_malformed_recorded_raw_name_list_is_refused(tmp_path):
    root = tmp_path / "exp"
    write_named(root / "ctrl" / "a.ome.tif", ["A", "B"])
    out = tmp_path / "out"
    run_experiment(root, prefill_mapping("CYX"), output_folder=out)
    path = out / "run_manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["files"][0]["raw_channel_names"] = [1, 2]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="raw channel names"):
        read_manifest(path)


def test_raw_names_do_not_decide_whether_a_file_is_unchanged():
    entry = FileEntry("a.tif", 1, 2, ("c", "r", "t"), "YX", raw_channel_names=("A",))
    assert entry.same_source(replace(entry, raw_channel_names=("B",)))


def test_ome_zarr_raw_labels_are_untrimmed(tmp_path):
    path = write_ngff(
        tmp_path / "exp/ctrl/a.ome.zarr",
        np.zeros((2, 6, 8), np.uint16),
        "cyx",
        channel_names=[" DAPI", "GFP"],
    )
    from morphobricks_preprocessing.metadata import inspect_image

    header = inspect_image(path, "ctrl/a.ome.zarr")
    assert header.raw_channel_names == (" DAPI", "GFP")
    assert header.channel_names == ("DAPI", "GFP")
