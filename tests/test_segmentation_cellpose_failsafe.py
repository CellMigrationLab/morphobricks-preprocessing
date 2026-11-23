"""A mask the Cellpose batch publishes is never left without its record entry.

Failures are injected right after publication (hashing), at the per-image record write and at
the final record write; each test checks every mask's SHA-256 on disk against its **latest**
record entry, the table, that the batch stops, and what a retry reports. Same stand-in and run
as ``test_segmentation_cellpose``.
"""

import pytest

from cellpose_stub import install
from morphobricks_preprocessing import segmentation_cellpose as cp
from morphobricks_preprocessing.segmentation_handoff import WRITE_LOCK_FILENAME, mask_path
from morphobricks_preprocessing.segmentation_record import (
    SegmentationError,
    mask_sha256,
    read_record,
)
from test_segmentation_cellpose import make_job


@pytest.fixture
def run(mixed_run):
    return mixed_run


@pytest.fixture
def stub(monkeypatch, tmp_path):
    return install(monkeypatch, tmp_path)


def verified(run):
    """Return the images with a mask, after checking each file against its latest entry.

    Every mask on disk has the SHA-256 of its latest record entry, and every image with a
    latest written entry has that mask on disk: no unrecorded mask, no stale record.
    """
    try:
        record = read_record(run.masks, "Cells")
    except SegmentationError:
        record = None
    on_disk, latest = {}, {}
    for fov in run.inputs.fovs():
        path = mask_path(run.masks, "Cells", fov)
        if path.exists():
            on_disk[fov.relative_path] = mask_sha256(path)  # never the patched one
        found = record.latest(fov.relative_path) if record is not None else None
        if found is not None:
            latest[fov.relative_path] = found[1].mask_sha256
    assert on_disk == latest
    hidden = [p for p in run.masks.rglob(".*") if p.is_file() and ".previous." in p.name]
    assert hidden == []  # no backup of an earlier mask left behind
    return set(on_disk)


def fail_from(monkeypatch, name, nth, error):
    """Make ``cp.<name>`` raise ``error`` from its ``nth`` call on (1 = the first)."""
    real, calls = getattr(cp, name), []

    def broken(*args, **kwargs):
        calls.append(1)
        if len(calls) >= nth:
            raise error
        return real(*args, **kwargs)

    monkeypatch.setattr(cp, name, broken)
    return real


def test_a_hash_that_fails_rolls_the_mask_back_and_stops_the_batch(run, stub, monkeypatch):
    fovs = run.inputs.fovs()
    fail_from(monkeypatch, "mask_sha256", 2, OSError("unreadable"))
    result = cp.run_batch(make_job(run), batch_id="b1")
    rows = {image: (status, message) for image, status, message in result.rows()}
    assert sorted(rows) == sorted(f.relative_path for f in fovs)  # every image accounted for
    assert rows[fovs[0].relative_path][0] == "written"
    status, message = rows[fovs[1].relative_path]
    assert status == "failed" and "unreadable" in message and cp.ROLLED_BACK_REMOVED in message
    for later in fovs[2:]:
        assert rows[later.relative_path] == ("failed", f"not started: {result.stopped}")
    assert "stopped at " + fovs[1].relative_path in result.stopped
    assert len(stub.calls) == 2  # the batch stopped: no third image was segmented
    assert verified(run) == {fovs[0].relative_path}  # no unrecorded mask
    assert read_record(run.masks, "Cells").batches[0] == result.batch


def test_a_record_write_that_fails_for_an_image_rolls_that_mask_back_and_stops(
    run, stub, monkeypatch
):
    fovs = run.inputs.fovs()
    fail_from(monkeypatch, "write_record", 2, OSError("disk full"))  # the 2nd image's record
    result = cp.run_batch(make_job(run), batch_id="b1")
    assert result.counts()["written"] == 1 and result.counts()["failed"] == len(fovs) - 1
    assert "disk full" in result.rows()[1][2] and cp.ROLLED_BACK_REMOVED in result.rows()[1][2]
    assert verified(run) == {fovs[0].relative_path}
    assert result.stopped and len(stub.calls) == 2


def test_a_failed_overwrite_after_earlier_images_restores_the_earlier_mask_and_record(
    run, stub, monkeypatch
):
    fovs = run.inputs.fovs()
    cp.run_batch(make_job(run), fovs=fovs[:3], batch_id="b1")
    before = {f.relative_path: mask_path(run.masks, "Cells", f).read_bytes() for f in fovs[:3]}
    real = cp.segment_image
    monkeypatch.setattr(cp, "segment_image", lambda *a: real(*a) * 3)  # other bytes
    fail_from(monkeypatch, "write_record", 3, OSError("disk full"))  # b2's 3rd image's record
    result = cp.run_batch(make_job(run, overwrite=True), batch_id="b2")
    rows = result.rows()
    assert [s for _, s, _ in rows[:2]] == ["written", "written"]
    assert rows[2][1] == "failed" and rows[2][2].endswith(cp.ROLLED_BACK_RESTORED)
    assert mask_path(run.masks, "Cells", fovs[2]).read_bytes() == before[fovs[2].relative_path]
    assert mask_path(run.masks, "Cells", fovs[0]).read_bytes() != before[fovs[0].relative_path]
    latest = read_record(run.masks, "Cells").latest(fovs[2].relative_path)
    assert latest[0].batch_id == "b1"  # the earlier record entry is still the latest
    assert verified(run) == {f.relative_path for f in fovs[:3]}
    assert result.stopped


def test_a_final_record_write_that_fails_leaves_only_recorded_masks(run, stub, monkeypatch):
    fovs = run.inputs.fovs()
    fail_from(monkeypatch, "write_record", len(fovs) + 1, OSError("disk full"))  # the final one
    with pytest.raises(OSError, match="disk full"):
        cp.run_batch(make_job(run), batch_id="b1")
    assert verified(run) == {f.relative_path for f in fovs}  # all recorded


def test_a_retry_reports_a_mask_without_provenance_instead_of_skipping_it(run, stub):
    fovs = run.inputs.fovs()
    cp.run_batch(make_job(run), fovs=fovs[:1], batch_id="b1")
    path = mask_path(run.masks, "Cells", fovs[1])  # a mask no recorded batch made
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(mask_path(run.masks, "Cells", fovs[0]).read_bytes())
    rows = {i: (s, m) for i, s, m in cp.run_batch(make_job(run), batch_id="b2").rows()}
    assert rows[fovs[0].relative_path] == ("skipped", cp.IMAGE_SKIPPED)
    assert rows[fovs[1].relative_path][0] == cp.NEEDS_ATTENTION  # never counted as skipped
    assert rows[fovs[1].relative_path][1].startswith(
        "exists without provenance (not made by a recorded batch)"
    )


def test_a_retry_after_a_stopped_batch_makes_the_missing_masks_and_skips_the_recorded(
    run, stub, monkeypatch
):
    fovs = run.inputs.fovs()
    real = fail_from(monkeypatch, "mask_sha256", 2, OSError("unreadable"))
    cp.run_batch(make_job(run), batch_id="b1")
    monkeypatch.setattr(cp, "mask_sha256", real)
    result = cp.run_batch(make_job(run), batch_id="b2")
    rows = {i: s for i, s, _ in result.rows()}
    assert rows[fovs[0].relative_path] == "skipped" and result.stopped == ""
    assert result.counts() == {
        "written": len(fovs) - 1,
        "skipped": 1,
        "needs_attention": 0,
        "failed": 0,
    }
    assert verified(run) == {f.relative_path for f in fovs}


def test_a_diameter_that_gives_no_flow_iteration_is_refused_not_raised_to_one():
    with pytest.raises(SegmentationError, match="less than one flow iteration"):
        cp.Settings(diameter=0.1)  # int(200 / (30 / 0.1)) is 0
    assert cp.flow_iterations(cp.Settings(diameter=0.15)) == 1
    with pytest.raises(SegmentationError, match="less than one flow iteration"):
        cp.Settings(diameter=1e-300)


def test_an_interrupt_between_publish_and_record_rolls_the_mask_back_and_propagates(
    run, stub, monkeypatch
):
    fovs = run.inputs.fovs()
    fail_from(monkeypatch, "mask_sha256", 2, KeyboardInterrupt())  # the stop button, image 2
    with pytest.raises(KeyboardInterrupt):
        cp.run_batch(make_job(run), batch_id="b1")
    assert verified(run) == {fovs[0].relative_path}  # no unrecorded mask


def test_the_hash_and_the_record_write_happen_under_the_write_lock(run, stub, monkeypatch):
    lock = run.masks / "Cells" / WRITE_LOCK_FILENAME
    seen = []
    real_hash, real_record = cp.mask_sha256, cp.write_record

    def hashing(path):
        seen.append(("hash", lock.exists()))
        return real_hash(path)

    def recording(*args):
        seen.append(("record", lock.exists()))
        return real_record(*args)

    monkeypatch.setattr(cp, "mask_sha256", hashing)
    monkeypatch.setattr(cp, "write_record", recording)
    cp.run_batch(make_job(run), fovs=run.inputs.fovs()[:1], batch_id="b1")
    assert seen[:2] == [("hash", True), ("record", True)]  # the final rewrite comes after


def test_a_retry_reports_a_recorded_mask_that_differs_from_its_record(run, stub):
    fovs = run.inputs.fovs()
    cp.run_batch(make_job(run), fovs=fovs[:2], batch_id="b1")
    changed = mask_path(run.masks, "Cells", fovs[1])
    changed.write_bytes(b"replaced after the batch")  # recorded, but no longer that file
    rows = {i: (s, m) for i, s, m in cp.run_batch(make_job(run), batch_id="b2").rows()}
    assert rows[fovs[0].relative_path] == ("skipped", cp.IMAGE_SKIPPED)
    status, message = rows[fovs[1].relative_path]
    assert status == cp.NEEDS_ATTENTION
    assert message.startswith("exists, but differs from its recorded provenance (sha256 ")
    assert cp.mask_sha256(changed) in message
