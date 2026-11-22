"""The hand-off's name parity with the run and its per-type write lock (audit of PR #367).

Fixtures and helpers are those of ``test_segmentation_handoff``.
"""

import os

import pytest
import tifffile

from morphobricks_preprocessing.lock_file import exclusive_lock
from morphobricks_preprocessing.segmentation_handoff import (
    WRITE_LOCK_FILENAME,
    images_to_segment,
    mask_path,
    write_mask,
)
from morphobricks_preprocessing.segmentation_record import SegmentationError
from test_segmentation_handoff import labels_like
from test_segmentation_handoff import run as handoff_run

run = handoff_run  # the fixture of the hand-off tests, under its own name

# Name parity with the run, for every discovered type (audit B1) --------------------------------


@pytest.mark.parametrize("other", ["NA", "Cafe\u0301"])
def test_an_unrelated_type_the_run_would_refuse_is_refused_before_writing(run, other):
    first = run.inputs.fovs()[0]
    stray = run.masks / other / "Control" / "Biological_repeat_1" / "img0.tif"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"")
    code = "R_MASK_TYPE_NAME_INVALID" if other == "NA" else "R_FOLDER_NOT_NFC"
    with pytest.raises(SegmentationError, match=code):
        images_to_segment(run.inputs, "Cells", run.masks)
    with pytest.raises(SegmentationError, match=code):
        write_mask(run.masks, "Cells", first, labels_like(run, first), run.inputs)
    files = [p.relative_to(run.masks).as_posix() for p in run.masks.rglob("*") if p.is_file()]
    assert files == [stray.relative_to(run.masks).as_posix()]


# One write lock per type, both modes (audit B2) --------------------------------------------------


@pytest.mark.parametrize("overwrite", [False, True])
def test_a_held_write_lock_refuses_the_writer_in_both_modes(run, overwrite):
    fov = run.inputs.fovs()[0]
    lock = run.masks / "Cells" / WRITE_LOCK_FILENAME
    lock.parent.mkdir(parents=True)
    with exclusive_lock(lock):
        with pytest.raises(SegmentationError, match="is being written by process"):
            write_mask(
                run.masks, "Cells", fov, labels_like(run, fov), run.inputs, overwrite=overwrite
            )
        files = [p for p in run.masks.rglob("*") if p.is_file()]
        assert files == [lock]  # nothing was written, and the other writer's lock stays
    assert not lock.exists()


def test_a_default_writer_never_replaces_a_concurrent_overwriting_writers_mask(run, monkeypatch):
    fov = run.inputs.fovs()[0]
    target = mask_path(run.masks, "Cells", fov)
    real, seen = os.replace, {}

    def overwriting_writer_in_the_window(source, destination, **kwargs):
        if os.fspath(destination) == os.fspath(target) and "refused" not in seen:
            with pytest.raises(SegmentationError, match="is being written by"):
                write_mask(
                    run.masks, "Cells", fov, labels_like(run, fov, 9), run.inputs, overwrite=True
                )
            seen["refused"] = True
        return real(source, destination, **kwargs)

    monkeypatch.setattr(os, "replace", overwriting_writer_in_the_window)
    write_mask(run.masks, "Cells", fov, labels_like(run, fov, 4), run.inputs)
    assert seen["refused"]
    assert tifffile.imread(target).max() == 4
    assert os.listdir(target.parent) == [target.name]
    assert not (run.masks / "Cells" / WRITE_LOCK_FILENAME).exists()  # released at the end


def test_the_write_lock_is_released_after_a_refusal_and_is_not_a_mask(run):
    fov = run.inputs.fovs()[0]
    write_mask(run.masks, "Cells", fov, labels_like(run, fov), run.inputs)
    with pytest.raises(SegmentationError, match="already exists"):
        write_mask(run.masks, "Cells", fov, labels_like(run, fov), run.inputs)
    assert not (run.masks / "Cells" / WRITE_LOCK_FILENAME).exists()
    # A lock left in the type folder is skipped silently by the run's own masks discovery.
    (run.masks / "Cells" / WRITE_LOCK_FILENAME).write_text("{}", encoding="utf-8")
    assert images_to_segment(run.inputs, "Cells", run.masks) == run.inputs.fovs()[1:]


# on_published runs under the write lock ---------------------------------------------------------


def test_on_published_runs_under_the_lock_with_the_published_path(run):
    fov = run.inputs.fovs()[0]
    lock = run.masks / "Cells" / WRITE_LOCK_FILENAME
    seen = []

    def callback(path):
        seen.append((path, lock.exists(), path.is_file()))

    target = write_mask(
        run.masks, "Cells", fov, labels_like(run, fov), run.inputs, on_published=callback
    )
    assert seen == [(target, True, True)]
    assert target == mask_path(run.masks, "Cells", fov)
    assert not lock.exists()


def test_on_published_is_not_called_when_the_write_is_refused(run):
    fov = run.inputs.fovs()[0]
    write_mask(run.masks, "Cells", fov, labels_like(run, fov), run.inputs)
    calls = []
    with pytest.raises(SegmentationError, match="already exists"):
        write_mask(
            run.masks, "Cells", fov, labels_like(run, fov), run.inputs, on_published=calls.append
        )
    assert calls == []


def test_a_raising_on_published_propagates_and_the_mask_stays_published(run):
    fov = run.inputs.fovs()[0]

    def callback(path):
        raise RuntimeError("hash failed")

    with pytest.raises(RuntimeError, match="hash failed"):
        write_mask(
            run.masks, "Cells", fov, labels_like(run, fov), run.inputs, on_published=callback
        )
    assert mask_path(run.masks, "Cells", fov).is_file()
    assert not (run.masks / "Cells" / WRITE_LOCK_FILENAME).exists()  # the lock is released
