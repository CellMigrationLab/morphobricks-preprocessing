"""A mask the Cellpose batch publishes is never left without its record entry.

Failures are injected right after publication (hashing), at the per-image record write and at
the final record write; each test checks the masks on disk against the record, the table, that
the batch stops, and what a retry reports. Same stand-in and run as ``test_segmentation_cellpose``.
"""

import pytest

from cellpose_stub import install
from morphobricks_preprocessing import segmentation_cellpose as cp
from morphobricks_preprocessing.segmentation_handoff import mask_path
from morphobricks_preprocessing.segmentation_record import SegmentationError, read_record
from test_segmentation_cellpose import make_job


@pytest.fixture
def run(mixed_run):
    return mixed_run


@pytest.fixture
def stub(monkeypatch, tmp_path):
    return install(monkeypatch, tmp_path)


def on_disk(run):
    return {f.relative_path for f in run.inputs.fovs() if mask_path(run.masks, "Cells", f).exists()}


def recorded(run):
    try:
        record = read_record(run.masks, "Cells")
    except SegmentationError:
        return set()
    return {i.image for b in record.batches for i in b.images if i.status == "written"}


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


def test_a_hash_that_fails_withdraws_the_mask_and_stops_the_batch(run, stub, monkeypatch):
    fovs = run.inputs.fovs()
    fail_from(monkeypatch, "mask_sha256", 2, OSError("unreadable"))
    result = cp.run_batch(make_job(run), batch_id="b1")
    rows = {image: (status, message) for image, status, message in result.rows()}
    assert sorted(rows) == sorted(f.relative_path for f in fovs)  # every image accounted for
    assert rows[fovs[0].relative_path][0] == "written"
    status, message = rows[fovs[1].relative_path]
    assert status == "failed" and "unreadable" in message and "was removed" in message
    for later in fovs[2:]:
        assert rows[later.relative_path] == ("failed", f"not started: {result.stopped}")
    assert "stopped at " + fovs[1].relative_path in result.stopped
    assert len(stub.calls) == 2  # the batch stopped: no third image was segmented
    assert on_disk(run) == recorded(run) == {fovs[0].relative_path}  # no unrecorded mask
    assert read_record(run.masks, "Cells").batches[0] == result.batch


def test_a_record_write_that_fails_for_an_image_withdraws_that_mask_and_stops(
    run, stub, monkeypatch
):
    fovs = run.inputs.fovs()
    fail_from(monkeypatch, "write_record", 2, OSError("disk full"))  # the 2nd image's record
    result = cp.run_batch(make_job(run), batch_id="b1")
    assert result.counts()["written"] == 1 and result.counts()["failed"] == len(fovs) - 1
    assert "disk full" in result.rows()[1][2] and "was removed" in result.rows()[1][2]
    assert on_disk(run) == recorded(run) == {fovs[0].relative_path}
    assert result.stopped and len(stub.calls) == 2


def test_a_mask_that_is_no_longer_the_one_written_is_not_removed_and_is_named(
    run, stub, monkeypatch
):
    fovs = run.inputs.fovs()
    other = mask_path(run.masks, "Cells", fovs[0])

    def replaced_meanwhile(*args):
        other.write_bytes(b"someone else's mask")  # another writer replaces our file
        raise OSError("disk full")

    monkeypatch.setattr(cp, "write_record", replaced_meanwhile)
    result = cp.run_batch(make_job(run), fovs=fovs[:1], batch_id="b1")
    message = result.rows()[0][2]
    assert other.read_bytes() == b"someone else's mask"  # never removed
    assert "WITHOUT provenance" in message and str(other) in message
    assert result.stopped


def test_a_final_record_write_that_fails_leaves_only_recorded_masks(run, stub, monkeypatch):
    fovs = run.inputs.fovs()
    fail_from(monkeypatch, "write_record", len(fovs) + 1, OSError("disk full"))  # the final one
    with pytest.raises(OSError, match="disk full"):
        cp.run_batch(make_job(run), batch_id="b1")
    assert on_disk(run) == recorded(run) == {f.relative_path for f in fovs}  # all recorded


def test_a_retry_reports_a_mask_without_provenance_instead_of_skipping_it(run, stub):
    fovs = run.inputs.fovs()
    cp.run_batch(make_job(run), fovs=fovs[:1], batch_id="b1")
    path = mask_path(run.masks, "Cells", fovs[1])  # a mask no recorded batch made
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(mask_path(run.masks, "Cells", fovs[0]).read_bytes())
    rows = {i: (s, m) for i, s, m in cp.run_batch(make_job(run), batch_id="b2").rows()}
    assert rows[fovs[0].relative_path] == ("skipped", cp.IMAGE_SKIPPED)
    assert rows[fovs[1].relative_path][0] == "skipped"
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
    assert result.counts() == {"written": len(fovs) - 1, "skipped": 1, "failed": 0}
    assert on_disk(run) == recorded(run) == {f.relative_path for f in fovs}


def test_a_diameter_that_gives_no_flow_iteration_is_refused_not_raised_to_one():
    with pytest.raises(SegmentationError, match="less than one flow iteration"):
        cp.Settings(diameter=0.1)  # int(200 / (30 / 0.1)) is 0
    assert cp.flow_iterations(cp.Settings(diameter=0.15)) == 1
    with pytest.raises(SegmentationError, match="less than one flow iteration"):
        cp.Settings(diameter=1e-300)
