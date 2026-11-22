"""``write_mask`` rolls back when its ``on_published`` callback fails (audit of PR #375).

The caller writes its provenance in the callback, under the type's write lock. If that fails,
an overwritten mask is restored byte for byte and a new mask is removed, still under the lock.
Fixtures and helpers are those of ``test_segmentation_handoff``.
"""

import os

import pytest

from morphobricks_preprocessing import segmentation_handoff
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


def _files(run):
    return sorted(p.relative_to(run.masks).as_posix() for p in run.masks.rglob("*") if p.is_file())


def _fail(error):
    def callback(path):
        assert (path.parent.parents[1] / WRITE_LOCK_FILENAME).exists()  # under the type lock
        raise error

    return callback


@pytest.mark.parametrize("error", [RuntimeError("record failed"), KeyboardInterrupt()])
def test_a_failing_callback_removes_a_new_mask(run, error):
    fov = run.inputs.fovs()[0]
    with pytest.raises(type(error)):
        write_mask(
            run.masks, "Cells", fov, labels_like(run, fov), run.inputs, on_published=_fail(error)
        )
    assert _files(run) == []  # no mask, no backup, no temporary file, no lock
    assert fov in images_to_segment(run.inputs, "Cells", run.masks)


@pytest.mark.parametrize("error", [RuntimeError("record failed"), KeyboardInterrupt()])
def test_a_failing_callback_restores_the_overwritten_mask_byte_for_byte(run, error):
    fov = run.inputs.fovs()[0]
    target = write_mask(run.masks, "Cells", fov, labels_like(run, fov), run.inputs)
    before = target.read_bytes()
    stat = os.stat(target)
    new = labels_like(run, fov)
    new[0, 0] = 7  # different bytes
    with pytest.raises(type(error)):
        write_mask(
            run.masks, "Cells", fov, new, run.inputs, overwrite=True, on_published=_fail(error)
        )
    assert target.read_bytes() == before
    assert os.stat(target).st_mode == stat.st_mode
    assert _files(run) == [target.relative_to(run.masks).as_posix()]  # no backup left


def test_a_successful_overwrite_leaves_no_backup_and_the_backup_is_hidden(run):
    fov = run.inputs.fovs()[0]
    target = write_mask(run.masks, "Cells", fov, labels_like(run, fov), run.inputs)
    seen = []

    def callback(path):
        seen.append(sorted(p.name for p in path.parent.iterdir()))
        # the backup is a dot-file: the run's masks discovery skips it (no twin, no stray mask)
        assert images_to_segment(run.inputs, "Cells", run.masks) == run.inputs.fovs()[1:]

    new = labels_like(run, fov)
    new[0, 0] = 7
    write_mask(run.masks, "Cells", fov, new, run.inputs, overwrite=True, on_published=callback)
    (names,) = seen
    assert [n for n in names if n.startswith(f".{target.name}.previous.")]  # kept meanwhile
    assert _files(run) == [target.relative_to(run.masks).as_posix()]


def test_a_failed_restore_is_loud_names_both_files_and_keeps_the_backup(run, monkeypatch):
    fov = run.inputs.fovs()[0]
    target = write_mask(run.masks, "Cells", fov, labels_like(run, fov), run.inputs)
    before = target.read_bytes()
    real = os.replace

    def replace(source, destination):
        if ".previous." in os.fspath(source):
            raise OSError("disk gone")
        return real(source, destination)

    monkeypatch.setattr(segmentation_handoff.os, "replace", replace)
    new = labels_like(run, fov)
    new[0, 0] = 7
    with pytest.raises(SegmentationError, match="could not be restored") as caught:
        write_mask(
            run.masks,
            "Cells",
            fov,
            new,
            run.inputs,
            overwrite=True,
            on_published=_fail(RuntimeError("record failed")),
        )
    (backup,) = target.parent.glob(f".{target.name}.previous.*")
    assert str(backup) in str(caught.value) and str(target) in str(caught.value)
    assert backup.read_bytes() == before  # the earlier mask is never lost
    assert isinstance(caught.value.__cause__.__context__, RuntimeError)  # caller's error kept


def test_a_failed_removal_of_a_new_mask_is_loud(run, monkeypatch):
    fov = run.inputs.fovs()[0]
    target = mask_path(run.masks, "Cells", fov)
    real = segmentation_handoff.Path.unlink

    def unlink(self, missing_ok=False):
        if self == target:
            raise OSError("read-only")
        return real(self, missing_ok=missing_ok)

    monkeypatch.setattr(segmentation_handoff.Path, "unlink", unlink)
    with pytest.raises(SegmentationError, match="could not be removed") as caught:
        write_mask(
            run.masks,
            "Cells",
            fov,
            labels_like(run, fov),
            run.inputs,
            on_published=_fail(RuntimeError("record failed")),
        )
    assert str(target) in str(caught.value)
