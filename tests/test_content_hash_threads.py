"""A run with several workers hashes that many images at once (D7, ``verify_content``).

The content SHA-256 passes before the reads and before the commit hash each image in a thread
pool of the run's worker count (:func:`.run.workers.hash_threads`): every image's SHA-256, the
images that cannot be hashed and the images found changed are those of one thread, in the
same order, and a run's outputs and refusals do not depend on it.
"""

import threading

import pytest

from described_tree import run_described
from morphobricks_preprocessing import RunMode, source_content
from morphobricks_preprocessing.manifest import FileEntry
from morphobricks_preprocessing.run import workers as workers_module
from morphobricks_preprocessing.run.workers import Workers, hash_threads
from morphobricks_preprocessing.source_content import content_changed, contents, with_content
from strict_tree import IMAGES, TYX, rewrite_keeping_metadata, write_experiment


@pytest.fixture
def experiment(tmp_path):
    return write_experiment(tmp_path)


def test_hash_threads_follow_the_run_workers(monkeypatch):
    monkeypatch.setattr(workers_module, "available_cpus", lambda: 6)
    assert hash_threads(None, 10) == 1
    assert hash_threads(Workers(), 10) == 1
    assert hash_threads(Workers(4), 10) == 4
    assert hash_threads(Workers(4), 3) == 3
    assert hash_threads(Workers("auto", 2**30), 10) == 6  # max_ram does not lower it
    assert hash_threads(Workers("auto"), 2) == 2
    assert hash_threads(Workers(4), 0) == 1


@pytest.mark.parametrize("threads", [2, 4, 8])
def test_contents_are_those_of_one_thread_in_order(experiment, threads, monkeypatch):
    root, _ = experiment
    paths = [root / rel for rel in IMAGES] + [root / "gone.tif"]
    serial = contents(paths)
    assert serial[-1] is None and all(serial[:-1])
    hashed, used = source_content.current_content, set()

    def recording(path):
        used.add(threading.current_thread().name)
        return hashed(path)

    monkeypatch.setattr(source_content, "current_content", recording)
    assert contents(paths, threads) == serial
    assert all(name.startswith("morphobricks-sha") for name in used)


def test_with_content_and_content_changed_do_not_depend_on_threads(experiment):
    root, _ = experiment
    entries = {rel: _entry(rel) for rel in [*IMAGES, "gone.tif"]}
    one, unreadable = with_content(entries, root)
    assert with_content(entries, root, 4) == (one, unreadable) and unreadable == ["gone.tif"]
    recorded = [*one.values(), _entry("unrecorded.tif")]
    rewrite_keeping_metadata(root / IMAGES[1])
    found = content_changed(recorded, root)
    assert found == sorted(["gone.tif", IMAGES[1], "unrecorded.tif"])
    assert content_changed(recorded, root, 4) == found


def _entry(rel):
    return FileEntry(rel, 0, 0, ("c", "r", "t"), "TYX")


@pytest.mark.parametrize("workers", [1, 3])
def test_a_strict_run_hashes_with_its_workers_and_writes_the_same_table(
    tmp_path, experiment, monkeypatch, workers
):
    root, out = experiment
    serial = run_described(root, TYX, output_folder=tmp_path / "serial")
    assert serial.written
    hashed, used = source_content.current_content, []

    def recording(path):
        used.append(threading.current_thread().name)
        return hashed(path)

    monkeypatch.setattr(source_content, "current_content", recording)
    mode = RunMode("restart", verify_content=True, workers=workers)
    result = run_described(root, TYX, output_folder=out, mode=mode)
    assert result.written and result.csv_path.read_bytes() == serial.csv_path.read_bytes()
    pooled = [name for name in used if name.startswith("morphobricks-sha")]
    # Before the reads and before the commit: each image once in each pass.
    assert len(pooled) == (2 * len(IMAGES) if workers > 1 else 0)
