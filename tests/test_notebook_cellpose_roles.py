"""Channel roles in the Cellpose notebook: cell channel(s), then an optional nuclear channel.

Owner, 23 November 2026, like ZeroCostDL4Mic's "channel to segment" and "nuclear channel": the
roles only label and order the channels chosen by ID; they are resolved by ID as before, stacked
cell channels first, at most three in all, and the record names each channel's role.
"""

import numpy as np
import pytest

from cellpose_stub import install
from morphobricks_preprocessing import notebook_cellpose
from morphobricks_preprocessing import segmentation_cellpose as cp
from morphobricks_preprocessing.segmentation_record import SegmentationError, read_record
from test_notebook_cellpose import BATCH, MODEL, open_and_choose, run_cell
from test_segmentation_cellpose import make_job


@pytest.fixture
def cells(mixed_run, monkeypatch, tmp_path):
    state = install(monkeypatch, tmp_path)
    return type("C", (), {"ns": {}, "stub": state, "run": mixed_run})()


def test_the_picker_asks_for_cell_channels_then_a_nuclear_channel(cells):
    picker = open_and_choose(cells, ids=("TUB", "ACTIN", "", "DNA"))
    labels = [d.description for d in picker.dropdowns]
    assert labels == ["Cell channel", "Cell channel 2", "Cell channel 3", "Nuclear channel"]
    assert picker.dropdowns[3] is picker.nuclear and picker.nuclear.value == "DNA"
    assert picker.channel_ids == ("TUB", "ACTIN", "DNA")
    assert picker.channel_roles == ("cell", "cell", "nuclear")
    assert "TUB (cell), ACTIN (cell), DNA (nuclear)" in picker.message.value
    picker.nuclear.value = ""  # the nuclear channel is optional
    assert picker.channel_ids == ("TUB", "ACTIN") and picker.plan is not None


def test_cellpose_gets_the_cell_channels_first_and_the_record_names_each_role(cells):
    picker = open_and_choose(cells, ids=("ACTIN", "", "", "DNA"))
    run_cell(cells.ns, MODEL, device="cpu")
    run_cell(cells.ns, BATCH)
    fov = cells.run.inputs.fovs()[0]
    stack = cells.stub.calls[0].x
    expected = picker.plan.read(fov)
    assert np.array_equal(stack, expected) and stack.shape[0] == 2
    batch = read_record(cells.run.masks, "Cells").batches[0]
    assert batch.channel_ids == ("ACTIN", "DNA")
    assert batch.parameters["channel_roles"] == [
        {"channel_id": "ACTIN", "role": "cell"},
        {"channel_id": "DNA", "role": "nuclear"},
    ]


def test_more_than_three_channels_in_all_is_the_existing_refusal(cells):
    picker = open_and_choose(cells, ids=("TUB", "ACTIN", "EXTRA", "DNA"))
    assert picker.plan is None
    assert picker.refusal.startswith("Cellpose 4.2.1.1 reads at most 3 channels")
    with pytest.raises(notebook_cellpose.CellStop, match="at most 3 channels"):
        run_cell(cells.ns, MODEL, device="cpu")


def test_the_same_channel_as_cell_and_nuclear_is_refused_by_the_resolver(cells):
    picker = open_and_choose(cells, ids=("DNA", "", "", "DNA"))
    assert picker.plan is None and picker.refusal == "Name at least one channel ID, each once."


@pytest.mark.parametrize(
    ("ids", "roles"),
    [
        (("DNA", "TUB"), ("nuclear", "cell")),  # nuclear first
        (("DNA", "TUB"), ("nuclear", "nuclear")),  # no cell channel
        (("DNA", "TUB", "ACTIN"), ("cell", "nuclear", "nuclear")),  # two nuclear
        (("DNA", "TUB"), ("cell",)),  # not one for one
        (("DNA",), ("membrane",)),  # unknown
    ],
)
def test_roles_that_do_not_fit_the_channels_are_refused(ids, roles):
    with pytest.raises(SegmentationError):
        cp.channel_roles_entry(ids, roles)


def test_a_job_without_roles_records_none_never_guessed(mixed_run, monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    result = cp.run_batch(make_job(mixed_run), batch_id="b1")
    assert result.batch.parameters["channel_roles"] is None
    with pytest.raises(SegmentationError, match="nuclear channel, in that order"):
        make_job(mixed_run, channel_roles=("nuclear", "cell"))
