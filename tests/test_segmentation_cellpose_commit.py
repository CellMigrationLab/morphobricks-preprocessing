"""The record is saved, then something fails: the mask and its record entry stay together.

``write_record`` can write the file and then raise (an ``OSError`` releasing the lock, an
interrupt after the replace). The callback re-reads the record: if the image's latest entry is
this batch with this SHA-256 the mask is kept, an error stops the batch and an interrupt is
raised again with the image in the final record. Same stand-in and helpers as
``test_segmentation_cellpose_failsafe``.
"""

from contextlib import contextmanager

import pytest

from cellpose_stub import install
from morphobricks_preprocessing import segmentation_cellpose as cp
from morphobricks_preprocessing import segmentation_handoff, segmentation_record
from morphobricks_preprocessing.segmentation_handoff import mask_path
from morphobricks_preprocessing.segmentation_record import read_record
from test_segmentation_cellpose import make_job
from test_segmentation_cellpose_failsafe import verified


@pytest.fixture
def run(mixed_run):
    return mixed_run


@pytest.fixture
def stub(monkeypatch, tmp_path):
    return install(monkeypatch, tmp_path)


def _fail_after_lock_release(monkeypatch, nth, error):
    """Make the record lock raise ``error`` on release in its ``nth`` use: the file is saved."""
    real, calls = segmentation_record._record_lock, []

    @contextmanager
    def lock(path):
        calls.append(1)
        with real(path):
            yield
        if len(calls) == nth:
            raise error

    monkeypatch.setattr(segmentation_record, "_record_lock", lock)


def test_an_error_after_the_record_was_saved_keeps_the_mask_and_stops_the_batch(
    run,
    stub,
    monkeypatch,
):
    fovs = run.inputs.fovs()
    _fail_after_lock_release(monkeypatch, 2, OSError("lock gone"))  # the 2nd image's record
    result = cp.run_batch(make_job(run), batch_id="b1")
    rows = result.rows()
    assert rows[1][1] == "written"  # kept
    assert "lock gone" in result.stopped and rows[1][0] in result.stopped
    assert result.stopped and "lock gone" in result.stopped
    assert [s for _, s, _ in rows[2:]] == ["failed"] * (len(fovs) - 2)
    assert verified(run) == {f.relative_path for f in fovs[:2]}  # latest SHA == file SHA
    assert read_record(run.masks, "Cells").batches[0] == result.batch


def test_an_interrupt_after_the_record_was_saved_keeps_the_mask_and_is_raised(
    run,
    stub,
    monkeypatch,
):
    fovs = run.inputs.fovs()
    _fail_after_lock_release(monkeypatch, 2, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        cp.run_batch(make_job(run), batch_id="b1")
    assert verified(run) == {f.relative_path for f in fovs[:2]}
    entries = read_record(run.masks, "Cells").batches[0].images
    assert [e.image for e in entries] == [f.relative_path for f in fovs[:2]]


def test_an_interrupt_between_the_record_write_and_the_entry_does_not_roll_back(
    run,
    stub,
    monkeypatch,
):
    fovs = run.inputs.fovs()
    real, calls = cp._write_batch, []

    def write_batch(*args):
        result = real(*args)
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt  # recorded, before the callback noted it
        return result

    monkeypatch.setattr(cp, "_write_batch", write_batch)
    with pytest.raises(KeyboardInterrupt):
        cp.run_batch(make_job(run), batch_id="b1")
    assert verified(run) == {f.relative_path for f in fovs[:2]}


def test_an_interrupt_after_a_successful_commit_is_raised_again(run, stub, monkeypatch):
    fovs = run.inputs.fovs()
    real = segmentation_handoff._drop_backup

    def drop(backup, target):
        real(backup, target)
        if backup is not None:
            raise KeyboardInterrupt  # after the callback returned, before write_mask does

    cp.run_batch(make_job(run), fovs=fovs[:1], batch_id="b1")
    monkeypatch.setattr(segmentation_handoff, "_drop_backup", drop)
    with pytest.raises(KeyboardInterrupt):
        cp.run_batch(make_job(run, overwrite=True), fovs=fovs[:1], batch_id="b2")
    assert verified(run) == {fovs[0].relative_path}
    assert read_record(run.masks, "Cells").latest(fovs[0].relative_path)[0].batch_id == "b2"


def test_the_rollback_note_is_read_under_the_lock_not_before(run, stub, monkeypatch):
    fovs = run.inputs.fovs()
    cp.run_batch(make_job(run), fovs=fovs[:1], batch_id="b1")
    real = cp.write_mask

    def write_mask(*args, **kwargs):
        mask_path(run.masks, "Cells", fovs[0]).unlink()  # gone just before the write
        return real(*args, **kwargs)

    monkeypatch.setattr(cp, "write_mask", write_mask)
    monkeypatch.setattr(cp, "write_record", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    result = cp.run_batch(make_job(run, overwrite=True), fovs=fovs[:1], batch_id="b2")
    assert result.rows()[0][2].endswith(cp.ROLLED_BACK_REMOVED)  # a new mask: nothing restored
