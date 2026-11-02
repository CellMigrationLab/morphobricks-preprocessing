"""An OME-Zarr image's fingerprint covers every file of it (issue #86, PP-12).

The fingerprint was the total size and the newest time: a chunk rewritten with the same
size whose new time stays below another file's newest time (or a chunk removed and another
of the same size added) left it unchanged, so a restart reused rows of pixels that no
longer exist. The inventory (each file's path, size and time) closes that gap.
"""

import json
import os
import shutil

import numpy as np
import pytest

from morphobricks_preprocessing import (
    MANIFEST_FILENAME,
    RunInputs,
    check_sources,
    prefill_mapping,
    read_manifest,
    run_experiment,
)
from morphobricks_preprocessing.fingerprint import Fingerprint
from morphobricks_preprocessing.inventory import source_fingerprint, source_signature
from morphobricks_preprocessing.run_inputs import RunInputsError
from zarr_tree import write_ngff

pytest.importorskip("zarr")

IMAGE = "ctrl/a.ome.zarr"


def two_chunks(path):
    """Write a 2-plane image whose planes are one chunk each, of the same size."""
    data = np.stack([np.full((8, 8), 5, np.uint8), np.full((8, 8), 9, np.uint8)])
    image = write_ngff(path, data, "zyx", chunks=(1, 8, 8))
    first, second = chunk_files(image)
    assert first.stat().st_size == second.stat().st_size
    # Fixed times: the chunks are older than the metadata, so neither is the newest file.
    for file in (p for p in image.rglob("*") if p.is_file()):
        os.utime(file, ns=(3_000, 3_000))
    os.utime(first, ns=(1_000, 1_000))
    os.utime(second, ns=(2_000, 2_000))
    os.utime(image, ns=(3_000, 3_000))
    return image


def chunk_files(image):
    files = sorted(p for p in (image / "0").iterdir() if not p.name.startswith("."))
    assert len(files) == 2, files
    return files


def swap_chunks(image, first_time=1_500):
    """Exchange the two chunks' bytes in place, keeping the size and the newest time."""
    first, second = chunk_files(image)
    a, b = first.read_bytes(), second.read_bytes()
    first.write_bytes(b)
    second.write_bytes(a)
    os.utime(first, ns=(first_time, first_time))
    os.utime(second, ns=(2_000, 2_000))
    os.utime(first.parent, ns=(3_000, 3_000))
    os.utime(image, ns=(3_000, 3_000))


def test_a_rewritten_chunk_below_the_newest_time_changes_the_fingerprint(tmp_path):
    # The issue's counterexample: same total size, same newest time.
    image = two_chunks(tmp_path / "a.ome.zarr")
    before = Fingerprint.of(image)
    old_size, old_time = source_fingerprint(image)
    swap_chunks(image)
    assert source_fingerprint(image) == (old_size, old_time)  # the old rule saw nothing
    assert Fingerprint.of(image) != before


def test_a_chunk_removed_and_another_added_changes_the_fingerprint(tmp_path):
    image = two_chunks(tmp_path / "a.ome.zarr")
    before = Fingerprint.of(image)
    first, _ = chunk_files(image)
    moved = first.with_name("9.0.0")
    shutil.copy2(first, moved)
    first.unlink()
    os.utime(first.parent, ns=(3_000, 3_000))
    assert source_fingerprint(image) == (before.size, before.mtime_ns)
    assert Fingerprint.of(image) != before


def test_an_unchanged_store_keeps_its_fingerprint_and_labels_are_left_out(tmp_path):
    image = two_chunks(tmp_path / "a.ome.zarr")
    before = Fingerprint.of(image)
    assert before.inventory is not None and len(before.inventory) == 64
    assert Fingerprint.of(image) == before
    labels = image / "labels" / "cells"
    labels.mkdir(parents=True)
    (labels / "chunk").write_bytes(b"1234")
    os.utime(image, ns=(3_000, 3_000))
    assert Fingerprint.of(image) == before


def test_a_file_has_no_inventory(tmp_path):
    path = tmp_path / "a.tif"
    path.write_bytes(b"1234")
    assert source_signature(path)[2] is None
    assert Fingerprint.of(path).inventory is None


@pytest.fixture
def zarr_run(tmp_path):
    root = tmp_path / "exp"
    image = two_chunks(root / IMAGE)
    output = tmp_path / "out"
    result = run_experiment(root, prefill_mapping("ZYX"), output_folder=output)
    assert result.written, result.issues
    return root, image, output


def maxima(output):
    inputs = RunInputs.open(output)
    return [r.histograms[0].pixel_max for r in inputs.histograms()]


def test_a_restart_recomputes_an_image_whose_chunk_changed(zarr_run):
    root, image, output = zarr_run
    entry = read_manifest(output / MANIFEST_FILENAME).files[0]
    assert entry.inventory_sha256 == Fingerprint.of(image).inventory
    assert maxima(output) == [5, 9]
    swap_chunks(image)
    result = run_experiment(root, output_folder=output)
    assert result.written, result.issues
    assert result.changed == (IMAGE,) and result.read == (IMAGE,)
    assert maxima(output) == [9, 5]  # the rows describe the pixels as they are now


def test_bricks_refuse_an_image_whose_chunk_changed(zarr_run):
    root, image, output = zarr_run
    manifest = read_manifest(output / MANIFEST_FILENAME)
    swap_chunks(image)
    found = {issue.code: issue.paths for issue in check_sources(manifest, root, [IMAGE])}
    assert found == {"images_changed": (IMAGE,)}
    with pytest.raises(RunInputsError, match="changed"):
        RunInputs.open(output).read_plane(IMAGE, 0, 0)


def test_an_image_recorded_without_inventory_is_read_again(zarr_run):
    # A manifest written before the inventory was recorded (v0.4.0rc3): its OME-Zarr images
    # are not known to be unchanged, so their rows are recomputed, never reused.
    root, _, output = zarr_run
    path = output / MANIFEST_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["files"][0]["inventory_sha256"]
    path.write_text(json.dumps(data), encoding="utf-8")
    result = run_experiment(root, output_folder=output)
    assert result.written, result.issues
    assert result.changed == (IMAGE,)
