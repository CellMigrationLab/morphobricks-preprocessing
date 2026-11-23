"""Channels by number in the Cellpose notebook (#440), beside the default channel tags.

A channel number (C position, 1-based in the widget) is allowed only when every image of the
run has the same channel IDs at the same C positions. It is translated once to that channel
ID, resolved by ID like a tag, and the record keeps the ID with the number typed. When the
layouts differ (3 channels in some repeats, 4 in others) the number is refused with the
differing images named, even where the position would happen to hold the same stain: a
number is never resolved image by image.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from cellpose_stub import install, two_channel_run
from morphobricks_preprocessing import notebook_cellpose
from morphobricks_preprocessing import segmentation_cellpose as cp
from morphobricks_preprocessing.run_inputs import RunInputs
from morphobricks_preprocessing.segmentation_cellpose_run import (
    channel_by_number,
    channel_layouts,
    channel_number_from_text,
)
from morphobricks_preprocessing.segmentation_record import SegmentationError, read_record
from test_notebook_cellpose import BATCH, CHANNELS, MODEL, OPEN, run_cell
from test_stage3_channels import described_run, extra_stain


def extra_last_run(tmp_path):
    """DNA, ACTIN, TUB everywhere, and EXTRA fourth in Biological_repeat_2 only (3 / 4 channels)."""
    result, out, _ = described_run(tmp_path, extra_stain())
    assert result.written, [(i.code, i.message) for i in result.issues]
    return SimpleNamespace(inputs=RunInputs.open(out), out=out, masks=out.parent / "masks")


@pytest.fixture
def stub(monkeypatch, tmp_path):
    return install(monkeypatch, tmp_path)


def choose_by_number(namespace, run, output, cell, nuclear):
    """Open ``run``, choose ``output`` in cell 4, then the channels by number."""
    run_cell(namespace, OPEN, run_folder=str(run.out), masks_folder=str(run.masks))
    run_cell(namespace, CHANNELS, output=output)
    picker = namespace["workflow"].picker
    assert picker.by.value == "tag"  # tags stay the default
    picker.by.value = "number"
    picker.cell_number.value, picker.nuclear_number.value = cell, nuclear
    return picker


def test_a_number_on_a_uniform_run_is_recorded_with_its_channel_id(stub, tmp_path):
    run, namespace = two_channel_run(tmp_path / "run"), {}
    picker = choose_by_number(namespace, run, "cells", "2", "1")
    assert picker.channel_ids == ("TUB", "DNA") and picker.numbers == {"TUB": 2, "DNA": 1}
    assert "TUB (cell, channel number 2), DNA (nuclear, channel number 1)" in (picker.message.value)
    assert "1 = DNA, 2 = TUB, the same in every image" in namespace["workflow"].shown[0]
    run_cell(namespace, MODEL, device="cpu")
    run_cell(namespace, BATCH)
    fov = run.inputs.fovs()[0]
    assert np.array_equal(stub.calls[0].x, picker.plan.read(fov))  # resolved by ID
    batch = read_record(run.masks, "Cells").batches[0]
    assert batch.channel_ids == ("TUB", "DNA")
    assert batch.parameters["channel_roles"] == [
        {"channel_id": "TUB", "role": "cell", "channel_number": 2},
        {"channel_id": "DNA", "role": "nuclear", "channel_number": 1},
    ]


def test_numbers_serve_the_output_both_too(stub, tmp_path):
    run, namespace = two_channel_run(tmp_path / "run"), {}
    choose_by_number(namespace, run, "both", "2", "1")
    run_cell(namespace, MODEL, device="cpu")
    run_cell(namespace, BATCH)
    nuclei = read_record(run.masks, "Nuclei").batches[0]
    assert nuclei.parameters["channel_roles"] == [
        {"channel_id": "DNA", "role": "nuclear", "channel_number": 1}
    ]


def test_a_number_on_a_mixed_3_and_4_channel_run_is_refused_naming_the_images(stub, tmp_path):
    run, namespace = extra_last_run(tmp_path), {}
    layouts = channel_layouts(run.inputs)
    assert list(layouts) == [("DNA", "ACTIN", "TUB"), ("DNA", "ACTIN", "TUB", "EXTRA")]
    # channel 1 is DNA in every image, yet the number is refused: never resolved per image
    picker = choose_by_number(namespace, run, "nuclei", "", "1")
    assert picker.plan is None and picker.targets == ()
    refusal = picker.refusal
    assert refusal.startswith("Channel number 1 cannot be used")
    assert "Choose the channel by its tag (channel ID) instead" in refusal
    for image in layouts[("DNA", "ACTIN", "TUB", "EXTRA")]:
        assert image in refusal  # each differing image is named
    assert "Biological_repeat_2" in refusal and "4 = EXTRA: 4 image(s)" in refusal
    assert "1 = DNA, 2 = ACTIN, 3 = TUB: 8 image(s)" in refusal
    assert "differ between images: choose the channels by tag" in namespace["workflow"].shown[0]
    with pytest.raises(notebook_cellpose.CellStop) as stopped:
        run_cell(namespace, MODEL, device="cpu")
    assert str(stopped.value) == refusal and stub.constructed == []
    picker.by.value = "tag"  # the same channel by its tag is accepted
    picker.nuclear.value = "DNA"
    assert picker.channel_ids == ("DNA",) and picker.numbers == {}


def test_a_number_is_refused_where_extra_comes_first_in_one_repeat(mixed_run):
    with pytest.raises(SegmentationError, match="cannot be used") as refused:
        channel_by_number(mixed_run.inputs, 2)
    assert "1 = EXTRA, 2 = DNA" in str(refused.value)


@pytest.mark.parametrize(
    ("text", "match"),
    [("abc", "not a channel number"), ("0", "not a channel number"), ("1.5", "not a channel")],
)
def test_a_number_that_is_not_a_channel_number_is_refused(text, match):
    with pytest.raises(SegmentationError, match=match):
        channel_number_from_text(text)
    assert channel_number_from_text(" ") is None and channel_number_from_text(" 2 ") == 2


def test_a_number_beyond_the_channels_is_refused(tmp_path):
    run = two_channel_run(tmp_path)
    assert channel_by_number(run.inputs, 1) == "DNA" and channel_by_number(run.inputs, 2) == "TUB"
    with pytest.raises(SegmentationError, match="images have 2 channels \\(1 = DNA, 2 = TUB\\)"):
        channel_by_number(run.inputs, 3)


def test_a_number_for_a_channel_not_read_is_refused():
    with pytest.raises(SegmentationError, match="not among the channels read"):
        cp.channel_roles_entry(("DNA",), ("nuclear",), "nuclei", {"TUB": 2})
    assert cp.channel_roles_entry(("DNA",), ("nuclear",), "nuclei", {"DNA": 1}) == [
        {"channel_id": "DNA", "role": "nuclear", "channel_number": 1}
    ]
