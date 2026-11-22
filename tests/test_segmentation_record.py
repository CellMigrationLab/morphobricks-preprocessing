"""The segmentation record (contract §15.5): an append-only history of batches.

Each batch keeps how its masks were made (tool, model and its SHA-256, parameters, channel IDs,
device, run and manifest SHA-256) and one entry per image with the SHA-256 of a written mask,
so a second batch with another model never erases the provenance of the first.
"""

import hashlib
import json

import pytest

from morphobricks_preprocessing.lock_file import LockHolder, exclusive_lock, read_holder
from morphobricks_preprocessing.segmentation_record import (
    RECORD_FILENAME,
    RECORD_LOCK_FILENAME,
    SegmentationBatch,
    SegmentationError,
    SegmentationRecord,
    SegmentedImage,
    mask_sha256,
    new_batch_id,
    read_record,
    write_record,
)

MASK = "ef" * 32


def batch(**changes):
    fields = {
        "batch_id": "2026-11-21T10:00:00Z",
        "tool": "cellpose",
        "tool_version": "4.2.1.1",
        "model_name": "cpsam_v2",
        "model_sha256": "ab" * 32,
        "model_file": "cpsam_v2.pt",
        "parameters": {"diameter": None, "flow_threshold": 0.4},
        "channel_ids": ("ACTIN", "DNA"),
        "device": "cpu",
        "run_folder": "/runs/out",
        "run_manifest_sha256": "cd" * 32,
        "images": (
            SegmentedImage("a/b.tif", "written", mask_sha256=MASK),
            SegmentedImage("a/c.tif", "skipped", "a mask was already there"),
            SegmentedImage("a/d.tif", "failed", "out of memory"),
        ),
    }
    return SegmentationBatch(**{**fields, **changes})


def second(**changes):
    """A later batch made with another model, which also wrote a/b.tif again."""
    fields = {
        "batch_id": "2026-11-22T09:00:00Z",
        "model_name": "cpdino",
        "model_sha256": "12" * 32,
        "model_file": None,
        "device": "cuda",
        "images": (SegmentedImage("a/b.tif", "written", mask_sha256="34" * 32),),
    }
    return batch(**{**fields, **changes})


def test_the_record_round_trips_through_its_file(tmp_path):
    first = batch()
    path = write_record(tmp_path, "Cells", first)
    assert path == tmp_path / "Cells" / RECORD_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["record_version"] == 1 and [b["batch_id"] for b in data["batches"]] == [
        first.batch_id
    ]
    original = SegmentationRecord("Cells", (first,))
    assert read_record(tmp_path, "Cells") == original
    assert SegmentationRecord.from_dict(original.to_dict()) == original
    assert b"\r" not in path.read_bytes()


def test_a_second_batch_with_another_model_keeps_both_provenances(tmp_path):
    write_record(tmp_path, "Cells", batch())
    write_record(tmp_path, "Cells", second())
    record = read_record(tmp_path, "Cells")
    assert [b.model_name for b in record.batches] == ["cpsam_v2", "cpdino"]
    assert [b.device for b in record.batches] == ["cpu", "cuda"]
    assert record.batches[0] == batch()  # the first batch is exactly as it was
    assert record.batches[0].images[0].mask_sha256 == MASK
    # The mask of a/b.tif was replaced by the second batch: it holds that batch's provenance.
    newest, entry = record.latest("a/b.tif")
    assert (newest.model_name, entry.mask_sha256) == ("cpdino", "34" * 32)
    assert record.latest("a/c.tif") is None and record.latest("nowhere.tif") is None


def test_the_same_batch_can_grow_but_never_change_or_lose_entries(tmp_path):
    early = batch(images=(SegmentedImage("a/b.tif", "written", mask_sha256=MASK),))
    write_record(tmp_path, "Cells", early)
    grown = batch(images=(*early.images, SegmentedImage("a/c.tif", "failed", "disk full")))
    write_record(tmp_path, "Cells", grown)
    assert read_record(tmp_path, "Cells").batches == (grown,)
    path = tmp_path / "Cells" / RECORD_FILENAME
    before = path.read_bytes()
    refused = {
        "another model": (batch(model_name="cpsam", images=grown.images), "made another way"),
        "other channels": (batch(channel_ids=("DNA",), images=grown.images), "made another way"),
        "entry dropped": (batch(images=early.images), "would lose"),
        "entry changed": (
            batch(images=(SegmentedImage("a/b.tif", "written", mask_sha256="99" * 32),)),
            "would lose",
        ),
    }
    for _name, (changed, message) in refused.items():
        with pytest.raises(SegmentationError, match=message):
            write_record(tmp_path, "Cells", changed)
        assert path.read_bytes() == before  # the record is exactly as it was


def test_an_image_twice_in_a_batch_is_refused_whatever_its_statuses():
    twice = (
        SegmentedImage("a/b.tif", "written", mask_sha256=MASK),
        SegmentedImage("a/b.tif", "failed", "out of memory"),
    )
    with pytest.raises(SegmentationError, match="more than one entry for a/b.tif"):
        batch(images=twice)
    with pytest.raises(SegmentationError, match="more than one entry"):
        batch(images=(twice[0], twice[0]))


def test_a_contradictory_duplicate_entry_in_a_file_is_refused_on_reading(tmp_path):
    path = write_record(tmp_path, "Cells", batch())
    data = json.loads(path.read_text(encoding="utf-8"))
    data["batches"][0]["images"].append(
        {"image": "a/b.tif", "status": "failed", "message": "x", "mask_sha256": None}
    )
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SegmentationError, match="more than one entry for a/b.tif"):
        read_record(tmp_path, "Cells")


def test_a_written_mask_needs_its_sha256_and_only_a_written_mask_has_one():
    with pytest.raises(SegmentationError, match="mask_sha256"):
        batch(images=(SegmentedImage("a/b.tif", "written"),))
    with pytest.raises(SegmentationError, match="mask_sha256"):
        batch(images=(SegmentedImage("a/b.tif", "written", mask_sha256="nope"),))
    with pytest.raises(SegmentationError, match="no mask_sha256"):
        batch(images=(SegmentedImage("a/b.tif", "skipped", mask_sha256=MASK),))


def test_mask_sha256_is_the_hash_of_the_files_bytes(tmp_path):
    path = tmp_path / "mask.tif"
    path.write_bytes(b"some mask bytes")
    assert mask_sha256(path) == hashlib.sha256(b"some mask bytes").hexdigest()


def test_the_same_batch_id_in_a_file_twice_is_refused(tmp_path):
    path = write_record(tmp_path, "Cells", batch())
    data = json.loads(path.read_text(encoding="utf-8"))
    data["batches"].append(data["batches"][0])
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SegmentationError, match="batch ID twice"):
        read_record(tmp_path, "Cells")


def test_an_unknown_record_version_is_refused(tmp_path):
    path = write_record(tmp_path, "Cells", batch())
    for version in (2, 0, "1", True, None):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["record_version"] = version
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(SegmentationError, match="version"):
            read_record(tmp_path, "Cells")
        with pytest.raises(SegmentationError, match="version"):
            write_record(tmp_path, "Cells", second())  # an unreadable record is never replaced


def test_a_record_that_is_not_valid_is_refused(tmp_path):
    path = write_record(tmp_path, "Cells", batch())
    good = json.loads(path.read_text(encoding="utf-8"))
    edits = {
        "device": lambda d: d["batches"][0].update(device="gpu"),
        "unknown key": lambda d: d.update(extra=1),
        "missing key": lambda d: d["batches"][0].pop("tool"),
        "model hash": lambda d: d["batches"][0]["model"].update(sha256="xyz"),
        "no channel": lambda d: d["batches"][0].update(channel_ids=[]),
        "status": lambda d: d["batches"][0]["images"][0].update(status="done"),
        "other type": lambda d: d.update(mask_type="Nuclei"),
        "missing value type": lambda d: d.update(mask_type="NA"),
    }
    for _name, edit in edits.items():
        data = json.loads(json.dumps(good))
        edit(data)
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(SegmentationError):
            read_record(tmp_path, "Cells")
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(SegmentationError, match="cannot be read"):
        read_record(tmp_path, "Cells")
    with pytest.raises(SegmentationError, match="no segmentation record"):
        read_record(tmp_path, "Nuclei")


def test_a_batch_cannot_be_built_without_what_it_needs():
    with pytest.raises(SegmentationError, match="failed without a message"):
        batch(images=(SegmentedImage("a.tif", "failed"),))
    with pytest.raises(SegmentationError, match="device"):
        batch(device="")
    with pytest.raises(SegmentationError, match="SHA-256"):
        batch(model_sha256="")
    with pytest.raises(SegmentationError, match="plain JSON"):
        batch(parameters={"diameter": float("nan")})
    with pytest.raises(SegmentationError, match="twice"):
        batch(channel_ids=("DNA", "DNA"))
    with pytest.raises(SegmentationError, match="batch_id"):
        batch(batch_id=" ")


@pytest.mark.parametrize(
    "name",
    [
        "NA",
        "na",
        "nan",
        "None",
        "null",
        "n/a",
        "<NA>",
        "",
        " Cells",
        "Cells ",
        "Café",
        "a/b",
        "..",
        ".",
        "a\\b",
    ],
)
def test_a_mask_type_preprocessing_would_refuse_is_refused_before_anything_is_written(
    tmp_path, name
):
    with pytest.raises(SegmentationError):
        write_record(tmp_path, name, batch())
    with pytest.raises(SegmentationError):
        read_record(tmp_path, name)
    with pytest.raises(SegmentationError):
        SegmentationRecord(name)
    assert list(tmp_path.iterdir()) == []


def test_a_decomposed_name_is_refused_but_its_composed_form_is_not(tmp_path):
    with pytest.raises(SegmentationError, match="NFC"):
        write_record(tmp_path, "Café", batch())
    write_record(tmp_path, "Caf\u00e9", batch())
    assert read_record(tmp_path, "Caf\u00e9").mask_type == "Caf\u00e9"


def test_write_record_refuses_a_record_of_another_type_on_disk(tmp_path):
    path = write_record(tmp_path, "Cells", batch())
    other = tmp_path / "Nuclei"
    other.mkdir()
    (other / RECORD_FILENAME).write_bytes(path.read_bytes())  # a Cells record in Nuclei/
    with pytest.raises(SegmentationError, match="is of mask type 'Cells', not 'Nuclei'"):
        write_record(tmp_path, "Nuclei", second())


def test_a_held_lock_refuses_the_write_and_leaves_the_record_unchanged(tmp_path):
    path = write_record(tmp_path, "Cells", batch())
    before = path.read_bytes()
    lock = path.with_name(RECORD_LOCK_FILENAME)
    lock.write_text('{"host": "another-pc", "pid": 7, "started_at": ""}\n', encoding="utf-8")
    with pytest.raises(SegmentationError, match="process 7 on another-pc"):
        write_record(tmp_path, "Cells", second())
    assert path.read_bytes() == before
    assert lock.exists()  # the other writer's lock is never removed by a refused writer
    lock.unlink()
    write_record(tmp_path, "Cells", second())
    assert len(read_record(tmp_path, "Cells").batches) == 2
    assert not lock.exists()  # released after a successful write


def test_the_lock_is_released_when_the_write_is_refused(tmp_path):
    write_record(tmp_path, "Cells", batch())
    with pytest.raises(SegmentationError, match="another way"):
        write_record(tmp_path, "Cells", batch(tool="other"))
    assert not (tmp_path / "Cells" / RECORD_LOCK_FILENAME).exists()


def test_a_lock_another_writer_took_meanwhile_is_never_removed(tmp_path):
    lock = tmp_path / RECORD_LOCK_FILENAME
    with exclusive_lock(lock):
        lock.unlink()  # the user deleted this writer's lock, and another writer took it
        lock.write_text('{"host": "another-pc", "pid": 7, "started_at": ""}\n', encoding="utf-8")
    assert read_holder(lock) == LockHolder("another-pc", 7)  # still the other writer's


def test_a_holder_carries_a_random_token_and_an_older_lock_without_one_still_reads(tmp_path):
    lock = tmp_path / RECORD_LOCK_FILENAME
    with exclusive_lock(lock) as holder:
        assert len(holder.token) == 32 and read_holder(lock) == holder
    with exclusive_lock(lock) as again:
        assert again.token != holder.token
    lock.write_text('{"host": "old-pc", "pid": 7, "started_at": "x"}\n', encoding="utf-8")
    assert read_holder(lock) == LockHolder("old-pc", 7, "x", "")


def test_a_lock_retaken_by_the_same_process_in_the_same_second_is_never_removed(tmp_path):
    lock = tmp_path / RECORD_LOCK_FILENAME
    with exclusive_lock(lock) as first:
        lock.unlink()
        with exclusive_lock(lock) as second:  # same host, PID and second; another holding
            assert second.token != first.token
        lock.write_text(json.dumps({**first.__dict__, "token": "other"}), encoding="utf-8")
    assert lock.exists()  # the first holder's exit did not remove the other's lock


def test_two_batches_made_in_the_same_second_get_different_ids(tmp_path, monkeypatch):
    class Frozen:
        @staticmethod
        def now(tz=None):
            return real.now(tz).replace(microsecond=0, second=0)

    from morphobricks_preprocessing import segmentation_record as module

    real = module.datetime
    monkeypatch.setattr(module, "datetime", Frozen)
    first, again = new_batch_id(), new_batch_id()
    assert first != again
    assert first.rsplit("-", 1)[0] == again.rsplit("-", 1)[0]  # the same stamp, another suffix
    write_record(tmp_path, "Cells", batch(batch_id=first))
    write_record(tmp_path, "Cells", batch(batch_id=again, tool_version="9"))  # no ID clash
    assert [b.batch_id for b in read_record(tmp_path, "Cells").batches] == [first, again]
