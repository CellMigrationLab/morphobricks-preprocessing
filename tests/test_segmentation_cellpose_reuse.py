"""A reused batch ID with other provenance is refused, never recorded as the old batch's.

``write_record`` refuses an existing batch ID whose model or settings differ. The callback's
recovery (the record was saved, then something failed) must not read that refusal as success
because the ID and the mask bytes happen to match: it needs the whole provenance of the batch.
Same stand-in and helpers as ``test_segmentation_cellpose_failsafe``.
"""

import pytest

from cellpose_stub import install
from morphobricks_preprocessing import segmentation_cellpose as cp
from morphobricks_preprocessing.segmentation_handoff import mask_path
from morphobricks_preprocessing.segmentation_record import read_record
from test_segmentation_cellpose import make_job
from test_segmentation_cellpose_commit import _fail_after_lock_release
from test_segmentation_cellpose_failsafe import verified


@pytest.fixture
def run(mixed_run):
    return mixed_run


@pytest.fixture
def stub(monkeypatch, tmp_path):
    return install(monkeypatch, tmp_path)


def test_a_reused_batch_id_with_other_settings_is_refused_and_the_mask_restored(run, stub):
    first = run.inputs.fovs()[0]
    cp.run_batch(make_job(run), fovs=[first], batch_id="same")
    path = mask_path(run.masks, "Cells", first)
    before_mask, before_record = path.read_bytes(), read_record(run.masks, "Cells")
    other = make_job(run, overwrite=True, settings=cp.Settings(batch_size=3))
    result = cp.run_batch(other, fovs=[first], batch_id="same")
    image, status, message = result.rows()[0]
    assert status == "failed" and cp.ROLLED_BACK_RESTORED in message
    assert result.stopped
    assert path.read_bytes() == before_mask  # the earlier mask, byte for byte
    assert read_record(run.masks, "Cells") == before_record  # latest entry unchanged
    assert verified(run) == {first.relative_path}


def test_a_reused_batch_id_with_another_model_is_refused_and_the_mask_restored(run, stub):
    first = run.inputs.fovs()[0]
    cp.run_batch(make_job(run), fovs=[first], batch_id="same")
    path = mask_path(run.masks, "Cells", first)
    before_mask, before_record = path.read_bytes(), read_record(run.masks, "Cells")
    other = make_job(run, overwrite=True, model="cpsam")
    result = cp.run_batch(other, fovs=[first], batch_id="same")
    assert result.rows()[0][1] == "failed" and result.stopped
    assert path.read_bytes() == before_mask
    assert read_record(run.masks, "Cells") == before_record


def test_a_reused_batch_id_that_leaves_out_earlier_images_is_refused(run, stub):
    fovs = run.inputs.fovs()
    cp.run_batch(make_job(run), fovs=fovs[:2], batch_id="same")
    path = mask_path(run.masks, "Cells", fovs[1])
    before_mask, before_record = path.read_bytes(), read_record(run.masks, "Cells")
    shorter = make_job(run, overwrite=True)  # same provenance, identical mask bytes
    result = cp.run_batch(shorter, fovs=fovs[1:2], batch_id="same")
    assert result.rows()[0][1] == "failed" and result.stopped
    assert path.read_bytes() == before_mask
    assert read_record(run.masks, "Cells") == before_record


def test_a_post_commit_error_with_the_complete_batch_keeps_the_mask(run, stub, monkeypatch):
    fovs = run.inputs.fovs()
    cp.run_batch(make_job(run), fovs=fovs[:1], batch_id="b1")
    _fail_after_lock_release(monkeypatch, 1, OSError("lock gone"))
    result = cp.run_batch(make_job(run, overwrite=True), fovs=fovs[:1], batch_id="b2")
    assert result.rows()[0][1] == "written" and "lock gone" in result.stopped
    assert read_record(run.masks, "Cells").latest(fovs[0].relative_path)[0].batch_id == "b2"
    assert verified(run) == {fovs[0].relative_path}  # latest SHA == file SHA


def test_a_post_commit_interrupt_with_the_complete_batch_keeps_the_mask(run, stub, monkeypatch):
    fovs = run.inputs.fovs()
    _fail_after_lock_release(monkeypatch, 1, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        cp.run_batch(make_job(run), fovs=fovs[:1], batch_id="b1")
    assert verified(run) == {fovs[0].relative_path}
