"""Issue #165: no mask relationship report from masks that change while they are scanned.

The mask step reads every image's masks and writes ``mask_relationships.json``. Before
#165 nothing checked the masks around the scan: a segmentation still writing a mask could
be analysed mid-write, and planes of one mask could come from two versions of the file.
Now each mask is fingerprinted with its header (:func:`prepare_mask_step`), before its
image's planes are read and after; with ``verify_content`` also its content SHA-256. A
change raises ``mask_changed_during_scan``: no report is written, an earlier one is left as
it was, and nothing half-written is left in the output folder.
"""

import itertools
import os

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import mask_scan, prefill_mapping, run_experiment
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.mask_binding import MaskChangedError
from morphobricks_preprocessing.mask_scan import MASK_REPORT_FILENAME
from morphobricks_preprocessing.run import describe_masks, prepare_mask_step
from morphobricks_preprocessing.testing import make_clean_run

MASK = "Cells/ctrl/rep1/b.tif"
LABELS = itertools.count(3)


@pytest.fixture
def clean(tmp_path):
    return make_clean_run(tmp_path, axes="YX", shape=(8, 8), mask_types=("Cells", "Nuclei"))


def rewrite_mask(path, *, keep_metadata=False):
    """Write other labels into a 2-D mask (same shape and type), a new time unless kept."""
    before = path.stat()
    labels = np.zeros(tifffile.imread(path).shape, np.uint16)
    labels[2:5, 2:5] = next(LABELS)  # other content at each rewrite
    tifffile.imwrite(path, labels, photometric="minisblack", metadata={"axes": "YX"})
    shift = 0 if keep_metadata else 10**9
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + shift))
    if keep_metadata:
        assert path.stat().st_size == before.st_size


def rewrite_while_read(monkeypatch, path, mask_path=MASK, **how):
    """Rewrite ``path`` right after the scan reads ``mask_path`` (a segmentation writing it)."""
    read = mask_scan.read_mask

    def read_then_rewrite(mask, *args, **kwargs):
        pixels = read(mask, *args, **kwargs)
        if mask.path == mask_path:
            rewrite_mask(path, **how)
        return pixels

    monkeypatch.setattr(mask_scan, "read_mask", read_then_rewrite)


def leftovers(out):
    return sorted(p.name for p in out.iterdir() if p.name.startswith(f".{MASK_REPORT_FILENAME}"))


def test_a_mask_changed_while_it_is_scanned_writes_no_report(clean, monkeypatch):
    out = clean.output
    step = prepare_mask_step(None, out)
    assert step.ready
    rewrite_while_read(monkeypatch, clean.masks / MASK)
    with pytest.raises(MaskChangedError) as raised:
        describe_masks(step)
    assert isinstance(raised.value, ValueError)  # as every mask the scan cannot use
    assert raised.value.paths == [MASK]
    issue = raised.value.issue
    assert issue.code == "mask_changed_during_scan" and issue.is_error
    assert issue.paths == (MASK,) and "No report was written" in issue.message
    assert not (out / MASK_REPORT_FILENAME).exists() and leftovers(out) == []
    assert not (out / ".morphobricks-preprocessing.lock").exists()


def test_an_earlier_report_is_left_as_it_was(clean, monkeypatch):
    out = clean.output
    first = describe_masks(prepare_mask_step(None, out))
    written = first.path.read_bytes()
    step = prepare_mask_step(None, out)
    rewrite_while_read(monkeypatch, clean.masks / MASK)
    with pytest.raises(MaskChangedError):
        describe_masks(step)
    assert (out / MASK_REPORT_FILENAME).read_bytes() == written and leftovers(out) == []


def test_a_mask_changed_since_its_header_was_read_is_not_scanned(clean):
    # The header (planes, axes) describes the mask only as it was when the step paired it.
    step = prepare_mask_step(None, clean.output)
    rewrite_mask(clean.masks / MASK)
    with pytest.raises(MaskChangedError) as raised:
        describe_masks(step)
    assert raised.value.paths == [MASK]
    assert not (clean.output / MASK_REPORT_FILENAME).exists()


def test_a_mask_gone_before_its_scan_is_refused(clean):
    step = prepare_mask_step(None, clean.output)
    (clean.masks / MASK).unlink()
    with pytest.raises(MaskChangedError) as raised:
        describe_masks(step)
    assert raised.value.paths == [MASK]


def test_content_is_compared_only_with_verify_content(clean, monkeypatch):
    # A rewrite keeping size and time is the metadata identity's stated blind spot.
    out = clean.output
    rewrite_while_read(monkeypatch, clean.masks / MASK, keep_metadata=True)
    assert describe_masks(prepare_mask_step(None, out)).path.exists()
    (out / MASK_REPORT_FILENAME).unlink()
    step = prepare_mask_step(None, out, verify_content=True)
    assert step.verify_content
    with pytest.raises(MaskChangedError) as raised:
        describe_masks(step)
    assert raised.value.compared == "fingerprint or content"
    assert "content (SHA-256)" in raised.value.issue.message
    assert not (out / MASK_REPORT_FILENAME).exists()


def test_the_step_verifies_content_when_the_run_did(tmp_path):
    from morphobricks_preprocessing import RunMode

    clean = make_clean_run(tmp_path, axes="YX", shape=(8, 8), mask_types=("Cells",))
    assert not prepare_mask_step(None, clean.output).verify_content
    strict = run_experiment(
        clean.experiment,
        output_folder=clean.output,
        masks_folder=clean.masks,
        mode=RunMode("restart", verify_content=True),
    )
    assert strict.written
    assert prepare_mask_step(None, clean.output).verify_content
    assert not prepare_mask_step(None, clean.output, verify_content=False).verify_content


def test_unchanged_masks_give_the_same_report_with_and_without_content(clean):
    out = clean.output
    plain = describe_masks(prepare_mask_step(None, out)).path.read_bytes()
    strict = describe_masks(prepare_mask_step(None, out, verify_content=True)).path.read_bytes()
    assert strict == plain


def test_the_masks_command_names_the_changed_mask(clean, monkeypatch, capsys):
    rewrite_while_read(monkeypatch, clean.masks / MASK)
    assert main(["masks", "--output", str(clean.output)]) == 1
    printed = capsys.readouterr().out
    assert "changed or went missing before or while the mask step read them" in printed
    assert MASK in printed
    assert "Report written" not in printed
    assert not (clean.output / MASK_REPORT_FILENAME).exists()


def test_a_label_image_changed_while_it_is_scanned_writes_no_report(tmp_path, monkeypatch):
    pytest.importorskip("zarr")
    from zarr_tree import write_labels, write_ngff

    image = write_ngff(tmp_path / "exp/ctrl/a.ome.zarr", np.zeros((2, 6, 8), np.uint8), "tyx")
    cells = np.zeros((2, 6, 8), np.uint16)
    cells[:, 1:3, 1:3] = 1
    write_labels(image, {"cells": cells}, "tyx")
    out = tmp_path / "out"
    assert run_experiment(tmp_path / "exp", prefill_mapping("TYX"), output_folder=out).written
    step = prepare_mask_step(None, out)
    assert step.ready
    read, path = mask_scan.read_mask, "ctrl/a.ome.zarr/labels/cells"

    def chunk_rewritten(mask, *args, **kwargs):
        pixels = read(mask, *args, **kwargs)
        label = image / "labels" / "cells" / "0"
        chunk = sorted(p for p in label.rglob("*") if p.is_file() and p.name[0] != ".")[0]
        stat = chunk.stat()
        chunk.write_bytes(chunk.read_bytes())
        os.utime(chunk, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**6))
        return pixels

    monkeypatch.setattr(mask_scan, "read_mask", chunk_rewritten)
    with pytest.raises(MaskChangedError) as raised:
        describe_masks(step)
    assert raised.value.paths == [path]
    assert not (out / MASK_REPORT_FILENAME).exists()


def test_the_masks_command_takes_verify_content(clean, monkeypatch, capsys):
    rewrite_while_read(monkeypatch, clean.masks / MASK, keep_metadata=True)
    out = str(clean.output)
    assert main(["masks", "--output", out, "--no-verify-content"]) == 0
    assert main(["masks", "--output", out, "--verify-content"]) == 1
    assert "content (SHA-256)" in capsys.readouterr().out
