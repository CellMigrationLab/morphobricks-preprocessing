"""Cellpose's own fallback download and the bytes it loads (external review bc of #582, B6).

Cellpose 4.2.1.1 calls ``cache_model_path("cpsam_v2")`` (a download from the moving ``main``
branch) for a path that does not exist when it looks, after this brick's last check. And a
weights file rewritten in place with its size and modification time kept matched every value
the brick compared. Now Cellpose loads a private copy hashed as it is made, and may fetch
nothing itself while it loads.
"""

import os
import sys
from pathlib import Path

import pytest

from cellpose_stub import custom_model, install, weights_bytes
from morphobricks_preprocessing import segmentation_cellpose as cp
from morphobricks_preprocessing import segmentation_cellpose_guard as guard
from morphobricks_preprocessing import segmentation_cellpose_model as model_module
from morphobricks_preprocessing import segmentation_cellpose_weights as weights_module
from morphobricks_preprocessing.segmentation_record import SegmentationError


@pytest.fixture
def stub(monkeypatch, tmp_path):
    return install(monkeypatch, tmp_path)


@pytest.mark.parametrize("model", ["cpdino", "cpsam", "custom"])
def test_a_path_gone_inside_cellpose_starts_no_download_of_its_own(stub, tmp_path, model):
    # The race of the review: the file disappears after every check, at constructor entry;
    # no other built-in model is cached, so Cellpose would download cpsam_v2.
    for name in ("cpsam_v2", "cpsam"):
        (stub.models_dir / name).unlink(missing_ok=True)
    request = str(custom_model(tmp_path)) if model == "custom" else model
    stub.on_construct = lambda given: Path(given).unlink()
    original = sys.modules["cellpose.models"].cache_model_path
    with pytest.raises(SegmentationError, match="tried to fetch weights by itself"):
        cp.load_model(request, "cpu")
    assert stub.cellpose_downloaded == []  # nothing fetched by Cellpose
    assert not (stub.models_dir / "cpsam_v2").exists()
    assert stub.downloaded == ([] if model in ("custom",) else [model])  # only the chosen
    # The guard is put back after the refusal.
    assert sys.modules["cellpose.models"].cache_model_path is original


def test_a_download_refused_even_when_cellpose_catches_it(stub, monkeypatch):
    models = sys.modules["cellpose.models"]
    real = models.CellposeModel

    def swallowing(*args, **kwargs):
        try:
            models.cache_model_path("cpsam_v2")
        except Exception:  # noqa: BLE001 - Cellpose could catch it: the guard still refuses
            pass
        return real(*args, **kwargs)

    monkeypatch.setattr(models, "CellposeModel", swallowing)
    with pytest.raises(guard.CellposeDownloadError):
        cp.load_model("cpsam", "cpu")
    assert stub.cellpose_downloaded == []


def test_a_cellpose_without_its_download_functions_is_refused(stub, monkeypatch):
    monkeypatch.delattr(sys.modules["cellpose.utils"], "download_url_to_file")
    with pytest.raises(SegmentationError, match="cannot be refused"):
        cp.load_model("cpsam", "cpu")
    assert stub.constructed == []


def _same_size_rewrite(path):
    """Rewrite ``path`` in place: same size, the modification time set back."""
    stat = path.stat()
    data = bytearray(path.read_bytes())
    data[0] ^= 0x01
    path.write_bytes(bytes(data))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def test_a_rewrite_in_place_after_the_hash_is_refused(stub, monkeypatch):
    real = model_module.fetch_weights

    def fetch_then_rewrite(*args, **kwargs):
        found = real(*args, **kwargs)
        _same_size_rewrite(found.path)
        return found

    monkeypatch.setattr(model_module, "fetch_weights", fetch_then_rewrite)
    with pytest.raises(SegmentationError, match="changed after it was checked"):
        cp.load_model("cpsam", "cpu")
    assert stub.constructed == []


def test_a_rewrite_while_the_private_copy_is_made_is_refused(stub, monkeypatch):
    real = guard._copy_hashed

    def copy_then_rewrite(source, target):
        found = real(source, target)
        _same_size_rewrite(source)
        return found

    monkeypatch.setattr(guard, "_copy_hashed", copy_then_rewrite)
    with pytest.raises(SegmentationError, match="changed after it was checked"):
        cp.load_model("cpsam", "cpu")
    assert stub.constructed == []


def test_weights_replaced_right_after_the_download_are_refused(stub, monkeypatch):
    (stub.models_dir / "cpsam").unlink()
    real = weights_module._download

    def download_then_replace(name, url, target, pin, progress):
        found = real(name, url, target, pin, progress)
        data = bytearray(target.read_bytes())
        data[0] ^= 0x01
        target.unlink()
        target.write_bytes(bytes(data))  # another writer's file, same size
        return found

    monkeypatch.setattr(weights_module, "_download", download_then_replace)
    with pytest.raises(SegmentationError, match="does not have the SHA-256"):
        cp.load_model("cpsam", "cpu")
    assert stub.constructed == []


def test_a_custom_model_is_hashed_before_it_is_loaded_and_the_hash_is_of_the_bytes_loaded(
    stub, tmp_path
):
    import hashlib

    path = custom_model(tmp_path)
    loaded = cp.load_model(str(path), "cpu")
    (built,) = stub.constructed
    assert built.loaded_bytes == weights_bytes("my_model")
    assert loaded.sha256 == hashlib.sha256(built.loaded_bytes).hexdigest()


def test_a_custom_model_gone_before_its_identity_is_taken_is_refused(stub, tmp_path, monkeypatch):
    path = custom_model(tmp_path)
    real = model_module.choose_model

    def choose_then_lose(model):
        found = real(model)
        path.unlink()
        return found

    monkeypatch.setattr(model_module, "choose_model", choose_then_lose)
    with pytest.raises(SegmentationError, match="changed after it was checked"):
        cp.load_model(str(path), "cpu")
    assert stub.constructed == [] and stub.cellpose_downloaded == []


def test_the_identity_holds_the_status_change_time(tmp_path):
    path = tmp_path / "w"
    path.write_bytes(b"x")
    found = os.stat(path)
    assert weights_module.file_identity(path)[-1] == found.st_ctime_ns
    assert len(weights_module.file_identity(path)) == 5


def test_the_private_copy_is_deleted_after_a_failed_load(stub, monkeypatch, tmp_path):
    made = []
    real = guard._copy_hashed

    def spy(source, target):
        made.append(target)
        return real(source, target)

    monkeypatch.setattr(guard, "_copy_hashed", spy)
    stub.substitute = str(stub.models_dir / "cpsam_v2")
    with pytest.raises(SegmentationError, match="substituted"):
        cp.load_model("cpsam", "cpu")
    assert made and not made[0].exists() and not made[0].parent.exists()
