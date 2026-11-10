"""Crash-consistent publication (PP-12, owner decision of 2 November 2026; contract §9).

A run's outputs are one generation, made current by one atomic step (the manifest's
replacement): any reader through the manifest sees the old or the new generation, a plain
reader of plane_histograms.csv one whole table, never a mix; a kill at any step is
finished or discarded by the next holder of the lock.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from morphobricks_normalization.csv_io import OUTPUT_FILENAME, read_plane_histograms

from described_tree import run_described
from morphobricks_preprocessing import (
    MANIFEST_FILENAME,
    RunInputs,
    prefill_mapping,
    read_manifest,
)
from morphobricks_preprocessing import run as run_module
from morphobricks_preprocessing.manifest import file_sha256
from morphobricks_preprocessing.run.generation import (
    PUBLICATION_RECORD,
    PUBLISH_FOLDER,
    PublicationIncompleteError,
    finish_publication,
)
from morphobricks_preprocessing.run.lock import output_lock, remove_stale_lock
from run_files import outputs
from tiff_tree import write_data

TYX = prefill_mapping("TYX")
IMAGES = ["ctrl/r1/a.tif", "ctrl/r1/b.tif", "drug/r1/a.tif"]
MASK_REPORT = "mask_relationships.json"


def stack(seed):
    return np.random.default_rng(seed).integers(0, 255, (2, 8, 8), dtype=np.uint8)


NEXT = {"mode": "full", "bin_count": 128}
"""The run that publishes generation B: every row recomputed with other bins."""


def first_generation(base):
    """Write an experiment and its run (generation A); :data:`NEXT` gives generation B.

    The images stay as they are, so both generations can be opened; B has other bins.
    """
    root, out = base / "exp", base / "out"
    for seed, rel in enumerate(IMAGES):
        write_data(root / rel, stack(seed), "TYX")
    assert run_described(root, TYX, output_folder=out).written
    (out / MASK_REPORT).write_text("{}\n", encoding="utf-8")  # a full run removes it
    return root, out


def same(rows, other):
    """True when two lists of rows hold the same planes with the same histograms."""

    def key(records):
        return [(r.plane_id, [h.counts.tolist() for h in r.histograms]) for r in records]

    return key(rows) == key(other)


def consistent(out):
    """Return the generation's table as RunInputs reads it, checking it is one generation."""
    inputs = RunInputs.open(out)
    manifest = inputs.manifest
    design = inputs.design()
    assert design is not None
    return manifest.csv_sha256, inputs.histograms()


# Failures inside the process -------------------------------------------------------------


@pytest.mark.parametrize("failure", ["csv", "hash", "manifest", "record", "commit"])
def test_a_failure_before_the_commit_keeps_the_earlier_generation(tmp_path, monkeypatch, failure):
    root, out = first_generation(tmp_path)
    before = outputs(out)
    generation = run_module.generation
    original_replace, original_hash = os.replace, generation.file_sha256

    def broken(*args, **kwargs):
        raise OSError("simulated failure")

    def broken_commit(source, destination):
        if Path(destination) == out / MANIFEST_FILENAME:
            raise PermissionError("simulated failure")
        return original_replace(source, destination)

    def broken_hash(path):
        if Path(path).parent.name == PUBLISH_FOLDER:
            raise OSError("simulated failure")
        return original_hash(path)

    with monkeypatch.context() as patch:
        target = {
            "csv": (run_module.pipeline, "write_plane_histograms", broken),
            "hash": (generation, "file_sha256", broken_hash),
            "manifest": (run_module.pipeline, "write_manifest", broken),
            "record": (generation, "write_text_atomic", broken),
            "commit": (os, "replace", broken_commit),
        }[failure]
        patch.setattr(*target)
        with pytest.raises(OSError, match="simulated failure"):
            run_described(root, output_folder=out, **NEXT)
    assert outputs(out) == before
    assert not (out / PUBLISH_FOLDER).exists()
    retried = run_described(root, output_folder=out, **NEXT)
    assert retried.written
    assert read_manifest(retried.manifest_path).csv_sha256 == file_sha256(retried.csv_path)


def test_an_interruption_at_the_commit_keeps_the_earlier_generation(tmp_path, monkeypatch):
    root, out = first_generation(tmp_path)
    before = outputs(out)
    original = os.replace

    def interrupted(source, destination):
        if Path(destination) == out / MANIFEST_FILENAME:
            raise KeyboardInterrupt
        return original(source, destination)

    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run_described(root, output_folder=out, **NEXT)
    assert outputs(out) == before and not (out / PUBLISH_FOLDER).exists()


def test_a_failure_after_the_commit_is_finished_by_the_next_run(tmp_path, monkeypatch):
    # For example a table held open by another program on Windows.
    root, out = first_generation(tmp_path)
    old_table = (out / OUTPUT_FILENAME).read_bytes()
    original = os.replace

    def table_in_use(source, destination):
        if Path(destination) == out / OUTPUT_FILENAME:
            raise PermissionError("simulated table in use")
        return original(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", table_in_use)
        with pytest.raises(PublicationIncompleteError, match="next run finishes"):
            run_described(root, output_folder=out, **NEXT)
    # The new generation is current: RunInputs reads its table from the publication folder;
    # a plain reader still reads the earlier, whole table.
    manifest = read_manifest(out / MANIFEST_FILENAME)
    assert (out / OUTPUT_FILENAME).read_bytes() == old_table
    assert file_sha256(out / PUBLISH_FOLDER / OUTPUT_FILENAME) == manifest.csv_sha256
    sha, rows = consistent(out)
    assert sha == manifest.csv_sha256
    assert same(rows, read_plane_histograms(out / PUBLISH_FOLDER / OUTPUT_FILENAME))
    # Whatever takes the lock next finishes the publication first.
    with output_lock(out):
        pass
    assert not (out / PUBLISH_FOLDER).exists() and not (out / MASK_REPORT).exists()
    assert file_sha256(out / OUTPUT_FILENAME) == manifest.csv_sha256
    again = run_described(root, output_folder=out)
    assert again.written and again.read == ()


def test_an_unfinished_uncommitted_publication_is_discarded(tmp_path):
    root, out = first_generation(tmp_path)
    before = outputs(out)
    leftover = out / PUBLISH_FOLDER
    leftover.mkdir()
    (leftover / OUTPUT_FILENAME).write_text("not a table", encoding="utf-8")
    (leftover / PUBLICATION_RECORD).write_text(
        json.dumps({"publication_version": "1", "files": {MANIFEST_FILENAME: "0" * 64}}),
        encoding="utf-8",
    )
    consistent(out)  # readers ignore it: the manifest in place is the earlier one
    with output_lock(out):
        pass
    assert not leftover.exists() and outputs(out) == before


def staged_copy(out, removes):
    """Leave a publication folder whose staged manifest is byte-identical to the one in place."""
    leftover = out / PUBLISH_FOLDER
    leftover.mkdir()
    shutil.copy2(out / MANIFEST_FILENAME, leftover / MANIFEST_FILENAME)
    record = {
        "publication_version": "1",
        "files": {MANIFEST_FILENAME: file_sha256(out / MANIFEST_FILENAME)},
        "removes": removes,
    }
    (leftover / PUBLICATION_RECORD).write_text(json.dumps(record), encoding="utf-8")
    return leftover


def test_a_publication_whose_manifest_is_still_staged_is_never_committed(tmp_path):
    # External audit of #147: a record and a published manifest byte-identical to the
    # record's made a publication look committed although its manifest never left
    # .publish (an identical rerun refused before its commit); finishing it ran its
    # removals. Committed means the staged manifest was moved: this one is discarded.
    root, out = first_generation(tmp_path)
    before = outputs(out)
    leftover = staged_copy(out, [MASK_REPORT])
    assert finish_publication(out) == "discarded"
    assert not leftover.exists() and (out / MASK_REPORT).exists() and outputs(out) == before


def test_a_committed_publication_is_finished_once_its_manifest_left_the_folder(tmp_path):
    # The same leftover after the commit (the manifest moved, a kill before the removals).
    root, out = first_generation(tmp_path)
    leftover = staged_copy(out, [MASK_REPORT])
    (leftover / MANIFEST_FILENAME).unlink()
    assert finish_publication(out) == "finished"
    assert not leftover.exists() and not (out / MASK_REPORT).exists()


def test_a_discarded_publication_loses_its_record_first(tmp_path, monkeypatch):
    # A removal that stops halfway must never leave a record without its staged manifest.
    root, out = first_generation(tmp_path)
    leftover = staged_copy(out, [MASK_REPORT])
    seen = []

    def stops_halfway(path, *args, **kwargs):
        seen.append(sorted(p.name for p in Path(path).iterdir()))
        (Path(path) / MANIFEST_FILENAME).unlink()
        raise PermissionError("simulated file in use")

    with monkeypatch.context() as patch:
        patch.setattr(shutil, "rmtree", stops_halfway)
        with pytest.raises(PermissionError):
            finish_publication(out)
    assert seen == [[MANIFEST_FILENAME]]  # the record was already gone
    assert finish_publication(out) == "discarded"
    assert not leftover.exists() and (out / MASK_REPORT).exists()


def test_a_cleanup_failure_does_not_fail_a_published_run(tmp_path, monkeypatch, caplog):
    root, out = first_generation(tmp_path)
    original = shutil.rmtree

    def broken(path, *args, **kwargs):
        if Path(path).name == PUBLISH_FOLDER and (Path(path) / MANIFEST_FILENAME).exists() is False:
            raise PermissionError("simulated folder in use")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", broken)
    result = run_described(root, output_folder=out, **NEXT)
    assert result.written
    assert read_manifest(result.manifest_path).csv_sha256 == file_sha256(result.csv_path)
    assert "Cannot remove the publication folder" in caplog.text


def test_the_output_folder_is_flushed_right_after_the_commit(tmp_path, monkeypatch):
    # Review of rc4 (I2): the manifest's rename was not followed by an fsync of the folder,
    # so a power cut after a run returned could bring back the earlier manifest.
    root, out = first_generation(tmp_path)
    generation = run_module.generation
    calls = []
    original_replace, original_flush = os.replace, generation._flush_folder

    def replace(source, destination):
        calls.append(("replace", Path(destination)))
        return original_replace(source, destination)

    def flush_folder(folder):
        calls.append(("flush_folder", Path(folder)))
        return original_flush(folder)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(generation, "_flush_folder", flush_folder)
    assert run_described(root, output_folder=out, **NEXT).written
    commit = calls.index(("replace", out / MANIFEST_FILENAME))
    assert calls[commit - 1] == ("flush_folder", out / PUBLISH_FOLDER)
    assert calls[commit + 1] == ("flush_folder", out)
    moves = [i for i, call in enumerate(calls) if call[0] == "replace" and i > commit]
    assert moves and all(i > commit + 1 for i in moves)


# Killed between any two steps (POSIX) ----------------------------------------------------

CHILD = """
import contextlib, json, os, shutil, signal, sys
from pathlib import Path
from morphobricks_preprocessing import run_experiment
from morphobricks_preprocessing.run import pipeline

root, out, kill_at = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
state = {"calls": 0, "active": False}

def step(function):
    def wrapped(*args, **kwargs):
        if state["active"]:
            state["calls"] += 1
            if state["calls"] == kill_at:
                os.kill(os.getpid(), signal.SIGKILL)
        return function(*args, **kwargs)
    return wrapped

# Every step that changes a file during the publication: writes (each an os.replace of
# its temporary file), the commit, the moves, the removals and the clean-up.
os.replace = step(os.replace)
os.unlink = step(os.unlink)
shutil.rmtree = step(shutil.rmtree)
Path.unlink = step(Path.unlink)
original = pipeline.staged_generation

@contextlib.contextmanager
def watched(*args, **kwargs):
    state["active"] = True
    with original(*args, **kwargs) as staged:
        yield staged

pipeline.staged_generation = watched
result = run_experiment(root, output_folder=out, mode="full", bin_count=128)
print(json.dumps({"calls": state["calls"], "written": result.written}))
"""


def child(base, kill_at):
    script = base.parent / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    return subprocess.run(
        [sys.executable, str(script), str(base / "exp"), str(base / "out"), str(kill_at)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.mark.skipif(os.name != "posix", reason="SIGKILL is POSIX only")
def test_a_kill_between_any_two_publication_steps_leaves_one_generation(tmp_path):
    template = tmp_path / "template"
    first_generation(template)
    old_table = (template / "out" / OUTPUT_FILENAME).read_bytes()
    old_sha = file_sha256(template / "out" / OUTPUT_FILENAME)

    def fresh(name):
        base = tmp_path / name
        shutil.copytree(template, base, symlinks=True)
        return base

    complete = fresh("complete")
    done = child(complete, 0)
    assert done.returncode == 0, done.stderr
    steps = json.loads(done.stdout.splitlines()[-1])["calls"]
    new_table = (complete / "out" / OUTPUT_FILENAME).read_bytes()
    new_sha = file_sha256(complete / "out" / OUTPUT_FILENAME)
    assert new_sha != old_sha and steps >= 8
    seen = set()
    for kill_at in range(1, steps + 1):
        base = fresh(f"kill-{kill_at}")
        out = base / "out"
        killed = child(base, kill_at)
        assert killed.returncode == -signal.SIGKILL, (kill_at, killed.stdout, killed.stderr)
        # A plain reader: one whole table of one generation.
        plain = (out / OUTPUT_FILENAME).read_bytes()
        assert plain in (old_table, new_table), kill_at
        assert read_plane_histograms(out / OUTPUT_FILENAME)
        # A reader through the manifest: exactly one generation, the old or the new one.
        sha, rows = consistent_at(out, base / "exp")
        assert sha in (old_sha, new_sha), kill_at
        expected = template / "out" if sha == old_sha else complete / "out"
        assert same(rows, read_plane_histograms(expected / OUTPUT_FILENAME)), kill_at
        if sha == old_sha:  # not committed: nothing in the folder changed
            assert plain == old_table and (out / MASK_REPORT).exists(), kill_at
        seen.add("old" if sha == old_sha else "new")
        # The next holder of the lock finishes or discards it; the folder is then whole.
        remove_stale_lock(out)
        with output_lock(out):
            pass
        assert not (out / PUBLISH_FOLDER).exists(), kill_at
        manifest = read_manifest(out / MANIFEST_FILENAME)
        assert manifest.csv_sha256 == file_sha256(out / OUTPUT_FILENAME) == sha, kill_at
        assert (out / MASK_REPORT).exists() == (sha == old_sha), kill_at
        again = run_described(base / "exp", output_folder=out)
        assert again.written, (kill_at, again.issues)
    assert seen == {"old", "new"}


def consistent_at(out, root):
    inputs = RunInputs.open(out, experiment_root=root)
    assert inputs.design() is not None
    return inputs.manifest.csv_sha256, inputs.histograms()
