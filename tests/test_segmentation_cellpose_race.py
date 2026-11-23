"""Two overlapping Cellpose batches never leave a mask whose latest record entry is another's.

The mask, its SHA-256 and its record entry are one step under the type's write lock: a second
batch is refused inside that window (audit of PR #375, blockers 1-3). Interleavings are
orchestrated in one process with the stand-in of ``test_segmentation_cellpose``.
"""

import pytest

from cellpose_stub import install
from morphobricks_preprocessing import segmentation_cellpose as cp
from morphobricks_preprocessing.segmentation_handoff import mask_path
from morphobricks_preprocessing.segmentation_record import mask_sha256, read_record
from test_segmentation_cellpose import make_job
from test_segmentation_cellpose_failsafe import verified


@pytest.fixture
def run(mixed_run):
    return mixed_run


@pytest.fixture
def stub(monkeypatch, tmp_path):
    return install(monkeypatch, tmp_path)


def latest(run, fov):
    found = read_record(run.masks, "Cells").latest(fov.relative_path)
    return found[0].batch_id, found[1].mask_sha256


def test_an_overwrite_in_the_window_after_publish_is_refused_until_the_record_is_written(
    run, stub, monkeypatch
):
    fov = run.inputs.fovs()[0]
    path = mask_path(run.masks, "Cells", fov)
    real_record, real_segment = cp.write_record, cp.segment_image
    other = []  # B's result

    def b_in_the_window(*args):
        if not other:  # A has published and hashed; its record entry is not written yet
            other.append(None)
            monkeypatch.setattr(cp, "segment_image", lambda *a: real_segment(*a) * 5)
            other[0] = cp.run_batch(make_job(run, overwrite=True), fovs=[fov], batch_id="B1")
            monkeypatch.setattr(cp, "segment_image", real_segment)
        return real_record(*args)

    monkeypatch.setattr(cp, "write_record", b_in_the_window)
    cp.run_batch(make_job(run), fovs=[fov], batch_id="A")
    ((_, status, message),) = other[0].rows()
    assert status == "failed" and "is being written by process" in message  # refused by the lock
    assert latest(run, fov) == ("A", mask_sha256(path))
    monkeypatch.setattr(cp, "segment_image", lambda *a: real_segment(*a) * 5)
    cp.run_batch(make_job(run, overwrite=True), fovs=[fov], batch_id="B2")  # after A: allowed
    assert latest(run, fov) == ("B2", mask_sha256(path))
    assert verified(run) == {fov.relative_path}


def test_a_batch_that_would_not_be_the_newest_entry_is_rolled_back(run, stub, monkeypatch):
    fovs = run.inputs.fovs()
    real_segment = cp.segment_image
    calls = []

    def b_between_two_images_of_a(loaded, settings, stack):
        calls.append(1)
        if len(calls) == 2:  # A recorded image 0; B overwrites image 1 before A writes it
            monkeypatch.setattr(cp, "segment_image", lambda *a: real_segment(*a) * 5)
            cp.run_batch(make_job(run, overwrite=True), fovs=fovs[1:2], batch_id="B")
            monkeypatch.setattr(cp, "segment_image", b_between_two_images_of_a)
        return real_segment(loaded, settings, stack)

    monkeypatch.setattr(cp, "segment_image", b_between_two_images_of_a)
    result = cp.run_batch(make_job(run, overwrite=True), fovs=fovs[:2], batch_id="A")
    status, message = result.rows()[1][1:]
    assert status == "failed" and "would not be the newest" in message
    assert message.endswith(cp.ROLLED_BACK_RESTORED)
    assert latest(run, fovs[1]) == ("B", mask_sha256(mask_path(run.masks, "Cells", fovs[1])))
    assert verified(run) == {f.relative_path for f in fovs[:2]}


@pytest.mark.parametrize("overwrite", [False, True])
def test_an_interrupt_during_the_record_append_rolls_back(run, stub, monkeypatch, overwrite):
    fovs = run.inputs.fovs()
    if overwrite:
        cp.run_batch(make_job(run), fovs=fovs[:2], batch_id="b0")
    before = mask_path(run.masks, "Cells", fovs[1]).read_bytes() if overwrite else None
    real_record, real_segment, calls = cp.write_record, cp.segment_image, []

    def interrupted(*args):
        calls.append(1)
        if len(calls) == 2:  # the stop button while image 1's record is being written
            raise KeyboardInterrupt
        return real_record(*args)

    monkeypatch.setattr(cp, "segment_image", lambda *a: real_segment(*a) * 3)
    monkeypatch.setattr(cp, "write_record", interrupted)
    with pytest.raises(KeyboardInterrupt):
        cp.run_batch(make_job(run, overwrite=overwrite), batch_id="b1")
    path = mask_path(run.masks, "Cells", fovs[1])
    assert (path.read_bytes() == before) if overwrite else not path.exists()
    expected = fovs[:2] if overwrite else fovs[:1]
    assert verified(run) == {f.relative_path for f in expected}
