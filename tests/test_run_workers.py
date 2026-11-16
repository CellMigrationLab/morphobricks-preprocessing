"""Image-level workers: one image per worker thread, outputs as with one (issue #107).

A run given ``RunMode(workers=N)`` or ``workers="auto"`` reads each image in a worker thread
that only reads it and checks it after the read; the calling thread alone resumes, stages,
aggregates, checks and publishes. The outputs are byte for byte those of the serial run, and
the failure rules are the serial ones.
"""

import json
import threading
import time

import numpy as np
import pytest

from described_tree import main_described, run_described
from morphobricks_preprocessing import PlaneReadError, prefill_mapping, read_manifest
from morphobricks_preprocessing import run as run_module
from morphobricks_preprocessing.cli import build_parser
from morphobricks_preprocessing.manifest import RunSettings
from morphobricks_preprocessing.planes import plane_layout
from morphobricks_preprocessing.report import run_summary
from morphobricks_preprocessing.run import RunMode
from morphobricks_preprocessing.run import workers as workers_module
from morphobricks_preprocessing.run.staging import STAGING_FOLDER
from morphobricks_preprocessing.run.workers import (
    PARENT_RESERVE,
    WorkerPlan,
    Workers,
    image_memory,
    plan_workers,
)
from run_files import outputs, without_report
from tiff_tree import write_data

TYX = prefill_mapping("TYX")
IMAGES = [f"{c}/r{r}/{n}.tif" for c in ("ctrl", "drug") for r in (1, 2) for n in ("a", "b")]


def stack(seed, side=8):
    return np.random.default_rng(seed).integers(0, 255, (2, side, side), dtype=np.uint8)


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    for seed, rel in enumerate(IMAGES):
        write_data(root / rel, stack(seed), "TYX")
    return root


def mode(workers, max_ram=None):
    return RunMode("restart", verify_content=True, workers=workers, max_ram=max_ram)


def published(out):
    """The table and design bytes, and the manifest without its time (every run's own)."""
    files = without_report(outputs(out))
    manifest = json.loads(files.pop("run_manifest.json"))
    manifest.pop("written_at")
    return files, manifest


def run_with(root, out, workers, **kwargs):
    result = run_described(root, TYX, output_folder=out, mode=mode(workers), **kwargs)
    assert result.written, result.issues
    return result


# Identical outputs ----------------------------------------------------------------------------


def test_any_number_of_workers_publishes_the_serial_outputs(tmp_path, experiment):
    serial = run_with(experiment, tmp_path / "w1", 1)
    expected = published(tmp_path / "w1")
    assert expected[0]["plane_histograms.csv"] and serial.workers == 1
    for workers in (2, 8, "auto"):
        result = run_with(experiment, tmp_path / f"w{workers}", workers)
        assert published(tmp_path / f"w{workers}") == expected  # CSV bytes, manifest contents
        assert result.read == serial.read == tuple(IMAGES)
        cpus = min(len(IMAGES), workers_module.available_cpus())
        assert result.workers == (cpus if workers == "auto" else workers)


def reverse_order_reads(read, slower_later=0.0):
    """A ``_read_file`` whose reads finish last image first, however long each read takes.

    Each image's read waits until the next image's read has finished, so the order does not
    depend on sleeps or thread scheduling (sleeps alone failed on the macOS runners). It needs
    every image's read running at once: a wait that times out fails the run.
    ``slower_later`` adds that many seconds per position to each read, so the later images'
    reads take longest, the case in which staggered sleeps did not give the reverse order.
    """
    finished, done = [], {rel: threading.Event() for rel in IMAGES}

    def last_first(path, rel, *args):
        index = IMAGES.index(rel)
        if index + 1 < len(IMAGES):
            after = IMAGES[index + 1]
            assert done[after].wait(timeout=30), f"{rel} waited for {after}: not concurrent"
        time.sleep(slower_later * index)
        rows = read(path, rel, *args)
        finished.append(rel)
        done[rel].set()
        return rows

    return last_first, finished


@pytest.mark.parametrize("slower_later", [0.0, 0.05])
def test_workers_finishing_out_of_order_change_no_output(
    tmp_path, experiment, monkeypatch, slower_later
):
    serial = run_with(experiment, tmp_path / "serial", 1)
    last_first, finished = reverse_order_reads(run_module.resume._read_file, slower_later)
    monkeypatch.setattr(run_module.resume, "_read_file", last_first)
    result = run_with(experiment, tmp_path / "parallel", len(IMAGES))
    assert finished == IMAGES[::-1]  # the workers really finished in the reverse order
    assert result.read == serial.read == tuple(IMAGES)
    assert published(tmp_path / "parallel") == published(tmp_path / "serial")


@pytest.mark.parametrize("workers", [2, "auto"])
def test_tiff_and_ome_zarr_read_whole_and_in_blocks_give_the_serial_outputs(tmp_path, workers):
    pytest.importorskip("zarr")
    from zarr_tree import write_ngff

    root = tmp_path / "exp"
    big = np.random.default_rng(1).integers(0, 255, (2, 128, 128), dtype=np.uint8)
    write_data(root / "ctrl/r1/small.tif", stack(2), "TYX")
    write_data(root / "ctrl/r1/big.tif", big, "TYX", rowsperstrip=8)
    write_ngff(root / "drug/r1/small.ome.zarr", stack(3), "tyx")
    write_ngff(root / "drug/r1/big.ome.zarr", big[::-1], "tyx", chunks=(1, 16, 16))
    layouts = [plane_layout(root / f, TYX, memory_limit=1) for f in ("ctrl/r1/big.tif",)]
    limit = max(
        layouts[0].block_memory_estimate,
        plane_layout(root / "drug/r1/big.ome.zarr", TYX, memory_limit=1).block_memory_estimate,
        plane_layout(root / "ctrl/r1/small.tif", TYX).memory_estimate,
        plane_layout(root / "drug/r1/small.ome.zarr", TYX).memory_estimate,
    )
    assert layouts[0].memory_estimate > limit
    serial = run_with(root, tmp_path / "serial", 1, memory_limit=limit)
    result = run_with(root, tmp_path / "parallel", workers, memory_limit=limit)
    blocks = {f.relative_path: f.read_in_blocks for f in read_manifest(serial.manifest_path).files}
    assert blocks == {
        "ctrl/r1/big.tif": True,
        "ctrl/r1/small.tif": False,
        "drug/r1/big.ome.zarr": True,
        "drug/r1/small.ome.zarr": False,
    }
    assert result.workers > 1 and result.read == serial.read
    assert published(tmp_path / "parallel") == published(tmp_path / "serial")


# The calling thread owns the run ---------------------------------------------------------------


def test_workers_only_read_and_progress_is_reported_in_the_calling_thread(
    tmp_path, experiment, monkeypatch
):
    read, readers, calls = run_module.resume._read_file, set(), []
    stage = run_module.resume.stage_file
    stagers = set()

    def recording_read(path, rel, *args):
        readers.add(threading.current_thread())
        return read(path, rel, *args)

    def recording_stage(*args):
        stagers.add(threading.current_thread())
        return stage(*args)

    def progress(done, total, rel):
        calls.append((threading.current_thread(), done, total, rel))

    monkeypatch.setattr(run_module.resume, "_read_file", recording_read)
    monkeypatch.setattr(run_module.resume, "stage_file", recording_stage)
    out = tmp_path / "out"
    result = run_described(experiment, TYX, output_folder=out, mode=mode(4), progress=progress)
    assert result.written and result.workers == 4
    main = threading.main_thread()
    assert main not in readers and stagers == {main}
    assert {thread for thread, *_ in calls} == {main}
    assert [done for _, done, _, _ in calls] == list(range(len(IMAGES) + 1))
    assert sorted(rel for *_, rel in calls[:-1]) == IMAGES and calls[-1][3] == ""


def test_one_worker_reads_in_the_calling_thread_as_before(tmp_path, experiment, monkeypatch):
    read, readers, calls = run_module.resume._read_file, set(), []

    def recording_read(path, rel, *args):
        readers.add(threading.current_thread())
        return read(path, rel, *args)

    monkeypatch.setattr(run_module.resume, "_read_file", recording_read)
    out = tmp_path / "out"
    result = run_described(
        experiment, TYX, output_folder=out, progress=lambda *a: calls.append(a)
    )  # the default: RunMode(workers=1)
    assert result.written and result.workers == 1
    assert readers == {threading.main_thread()}
    expected = [(i, len(IMAGES), rel) for i, rel in enumerate(IMAGES)]
    assert calls == [*expected, (len(IMAGES), len(IMAGES), "")]


# Failures as with one worker -----------------------------------------------------------------


def errors(result):
    return sorted((issue.code, issue.paths) for issue in result.issues if issue.is_error)


@pytest.mark.parametrize("workers", [1, 3])
def test_an_unreadable_image_stops_the_run_as_without_workers(
    tmp_path, experiment, monkeypatch, workers
):
    read = run_module.resume._read_file

    def unreadable(path, rel, *args):
        if rel == IMAGES[1]:
            raise PlaneReadError("simulated")
        return read(path, rel, *args)

    monkeypatch.setattr(run_module.resume, "_read_file", unreadable)
    out = tmp_path / "out"
    result = run_described(experiment, TYX, output_folder=out, mode=mode(workers))
    assert not result.written
    assert errors(result) == [("unreadable_planes", (IMAGES[1],))]
    assert result.read == tuple(rel for rel in IMAGES if rel != IMAGES[1])
    assert "plane_histograms.csv" not in outputs(out)
    assert len(list((out / STAGING_FOLDER).glob("*.json"))) == len(IMAGES) - 1


def test_an_image_changed_while_a_worker_reads_it_is_never_staged(
    tmp_path, experiment, monkeypatch
):
    read = run_module.resume._read_file

    def written_meanwhile(path, rel, *args):
        rows = read(path, rel, *args)
        if rel == IMAGES[2]:
            before = path.stat().st_mtime_ns
            write_data(path, stack(77), "TYX")  # an acquisition still writing the file
            path.touch()
            assert path.stat().st_mtime_ns != before
        return rows

    monkeypatch.setattr(run_module.resume, "_read_file", written_meanwhile)
    out = tmp_path / "out"
    result = run_described(experiment, TYX, output_folder=out, mode=mode(3))
    assert not result.written
    assert errors(result) == [("source_changed_during_run", (IMAGES[2],))]
    assert "plane_histograms.csv" not in outputs(out)
    staged = {
        json.loads(p.read_text())["relative_path"] for p in (out / STAGING_FOLDER).glob("*.json")
    }
    assert staged == set(IMAGES) - {IMAGES[2]}


def test_a_worker_exception_publishes_nothing_and_the_next_run_resumes(
    tmp_path, experiment, monkeypatch
):
    reference = run_with(experiment, tmp_path / "reference", 1)
    read = run_module.resume._read_file
    out = tmp_path / "out"

    def failing(path, rel, *args):
        if rel == IMAGES[0]:
            # Fail once the calling thread has staged every other image.
            deadline = time.monotonic() + 30
            while len(list((out / STAGING_FOLDER).glob("*.json"))) < len(IMAGES) - 1:
                assert time.monotonic() < deadline
                time.sleep(0.01)
            raise RuntimeError("an unexpected failure")
        return read(path, rel, *args)

    out = tmp_path / "out"
    with monkeypatch.context() as patch:
        patch.setattr(run_module.resume, "_read_file", failing)
        with pytest.raises(RuntimeError, match="an unexpected failure") as raised:
            run_described(experiment, TYX, output_folder=out, mode=mode(4))
    # The image is named: the exception reaches the calling thread from another one.
    assert raised.value.__notes__ == [f"While reading the image {IMAGES[0]}"]
    assert "plane_histograms.csv" not in outputs(out)
    assert len(list((out / STAGING_FOLDER).glob("*.json"))) == len(IMAGES) - 1
    assert not (out / run_module.LOCK_FILENAME).exists()  # the lock is released
    again = run_with(experiment, out, 4)
    assert again.resumed == tuple(IMAGES[1:]) and again.read == (IMAGES[0],)
    assert again.csv_path.read_bytes() == reference.csv_path.read_bytes()


def test_staged_images_are_not_read_again_by_workers(tmp_path, experiment, monkeypatch):
    read = run_module.resume._read_file

    def unreadable(path, rel, *args):
        if rel == IMAGES[-1]:
            raise PlaneReadError("simulated")
        return read(path, rel, *args)

    out = tmp_path / "out"
    with monkeypatch.context() as patch:
        patch.setattr(run_module.resume, "_read_file", unreadable)
        assert not run_described(experiment, TYX, output_folder=out, mode=mode(4)).written
    read_now = []

    def recording(path, rel, *args):
        read_now.append(rel)
        return read(path, rel, *args)

    monkeypatch.setattr(run_module.resume, "_read_file", recording)
    result = run_with(experiment, out, "auto")
    assert result.resumed == tuple(IMAGES[:-1]) and read_now == [IMAGES[-1]]
    assert result.workers == 1  # the CPU cap: one image to read


# workers="auto" and max_ram --------------------------------------------------------------------


def test_auto_never_exceeds_the_cpu_cap_or_the_ram_budget(tmp_path, experiment, monkeypatch):
    settings = RunSettings(256, 2**30)
    names = list(IMAGES)
    estimates = dict.fromkeys(names, 10 * 2**20)
    estimates[names[3]] = 40 * 2**20  # the largest image counts for every worker
    monkeypatch.setattr(workers_module, "image_memory", lambda p, m, s: estimates[_rel(p)])
    mappings = dict.fromkeys(names, TYX)
    for cpus in (1, 3, 64):
        monkeypatch.setattr(workers_module, "available_cpus", lambda cpus=cpus: cpus)
        no_ram = plan_workers(Workers("auto"), experiment, names, mappings, settings)
        assert no_ram == WorkerPlan(min(cpus, len(names)))
        for budget_mib in (40, 80, 120, 1000):
            max_ram = PARENT_RESERVE + budget_mib * 2**20
            plan = plan_workers(Workers("auto", max_ram), experiment, names, mappings, settings)
            assert plan.refused is None
            assert plan.count == min(cpus, len(names), budget_mib // 40)
            assert plan.count * 40 * 2**20 <= max_ram - PARENT_RESERVE
    refused = plan_workers(
        Workers("auto", PARENT_RESERVE + 20 * 2**20), experiment, names, mappings, settings
    )
    assert refused.count == 0 and refused.refused.code == "image_over_max_ram"
    assert refused.refused.paths == (names[3],)
    assert plan_workers(Workers(5), experiment, names, mappings, settings) == WorkerPlan(5)


def _rel(path):
    return "/".join(path.parts[-3:])


def test_auto_reads_at_most_the_planned_images_at_once(tmp_path, experiment, monkeypatch):
    settings = RunSettings(256, 2**30)
    largest = max(image_memory(experiment / rel, TYX, settings) for rel in IMAGES)
    read, active, most = run_module.resume._read_file, [0], [0]
    lock = threading.Lock()

    pair = threading.Barrier(2, timeout=30)  # two reads at once, without a timing assumption

    def counting(path, rel, *args):
        with lock:
            active[0] += 1
            most[0] = max(most[0], active[0])
        pair.wait()  # a third read at once is counted above; one worker alone times out
        try:
            return read(path, rel, *args)
        finally:
            with lock:
                active[0] -= 1

    monkeypatch.setattr(run_module.resume, "_read_file", counting)
    monkeypatch.setattr(workers_module, "available_cpus", lambda: 8)
    max_ram = PARENT_RESERVE + 2 * largest + 1
    out = tmp_path / "out"
    result = run_described(experiment, TYX, output_folder=out, mode=mode("auto", max_ram))
    assert result.written and result.workers == 2 and most[0] == 2


def test_an_image_over_max_ram_stops_the_run_before_any_image_is_read(
    tmp_path, experiment, monkeypatch
):
    read_now = []
    read = run_module.resume._read_file
    monkeypatch.setattr(
        run_module.resume, "_read_file", lambda p, rel, *a: read_now.append(rel) or read(p, rel, *a)
    )
    out = tmp_path / "out"
    result = run_described(
        experiment, TYX, output_folder=out, mode=mode("auto", PARENT_RESERVE + 1)
    )
    assert not result.written and read_now == [] and result.read == ()
    (found,) = [issue for issue in result.issues if issue.is_error]
    assert found.code == "image_over_max_ram" and found.paths == tuple(IMAGES)
    assert "max_ram" in found.message and "plane_histograms.csv" not in outputs(out)


def test_the_estimate_is_the_header_working_memory_not_the_file_size(experiment):
    settings = RunSettings(256, 2**30)
    layout = plane_layout(experiment / IMAGES[0], TYX, bin_count=256)
    expected = layout.memory_estimate + layout.plane_count * 8 * 256
    assert image_memory(experiment / IMAGES[0], TYX, settings) == expected
    assert expected != (experiment / IMAGES[0]).stat().st_size


@pytest.mark.parametrize(
    ("workers", "max_ram"),
    [
        (0, None),
        (-1, None),
        (True, None),
        (1.5, None),
        ("4", None),
        ("all", None),
        (2, 2**30),
        (1, 2**30),
        ("auto", 0),
        ("auto", True),
        ("auto", 1.5),
    ],
)
def test_workers_and_max_ram_that_are_not_allowed_are_refused(
    tmp_path, experiment, workers, max_ram
):
    with pytest.raises(ValueError, match="workers|max_ram"):
        run_described(experiment, TYX, output_folder=tmp_path / "out", mode=mode(workers, max_ram))
    assert not (tmp_path / "out").exists()


def test_workers_are_in_no_row_key_or_output(tmp_path, experiment):
    out = tmp_path / "out"
    run_with(experiment, out, 1)
    before = published(out)
    again = run_with(experiment, out, 4)
    assert again.read == () and again.unchanged == tuple(IMAGES)  # nothing read again
    assert published(out) == before
    assert "workers" not in keys(read_manifest(again.manifest_path).to_dict())


def keys(data):
    """Every key of a JSON document, at any depth."""
    if isinstance(data, dict):
        return {k for key, value in data.items() for k in (key, *keys(value))}
    if isinstance(data, list):
        return {k for value in data for k in keys(value)}
    return set()


# The command line ------------------------------------------------------------------------------


def test_the_command_line_reads_with_workers(tmp_path, experiment, capsys):
    serial = tmp_path / "serial"
    assert main_described(["run", experiment, "--axes", "TYX", "--output", serial]) == 0
    parallel = tmp_path / "parallel"
    argv = ["run", experiment, "--axes", "TYX", "--output", parallel, "--workers", "3"]
    assert main_described(argv) == 0
    assert "Read with 3 workers (one image each)" in capsys.readouterr().out
    assert published(parallel) == published(serial)
    auto = tmp_path / "auto"
    argv = ["run", experiment, "--axes", "TYX", "--output", auto, "--workers", "auto"]
    assert main_described([*argv, "--max-ram", "4GiB"]) == 0
    assert published(auto) == published(serial)


def test_the_command_line_refuses_max_ram_without_auto(tmp_path, experiment, capsys):
    argv = ["run", experiment, "--axes", "TYX", "--output", tmp_path / "out", "--max-ram", "1GiB"]
    assert main_described(argv) == 1
    assert "--max-ram is used only with --workers auto" in capsys.readouterr().out
    assert not (tmp_path / "out" / "plane_histograms.csv").exists()


@pytest.mark.parametrize("text", ["0", "-2", "1.5", "two", ""])
def test_the_command_line_refuses_a_workers_value_that_is_not_allowed(text, capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "exp", "--workers", text])
    assert "--workers" in capsys.readouterr().err


def test_an_unexpected_exception_names_the_image_without_workers_too(
    tmp_path, experiment, monkeypatch
):
    def failing(path, rel, *args):
        raise RuntimeError("an unexpected failure")

    monkeypatch.setattr(run_module.resume, "_read_file", failing)
    with pytest.raises(RuntimeError, match="an unexpected failure") as raised:
        run_described(experiment, TYX, output_folder=tmp_path / "out", mode=mode(1))
    assert raised.value.__notes__ == [f"While reading the image {IMAGES[0]}"]

