"""The built-in weights (#574): pinned, downloaded on first use, checked; one torch warning hidden.

Nothing is downloaded here: the stand-in (``cellpose_stub.install``) answers the brick's
download with its own bytes, whose SHA-256 and size it makes the pins.
"""

import email.message
import hashlib
import re
import urllib.error
import warnings

import numpy as np
import pytest

from cellpose_stub import BUILTINS, install, weights_bytes
from morphobricks_preprocessing import segmentation_cellpose as cp
from morphobricks_preprocessing import segmentation_cellpose_weights as weights_module
from morphobricks_preprocessing.segmentation_record import SegmentationError, read_record
from test_segmentation_cellpose import make_job

SPARSE_MESSAGE = (
    "Sparse invariant checks are implicitly disabled. Memory errors (e.g. SEGFAULT) will occur "
    "when operating on a sparse tensor which violates the invariants, but checks are disabled. "
    "See torch.sparse.check_sparse_tensor_invariants.enable for more information."
)
"""torch's warning, as Cellpose 4.2.1.1's ``dynamics.py`` triggers it."""


@pytest.fixture
def run(mixed_run):
    return mixed_run


@pytest.fixture
def stub(monkeypatch, tmp_path):
    return install(monkeypatch, tmp_path)


# the pins ---------------------------------------------------------------------------------------


def test_every_builtin_model_has_a_pinned_file_at_a_pinned_revision():
    assert tuple(weights_module.PINNED_WEIGHTS) == cp.BUILTIN_MODELS == BUILTINS
    assert re.fullmatch("[0-9a-f]{40}", cp.WEIGHTS_REVISION)
    for pin in weights_module.PINNED_WEIGHTS.values():
        assert re.fullmatch("[0-9a-f]{64}", pin.sha256) and pin.size > 100_000_000
    url = cp.weights_url("cpsam_v2")
    assert url == (
        f"https://huggingface.co/mouseland/cellpose-sam/resolve/{cp.WEIGHTS_REVISION}/cpsam_v2"
    )
    assert "/main/" not in url  # never the moving branch Cellpose itself downloads from


# the download: only the chosen model's file, with its progress -------------------------------


def test_only_the_chosen_models_weights_are_downloaded_with_their_progress(stub):
    for name in BUILTINS:
        (stub.models_dir / name).unlink(missing_ok=True)
    told = []
    loaded = cp.load_model("cpsam", "cpu", progress=lambda *a: told.append(a))
    assert stub.downloaded == ["cpsam"] and stub.urls == [cp.weights_url("cpsam")]
    assert sorted(p.name for p in stub.models_dir.iterdir()) == ["cpsam"]  # no partial file
    size = len(weights_bytes("cpsam"))
    assert told == [
        ("Downloading cpsam weights…", 0, size),
        ("Downloading cpsam weights…", size, size),
    ]
    assert (loaded.repository, loaded.revision) == (cp.WEIGHTS_REPOSITORY, cp.WEIGHTS_REVISION)
    assert stub.cellpose_downloaded == []  # Cellpose was given the file, never a name


def test_a_copy_on_disk_shows_no_progress_and_is_not_downloaded(stub):
    told = []
    loaded = cp.load_model("cpsam_v2", "cpu", progress=lambda *a: told.append(a))
    assert told == [] and stub.downloaded == [] and not loaded.downloaded


# refusals: the model and the source named, nothing substituted, nothing left -----------------


def _http_error(url, code, reason):
    return urllib.error.HTTPError(url, code, reason, email.message.Message(), None)


@pytest.mark.parametrize(
    ("failure", "said"),
    [
        (urllib.error.URLError("blocked by the proxy"), "blocked by the proxy"),
        (TimeoutError("timed out"), "timed out"),
        ("http", "the server answered 403 Forbidden"),
    ],
)
def test_a_failed_or_blocked_download_is_a_refusal_naming_the_model_and_the_source(
    stub, failure, said
):
    (stub.models_dir / "cpsam").unlink()
    url = cp.weights_url("cpsam")
    stub.download_fail = _http_error(url, 403, "Forbidden") if failure == "http" else failure
    with pytest.raises(SegmentationError) as caught:
        cp.load_model("cpsam", "cpu")
    message = str(caught.value)
    assert "weights of cpsam could not be downloaded from " + url in message
    assert said in message and "no other model is used" in message
    assert stub.constructed == []  # nothing loaded, cpsam_v2 (on disk) not used instead
    assert not (stub.models_dir / "cpsam").exists()
    assert not [p for p in stub.models_dir.iterdir() if p.name.endswith(".part")]


def test_downloaded_bytes_that_are_not_the_pinned_file_are_refused_and_not_kept(stub, monkeypatch):
    (stub.models_dir / "cpsam").unlink()
    monkeypatch.setitem(
        weights_module.PINNED_WEIGHTS, "cpsam", weights_module.PinnedFile("0" * 64, 16)
    )
    with pytest.raises(SegmentationError, match="cpsam could not be downloaded") as caught:
        cp.load_model("cpsam", "cpu")
    assert "the pinned file has 16 bytes and SHA-256 " + "0" * 64 in str(caught.value)
    assert stub.constructed == [] and sorted(p.name for p in stub.models_dir.iterdir()) == [
        "cpsam_v2"
    ]


def test_a_copy_on_disk_that_is_not_the_pinned_file_is_refused_not_replaced(stub):
    (stub.models_dir / "cpsam").write_bytes(b"older weights of cpsam")
    with pytest.raises(SegmentationError, match="not the pinned file") as caught:
        cp.load_model("cpsam", "cpu")
    assert str(stub.models_dir / "cpsam") in str(caught.value)
    assert hashlib.sha256(b"older weights of cpsam").hexdigest() in str(caught.value)
    assert stub.constructed == [] and stub.downloaded == []
    assert (stub.models_dir / "cpsam").read_bytes() == b"older weights of cpsam"


def test_a_model_file_of_ones_own_is_never_checked_against_a_pin_or_downloaded(stub, tmp_path):
    from cellpose_stub import custom_model

    loaded = cp.load_model(str(custom_model(tmp_path)), "cpu")
    assert stub.downloaded == [] and loaded.source is None and loaded.revision is None


def test_the_written_record_names_the_weights_source_revision_and_hash(run, stub):
    (stub.models_dir / "cpsam_v2").unlink()
    result = cp.run_batch(make_job(run), batch_id="b1")
    (batch,) = read_record(run.masks, "Cells").batches
    assert batch == result.batch
    assert batch.model_sha256 == hashlib.sha256(weights_bytes("cpsam_v2")).hexdigest()
    params = batch.parameters
    assert params["weights_source"] == cp.weights_url("cpsam_v2")
    assert params["weights_repository"] == "mouseland/cellpose-sam"
    assert params["weights_revision"] == cp.WEIGHTS_REVISION
    assert params["weights_downloaded"] is True


# the torch warning (#574, item 5) -------------------------------------------------------------


def _warn(message, module, category=UserWarning):
    def warn(_stack):
        warnings.warn_explicit(message, category, "dynamics.py", 541, module=module)

    return warn


def _evaluate(stub):
    loaded = cp.load_model("cpsam_v2", "cpu")
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        before = list(warnings.filters)
        cp.evaluate(loaded, cp.Settings(), np.zeros((2, 8, 8), np.uint16))
        assert warnings.filters == before  # the filter was only there during the call
    return [str(w.message) for w in seen]


@pytest.mark.parametrize("module", ["cellpose.dynamics", "torch", "torch.sparse"])
def test_the_sparse_invariant_warning_is_hidden_while_cellpose_runs(stub, module):
    stub.fail = _warn(SPARSE_MESSAGE, module)
    assert _evaluate(stub) == []


@pytest.mark.parametrize(
    ("message", "module", "category"),
    [
        (SPARSE_MESSAGE, "my_analysis", UserWarning),  # another module
        (SPARSE_MESSAGE, "cellposeextra", UserWarning),  # not the cellpose package
        (SPARSE_MESSAGE, "cellpose.dynamics", RuntimeWarning),  # another category
        ("Something else from torch", "torch", UserWarning),  # another message
        ("Note: " + SPARSE_MESSAGE, "cellpose.dynamics", UserWarning),  # not its start
    ],
)
def test_every_other_warning_is_still_shown(stub, message, module, category):
    stub.fail = _warn(message, module, category)
    assert _evaluate(stub) == [message]


def test_the_warning_is_shown_again_outside_the_cellpose_call(stub):
    _evaluate(stub)
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        _warn(SPARSE_MESSAGE, "cellpose.dynamics")(None)
    assert [str(w.message) for w in seen] == [SPARSE_MESSAGE]
