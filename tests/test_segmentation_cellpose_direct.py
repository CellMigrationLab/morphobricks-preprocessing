"""A ``SegmentationJob`` made directly, without the notebook, keeps the folder and run it named.

External review ar (24 November 2026), on the direct API (the notebook was fixed in review aq):

- B1: a job kept a relative ``masks_root`` as given, so ``run_batch`` after ``os.chdir`` wrote
  the masks and the record under the new working directory. The job now fixes the folder,
  resolved, when it is made.

A stand-in Cellpose (``cellpose_stub``) on a run whose images all have DNA and TUB.
"""

from pathlib import Path

import pytest

from morphobricks_preprocessing import segmentation_cellpose as cp
from morphobricks_preprocessing.segmentation_handoff import resolve_segmentation
from morphobricks_preprocessing.segmentation_record import read_record, record_path
from test_notebook_cellpose_cwd import setup  # the fixture: run, stand-in Cellpose, A and B

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
