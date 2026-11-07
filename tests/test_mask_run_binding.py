"""Issue #160: every mask is bound to its fingerprint for the whole run, like the images.

A run checks the masks from their headers and records each mask's fingerprint in the
manifest, but before #160 it took that fingerprint only when it wrote the manifest: a mask
rewritten while the run checked headers or read images was recorded as it was then, under
headers checked on another version. Now each mask is fingerprinted before and after its
header is read (``mask_changed_during_check`` when they differ), that fingerprint is the
one recorded, and every mask must still have it right before the commit
(``mask_changed_during_run``); a ``verify_content`` run also compares each mask's content
SHA-256 taken after the header checks with one taken right before the commit.
"""

import json
import os

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import RunMode, prefill_mapping, run_experiment
from morphobricks_preprocessing import run as run_module
from morphobricks_preprocessing.fingerprint import Fingerprint
from morphobricks_preprocessing.masks import checks as mask_checks
from morphobricks_preprocessing.run.generation import PUBLISH_FOLDER
from morphobricks_preprocessing.testing import make_clean_run

STRICT = RunMode("restart", verify_content=True)
YX = prefill_mapping("YX")
MASK = "Cells/ctrl/rep1/a.tif"
OUTPUTS = ("run_manifest.json", "plane_histograms.csv")


@pytest.fixture
def clean(tmp_path):
    return make_clean_run(tmp_path, axes="YX", shape=(8, 8), mask_types=("Cells", "Nuclei"))


def outputs(out):
    return {name: (out / name).read_bytes() for name in OUTPUTS}


def rewrite_mask(path, *, keep_metadata=False):
    """Write other labels into a 2-D mask (same shape and type), a new time unless kept."""
    before = path.stat()
    labels = np.zeros(tifffile.imread(path).shape, np.uint16)
    labels[2:5, 2:5] = 3
    tifffile.imwrite(path, labels, photometric="minisblack", metadata={"axes": "YX"})
    if keep_metadata:
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert path.stat().st_size == before.st_size
    else:
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 10**9))


def mask_entry(out, path=MASK):
    data = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    (entry,) = [m for m in data["masks"]["files"] if m["path"] == path]
    return entry


def test_a_mask_changed_while_the_images_are_read_is_refused(clean, monkeypatch):
    out, mask = clean.output, clean.masks / MASK
    before = outputs(out)
    (clean.experiment / "ctrl/rep1/b.ome.tif").touch()  # one image to read again
    os.utime(clean.experiment / "ctrl/rep1/b.ome.tif", ns=(1, 10**9))
    read = run_module.resume._read_file

    def segmentation_still_writing(path, rel, *args):
        rows = read(path, rel, *args)
        rewrite_mask(mask)
        return rows

    monkeypatch.setattr(run_module.resume, "_read_file", segmentation_still_writing)
    result = run_experiment(clean.experiment, output_folder=out, masks_folder=clean.masks)
    assert not result.written and result.report_written
    (issue,) = [i for i in result.issues if i.is_error]
    assert issue.code == "mask_changed_during_run" and issue.paths == (MASK,)
    assert "size or modification time" in issue.message and "Nothing was published" in (
        issue.message
    )
    assert outputs(out) == before and not (out / PUBLISH_FOLDER).exists()
    monkeypatch.undo()
    again = run_experiment(clean.experiment, output_folder=out, masks_folder=clean.masks)
    assert again.written
    assert Fingerprint.recorded(mask_entry(out)) == Fingerprint.of(mask)


def test_a_mask_changed_after_its_header_and_before_the_commit_is_refused(clean, monkeypatch):
    # No image is read: the masks are checked again right before the commit.
    out, mask = clean.output, clean.masks / MASK
    changed_since = run_module.pipeline.changed_since

    def meanwhile(entries, root):
        rewrite_mask(mask)
        return changed_since(entries, root)

    monkeypatch.setattr(run_module.pipeline, "changed_since", meanwhile)
    result = run_experiment(clean.experiment, output_folder=out, masks_folder=clean.masks)
    assert not result.written
    assert [(i.code, i.paths) for i in result.issues if i.is_error] == [
        ("mask_changed_during_run", (MASK,))
    ]


def test_a_mask_changed_while_its_header_is_read_is_refused(clean, monkeypatch):
    check = mask_checks._check_header

    def rewritten_during(mask, *args):
        found = check(mask, *args)
        if mask.path == MASK:
            rewrite_mask(clean.masks / MASK)
        return found

    monkeypatch.setattr(mask_checks, "_check_header", rewritten_during)
    result = run_experiment(clean.experiment, output_folder=clean.output, masks_folder=clean.masks)
    assert not result.written
    (issue,) = [i for i in result.issues if i.is_error]
    assert issue.code == "mask_changed_during_check" and issue.paths == (MASK,)


def test_an_unchanged_rerun_keeps_every_output_byte_for_byte(clean):
    out = clean.output
    before = outputs(out)
    entry = mask_entry(out)
    assert Fingerprint.recorded(entry) == Fingerprint.of(clean.masks / MASK)
    result = run_experiment(clean.experiment, output_folder=out, masks_folder=clean.masks)
    assert result.written and result.read == ()
    assert outputs(out) == before and mask_entry(out) == entry


def test_a_strict_run_refuses_a_mask_rewritten_with_its_size_and_time_kept(clean, monkeypatch):
    out, mask = clean.output, clean.masks / MASK
    assert run_experiment(
        clean.experiment, output_folder=out, masks_folder=clean.masks, mode=STRICT
    ).written
    before = outputs(out)
    hashed = run_module.pipeline.content_changed

    def rewritten_before_the_commit(entries, root):
        rewrite_mask(mask, keep_metadata=True)
        return hashed(entries, root)

    monkeypatch.setattr(run_module.pipeline, "content_changed", rewritten_before_the_commit)
    result = run_experiment(clean.experiment, output_folder=out)
    assert not result.written
    (issue,) = [i for i in result.issues if i.is_error]
    assert issue.code == "mask_changed_during_run" and issue.paths == (MASK,)
    assert "content (SHA-256)" in issue.message
    assert outputs(out) == before and not (out / PUBLISH_FOLDER).exists()


def test_a_run_that_opted_out_does_not_see_a_rewrite_with_size_and_time_kept(clean, monkeypatch):
    # The stated blind spot of the metadata identity, for a run that opted out of the content
    # hashing that is on by default (#159).
    mask = clean.masks / MASK
    changed_since = run_module.pipeline.changed_since

    def meanwhile(entries, root):
        rewrite_mask(mask, keep_metadata=True)
        return changed_since(entries, root)

    monkeypatch.setattr(run_module.pipeline, "changed_since", meanwhile)
    result = run_experiment(
        clean.experiment,
        output_folder=clean.output,
        masks_folder=clean.masks,
        mode=RunMode("restart", verify_content=False),
    )
    assert result.written


def test_a_label_image_changed_during_the_run_is_refused(tmp_path, monkeypatch):
    pytest.importorskip("zarr")
    from zarr_tree import write_labels, write_ngff

    image = write_ngff(tmp_path / "exp/ctrl/a.ome.zarr", np.zeros((6, 8), np.uint8), "yx")
    cells = np.zeros((6, 8), np.uint16)
    cells[1:3, 1:3] = 1
    write_labels(image, {"cells": cells}, "yx")
    changed_since = run_module.pipeline.changed_since

    def chunk_rewritten(entries, root):
        label = image / "labels" / "cells" / "0"
        chunk = sorted(p for p in label.rglob("*") if p.is_file() and p.name[0] != ".")[0]
        stat = chunk.stat()
        chunk.write_bytes(chunk.read_bytes())
        os.utime(chunk, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**6))
        return changed_since(entries, root)

    monkeypatch.setattr(run_module.pipeline, "changed_since", chunk_rewritten)
    result = run_experiment(tmp_path / "exp", YX, output_folder=tmp_path / "out")
    assert not result.written
    assert [(i.code, i.paths) for i in result.issues if i.is_error] == [
        ("mask_changed_during_run", ("ctrl/a.ome.zarr/labels/cells",))
    ]
