"""A ``SegmentationJob`` made directly, without the notebook, keeps the folder and run it named.

External review ar (24 November 2026), on the direct API (the notebook was fixed in review aq):

- B1: a job kept a relative ``masks_root`` as given, so ``run_batch`` after ``os.chdir`` wrote
  the masks and the record under the new working directory. The job now fixes the folder,
  resolved, when it is made.
- B2: a job made without ``run_manifest_sha256`` hashed ``run_manifest.json`` as it was on disk
  when the job was made, so a job made after the run was published again (manifest B) pinned B
  for an inputs snapshot of A. ``RunInputs.open`` now keeps the SHA-256 of the manifest bytes it
  parsed (``RunInputs.manifest_sha256``); the job takes it from there, refuses a SHA-256 given
  that is not the snapshot's, and never hashes the file itself.

A stand-in Cellpose (``cellpose_stub``) on a run whose images all have DNA and TUB.
"""

import dataclasses
import hashlib
from pathlib import Path

import pytest

from morphobricks_preprocessing import segmentation_cellpose as cp
from morphobricks_preprocessing.manifest import MANIFEST_FILENAME
from morphobricks_preprocessing.run_inputs import RunInputs
from morphobricks_preprocessing.segmentation_handoff import resolve_segmentation
from morphobricks_preprocessing.segmentation_record import (
    SegmentationError,
    read_record,
    record_path,
)
from test_notebook_cellpose_cwd import setup  # the fixture: run, stand-in Cellpose, A and B
from test_notebook_cellpose_republished import AGAIN, republish

__all__ = ["setup"]  # the fixture, imported for the tests below

JOBS = {
    "Cells": (("TUB", "DNA"), ("cell", "nuclear"), "cells"),
    "Nuclei": (("DNA",), ("nuclear",), "nuclei"),
}


def make_job(inputs, mask_type="Cells", masks_root=Path("masks"), **extra):
    ids, roles, output = JOBS[mask_type]
    return cp.SegmentationJob(
        inputs=inputs,
        plan=resolve_segmentation(inputs, ids),
        loaded=cp.load_model("cpsam_v2", "cpu"),
        settings=cp.Settings(),
        mask_type=mask_type,
        masks_root=masks_root,
        channel_roles=roles,
        output=output,
        **extra,
    )


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files(folder):
    return sorted(p for p in folder.rglob("*") if p.is_file()) if folder.exists() else []


# B1: the masks folder is the one named when the job was made ------------------------------


@pytest.mark.parametrize("types", [("Cells",), ("Cells", "Nuclei")], ids=["one", "both"])
def test_a_relative_masks_folder_is_fixed_when_the_job_is_made(setup, monkeypatch, types):
    jobs = [make_job(setup.run.inputs, t) for t in types]  # made in A: "masks" is A/masks
    monkeypatch.chdir(setup.b)
    results = [cp.run_batch(job, batch_id=f"b-{job.mask_type}") for job in jobs]
    assert list(setup.b.rglob("*")) == []  # nothing at all where "masks" is now
    pinned = (setup.a / "masks").resolve()
    assert [job.masks_root for job in jobs] == [pinned] * len(types)
    count = len(setup.run.inputs.fovs())
    for job, result in zip(jobs, results, strict=True):
        assert not result.stopped
        assert [i.status for i in result.batch.images] == ["written"] * count
        assert read_record(pinned, job.mask_type).batches == (result.batch,)
        assert result.record == record_path(pinned, job.mask_type)
    assert len(files(pinned)) == len(types) * (count + 1)  # the masks and each type's record


def test_a_rolled_back_write_stays_in_the_folder_fixed_when_the_job_was_made(setup, monkeypatch):
    job = make_job(setup.run.inputs)
    monkeypatch.chdir(setup.b)

    def refuse(*args, **kwargs):
        raise OSError("the record could not be written")

    monkeypatch.setattr(cp, "write_record", refuse)
    result = cp.run_batch(job, batch_id="b1")
    first, *rest = result.batch.images
    assert first.status == "failed" and "rolled back" in first.message
    assert rest and all("not started" in i.message for i in rest)
    assert "the record could not be written" in result.stopped
    assert list(setup.b.rglob("*")) == []  # neither the mask nor its folder nor a lock
    assert [p for p in files(setup.a / "masks") if p.suffix == ".tif"] == []  # rolled back
    assert len(setup.stub.calls) == 1


# B2: the run manifest is the one the inputs were read from --------------------------------


def test_the_snapshot_keeps_the_sha256_of_the_manifest_it_read(setup):
    manifest = setup.run.out / MANIFEST_FILENAME
    assert setup.run.inputs.manifest_sha256 == sha(manifest)
    republish(setup.run.out)
    assert setup.run.inputs.manifest_sha256 != sha(manifest)  # still A: a snapshot
    assert RunInputs.open(setup.run.out).manifest_sha256 == sha(manifest)


def test_a_job_made_after_the_run_was_published_again_is_refused(setup):
    inputs, pinned = setup.run.inputs, sha(setup.run.out / MANIFEST_FILENAME)  # A
    republish(setup.run.out)  # B
    for types in (("Cells",), ("Cells", "Nuclei")):
        jobs = [make_job(inputs, t, setup.a / "masks") for t in types]  # no SHA-256 given
        assert [job.run_manifest_sha256 for job in jobs] == [pinned] * len(types)
        for job in jobs:
            with pytest.raises(SegmentationError, match=AGAIN):
                cp.run_batch(job)
    assert files(setup.a / "masks") == [] and setup.stub.calls == []


def test_a_job_refuses_a_sha256_that_is_not_the_snapshots(setup):
    inputs = setup.run.inputs
    published = republish(setup.run.out)  # B: the file now, not the snapshot (A)
    for given in (published, "0" * 64):
        with pytest.raises(SegmentationError, match="not the SHA-256 of the run manifest"):
            make_job(inputs, masks_root=setup.a / "masks", run_manifest_sha256=given)
    assert files(setup.a / "masks") == [] and setup.stub.calls == []


def test_a_job_refuses_inputs_without_a_manifest_sha256(setup):
    inputs = dataclasses.replace(setup.run.inputs, manifest_sha256=None)  # built by hand
    for given in (None, sha(setup.run.out / MANIFEST_FILENAME)):
        with pytest.raises(SegmentationError, match="SHA-256 of the manifest it was read"):
            make_job(inputs, masks_root=setup.a / "masks", run_manifest_sha256=given)


@pytest.mark.parametrize("given", [False, True], ids=["default", "explicit"])
def test_a_job_on_a_stable_run_records_the_snapshots_sha256(setup, given):
    pinned = sha(setup.run.out / MANIFEST_FILENAME)
    extra = {"run_manifest_sha256": pinned} if given else {}
    job = make_job(setup.run.inputs, masks_root=setup.a / "masks", **extra)
    result = cp.run_batch(job, batch_id="b1")
    assert not result.stopped and result.batch.run_manifest_sha256 == pinned
    assert read_record(setup.a / "masks", "Cells").batches == (result.batch,)


def test_the_right_sha256_given_after_a_republish_is_still_refused_at_the_batch(setup):
    pinned = sha(setup.run.out / MANIFEST_FILENAME)  # A
    republish(setup.run.out)
    job = make_job(setup.run.inputs, masks_root=setup.a / "masks", run_manifest_sha256=pinned)
    assert job.run_manifest_sha256 == pinned  # accepted: it is the snapshot's
    with pytest.raises(SegmentationError, match=AGAIN):
        cp.run_batch(job)
    assert files(setup.a / "masks") == [] and setup.stub.calls == []
