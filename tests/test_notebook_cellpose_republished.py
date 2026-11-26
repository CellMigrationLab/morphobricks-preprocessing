"""A record never names a run manifest other than the one the run was opened from.

External review aq (24 November 2026), B2: ``RunInputs.open`` reads a snapshot of manifest A,
but each batch's record hashed ``run_manifest.json`` as it was on disk when the record was
written. A run published again at the same path (manifest B, same description and images) was
then recorded as B for work resolved against A. The SHA-256 is now pinned when the run is
opened (``open_pinned``); Preview, every batch and every record write check the manifest
against it and refuse a republished run (``REPUBLISHED``: press Open the run again), and the
record names the pinned SHA-256. A stand-in Cellpose (``cellpose_stub``) on a run whose images
all have DNA and TUB.
"""

import hashlib
import json
import os

import pytest

from cellpose_stub import install, two_channel_run
from morphobricks_preprocessing import segmentation_cellpose as cp
from morphobricks_preprocessing import segmentation_cellpose_run as run_module
from morphobricks_preprocessing.manifest import MANIFEST_FILENAME
from morphobricks_preprocessing.run_inputs import RunInputs, open_checked
from morphobricks_preprocessing.segmentation_record import read_record, record_path
from test_notebook_cellpose import BATCH, CHANNELS, MODEL, OPEN, PREVIEW, run_cell, source

TYPES = {"cells": ("Cells",), "both": ("Cells", "Nuclei")}
AGAIN = "The run was published again since it was opened"
"""The start of ``REPUBLISHED``; matched as text so that each case fails on its own before."""


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def republish(out, check=True):
    """Replace the manifest at the same path with a valid B: same content, other bytes."""
    path = out / MANIFEST_FILENAME
    before = sha(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    staged = path.with_name("manifest.staged")
    staged.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(staged, path)
    assert sha(path) != before
    if check:
        RunInputs.open(out)  # B is a valid manifest of the same run: it opens
    return sha(path)


def mask_files(masks):
    return sorted(p for p in masks.rglob("*") if p.is_file()) if masks.exists() else []


@pytest.fixture
def opened(monkeypatch, tmp_path, request):
    """Cells 3 to 5 for ``(output, by)``; 6 and 7 shown, not pressed."""
    output, by = request.param
    stub = install(monkeypatch, tmp_path)
    run, ns = two_channel_run(tmp_path / "run"), {}
    run_cell(ns, OPEN, run_folder=str(run.out), masks_folder=str(run.masks))
    run_cell(ns, CHANNELS, output=output)
    picker = ns["workflow"].picker
    if by == "number":
        picker.by.value = "number"
        picker.cell_number.value, picker.nuclear_number.value = "2", "1"
    else:
        picker.cell.value, picker.nuclear.value = "TUB", "DNA"
    run_cell(ns, MODEL, device="cpu")
    for cell in (PREVIEW, BATCH):
        exec(compile(source(cell), f"<cell {cell + 1}>", "exec"), ns)
    return type("O", (), {"ns": ns, "stub": stub, "run": run, "output": output})()


CASES = [(o, b) for o in sorted(TYPES) for b in ("tag", "number")]


@pytest.mark.parametrize("opened", CASES, indirect=True, ids=lambda c: "-".join(c))
def test_preview_and_segment_refuse_a_run_published_again(opened):
    workflow, run = opened.ns["workflow"], opened.run
    pinned = sha(run.out / MANIFEST_FILENAME)
    published = republish(run.out)
    refusals = []
    for step in ("preview", "segment"):
        form = workflow.forms[step]
        form.button.click()
        refusals.append(form.refusal)
    assert mask_files(run.masks) == []  # no mask and no record anywhere
    assert opened.stub.calls == [] and workflow.results == {} and workflow.previewed == []
    assert refusals == [run_module.REPUBLISHED] * 2 and refusals[0].startswith(AGAIN)
    assert workflow.run_manifest_sha256 == pinned
    run_cell(opened.ns, OPEN, run_folder=str(run.out), masks_folder=str(run.masks))
    assert opened.ns["workflow"].run_manifest_sha256 == published  # opened again: B now


def test_a_job_records_the_pinned_manifest_never_the_file_now(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    run, ns = two_channel_run(tmp_path / "run"), {}
    run_cell(ns, OPEN, run_folder=str(run.out), masks_folder=str(run.masks))
    run_cell(ns, CHANNELS, output="both")
    workflow = ns["workflow"]
    workflow.picker.cell.value, workflow.picker.nuclear.value = "TUB", "DNA"
    run_cell(ns, MODEL, device="cpu")
    pinned = sha(run.out / MANIFEST_FILENAME)
    published = []
    workflow.on_masks_written = lambda _: published.append(republish(run.out))
    with pytest.raises(Exception) as stopped:  # noqa: PT011, B017 - CellStop, asserted below
        workflow.run()  # the cells batch runs, then B is published
    assert not record_path(run.masks, "Nuclei").exists()
    assert not (run.masks / "Nuclei").exists()
    cells = read_record(run.masks, "Cells").batches
    assert [b.run_manifest_sha256 for b in cells] == [pinned] and len(published) == 1
    assert str(stopped.value).startswith(AGAIN)


def test_a_run_published_between_two_images_stops_the_batch(monkeypatch, tmp_path):
    stub = install(monkeypatch, tmp_path)
    run, ns = two_channel_run(tmp_path / "run"), {}
    run_cell(ns, OPEN, run_folder=str(run.out), masks_folder=str(run.masks))
    run_cell(ns, CHANNELS, output="cells")
    workflow = ns["workflow"]
    workflow.picker.cell.value, workflow.picker.nuclear.value = "TUB", "DNA"
    run_cell(ns, MODEL, device="cpu")
    pinned = sha(run.out / MANIFEST_FILENAME)
    (target,) = workflow.picker.current_targets()
    job = workflow._job(target, overwrite=False)

    def progress(done, total, image):
        if done == 1:  # after the first image was written and recorded
            republish(run.out)

    result = cp.run_batch(job, progress=progress)
    batches = read_record(run.masks, "Cells").batches
    assert [b.run_manifest_sha256 for b in batches] == [pinned]  # nothing names B
    first_mask = cp.mask_path(run.masks, "Cells", job.inputs.fovs()[0])
    assert mask_files(run.masks) == sorted([first_mask, record_path(run.masks, "Cells")])
    first, second, *rest = result.batch.images
    assert first.status == "written"
    assert second.status == "failed" and AGAIN in second.message  # rolled back
    assert rest and all(i.status == "failed" and "not started" in i.message for i in rest)
    assert AGAIN in result.stopped and len(stub.calls) == 2


def test_a_run_published_while_it_is_opened_is_refused(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    run, ns = two_channel_run(tmp_path / "run"), {}
    real = open_checked

    def open_then_publish(*args, **kwargs):
        inputs = real(*args, **kwargs)
        republish(run.out, check=False)  # after the snapshot was read
        return inputs

    # The notebook opens the run through open_checked (RunInputs.open with its wait, #574).
    monkeypatch.setattr(run_module, "open_checked", open_then_publish)
    exec(compile(source(OPEN), "<cell 3>", "exec"), ns)
    opener = ns["workflow"].forms["open"]
    opener.fields["run_folder"].value = str(run.out)
    opener.fields["masks_folder"].value = str(run.masks)
    opener.button.click()
    assert opener.refusal.startswith(AGAIN) and ns["workflow"].inputs is None
