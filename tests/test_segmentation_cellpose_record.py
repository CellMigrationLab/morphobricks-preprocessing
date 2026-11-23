"""What the Cellpose batch records (full normalisation, flow iterations, downloads, notes, ID).

Split from ``test_segmentation_cellpose.py``; the stand-in and the run are the same.
"""

import dataclasses
import re
import sys

import pytest

from cellpose_stub import custom_model, install
from morphobricks_preprocessing import segmentation_cellpose as cp
from morphobricks_preprocessing import segmentation_cellpose_model as model_module
from morphobricks_preprocessing.segmentation_record import SegmentationError
from test_segmentation_cellpose import make_job


@pytest.fixture
def run(mixed_run):
    return mixed_run


@pytest.fixture
def stub(monkeypatch, tmp_path):
    return install(monkeypatch, tmp_path)


def test_the_record_says_whether_the_weights_were_downloaded_and_from_where(stub, tmp_path):
    url = "https://huggingface.co/mouseland/cellpose-sam/resolve/main/"
    on_disk = cp.load_model("cpsam", "cpu")
    assert (on_disk.downloaded, on_disk.source) == (False, url + "cpsam")
    (stub.models_dir / "cpsam").unlink()
    fetched = cp.load_model("cpsam", "cpu", allow_download=True)
    assert (fetched.downloaded, fetched.source) == (True, url + "cpsam")
    params = cp.inference_parameters(fetched, cp.Settings(), ("DNA",))
    assert params["weights_downloaded"] is True and params["weights_source"] == url + "cpsam"
    own = cp.load_model(str(custom_model(tmp_path)), "cpu")
    assert (own.downloaded, own.source) == (False, None)


def test_the_full_normalisation_is_read_from_cellpose_now_copied_and_recorded(run, stub):
    models = sys.modules["cellpose.models"]
    models.normalize_default["tile_norm_blocksize"] = 100  # as set by another cell or session
    models.normalize_default["normalize"] = False  # Cellpose's eval writes this global itself
    full = cp.eval_arguments(cp.load_model("cpsam_v2", "cpu"), cp.Settings())["normalize"]
    assert full["tile_norm_blocksize"] == 100 and full["normalize"] is True  # the user's wins
    assert set(full) == set(stub.normalize_default) and full["percentile"] == [1.0, 99.0]
    assert models.normalize_default["percentile"] is None  # the global is not written to
    cp.run_batch(make_job(run), batch_id="b1")
    assert stub.calls[0].kwargs["normalize"] == full


def test_the_flow_iterations_are_cellpose_derivation_given_and_recorded(stub):
    loaded = cp.load_model("cpsam_v2", "cpu")
    assert cp.eval_arguments(loaded, cp.Settings())["niter"] == 200
    assert cp.eval_arguments(loaded, cp.Settings(diameter=60))["niter"] == 400  # int(200 / 0.5)


def test_a_gpu_batch_records_that_cellpose_may_use_the_cpu_for_post_processing(stub):
    stub.cuda = True
    on_gpu = cp.inference_parameters(cp.load_model("cpsam_v2", "cuda"), cp.Settings(), ("DNA",))
    assert on_gpu["notes"] == [model_module.DEVICE_NOTE]
    assert "mps" in model_module.DEVICE_NOTE and "not refused" in model_module.DEVICE_NOTE
    on_cpu = cp.inference_parameters(cp.load_model("cpsam_v2", "cpu"), cp.Settings(), ("DNA",))
    assert on_cpu["notes"] == []


def test_the_default_batch_id_is_the_records_own_unique_id(run, stub):
    first = cp.run_batch(make_job(run)).batch.batch_id
    second = cp.run_batch(make_job(run, overwrite=True)).batch.batch_id
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ-[0-9a-f]{8}", first)
    assert first != second  # two batches in the same second never clash


def test_a_job_without_resolved_channels_is_refused_before_anything_runs(run, stub):
    with pytest.raises(SegmentationError, match="No channels are resolved"):
        dataclasses.replace(make_job(run), plan=None)
    assert stub.calls == []


def test_an_error_while_writing_the_record_never_hides_the_error_that_interrupted(
    run, stub, monkeypatch
):
    def interrupt(stack):
        if len(stub.calls) == 2:  # one image is done, so there is a record to write
            raise KeyboardInterrupt

    real, calls = cp.write_record, []

    def broken(*args):
        calls.append(args)
        if len(calls) > 1:  # the record after the first image was written; the next one fails
            raise OSError("disk full")
        return real(*args)

    stub.fail = interrupt
    monkeypatch.setattr(cp, "write_record", broken)
    with pytest.raises(KeyboardInterrupt) as raised:
        cp.run_batch(make_job(run))
    assert "disk full" in " ".join(raised.value.__notes__)
