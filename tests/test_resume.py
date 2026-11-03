"""Per-file staging and resume (design v0.4 §1, PP-12) and source changes during a run (#90).

Each file's rows are staged as soon as it is read. A run killed part-way (a crash, a kill,
a lost Colab session) resumes on the next run from the staged rows of files that are as
they were, and recomputes only the rest; staged rows whose image, settings or version
changed are discarded and reported, never reused. A file that changes while the run checks
or reads it is never published.
"""

import json
import os
import signal
import subprocess
import sys

import numpy as np
import pytest
from morphobricks_normalization.csv_io import OUTPUT_FILENAME

from morphobricks_preprocessing import PlaneReadError, RunInputs, prefill_mapping, run_experiment
from morphobricks_preprocessing import run as run_module
from morphobricks_preprocessing.discovery_report import REPORT_FILENAME
from morphobricks_preprocessing.run.lock import remove_stale_lock
from morphobricks_preprocessing.run.staging import STAGING_FOLDER
from run_files import outputs
from tiff_tree import write_data

ZYX = prefill_mapping("ZYX")
IMAGES = ["ctrl/r1/a.tif", "ctrl/r1/b.tif", "ctrl/r2/a.tif", "drug/r1/a.tif", "drug/r2/a.tif"]


def stack(seed):
    return np.random.default_rng(seed).integers(0, 255, (2, 8, 8), dtype=np.uint8)


def change(path, seed=99):
    before = path.stat().st_mtime_ns
    write_data(path, stack(seed), "ZYX")
    os.utime(path, ns=(before + 10**9, before + 10**9))


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    for seed, rel in enumerate(IMAGES):
        write_data(root / rel, stack(seed), "ZYX")
    return root, tmp_path / "out"


def reference_table(tmp_path, root):
    """The table of an uninterrupted run on the experiment as it is."""
    result = run_experiment(root, ZYX, output_folder=tmp_path / "reference")
    assert result.written
    return result.csv_path.read_bytes()


def report(out):
    return json.loads((out / REPORT_FILENAME).read_text(encoding="utf-8"))


KILLED_RUN = """
import os, signal, sys
from pathlib import Path
from morphobricks_preprocessing import prefill_mapping, run_experiment
from morphobricks_preprocessing.run import resume

root, out, kill_at = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
read = resume._read_file
calls = []

def killing(*args, **kwargs):
    calls.append(args[1])
    if len(calls) == kill_at:
        os.kill(os.getpid(), signal.SIGKILL)
    return read(*args, **kwargs)

resume._read_file = killing
run_experiment(root, prefill_mapping("ZYX"), output_folder=out)
"""


def killed_run(tmp_path, root, out, kill_at):
    """Run in another process and kill it as it starts reading file number ``kill_at``."""
    script = tmp_path / "killed_run.py"
    script.write_text(KILLED_RUN, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    done = subprocess.run(
        [sys.executable, str(script), str(root), str(out), str(kill_at)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert done.returncode == -signal.SIGKILL, done.stderr
    remove_stale_lock(out)  # the user confirms the killed run is gone


@pytest.mark.skipif(os.name != "posix", reason="SIGKILL is POSIX only")
def test_a_killed_run_resumes_from_the_staged_rows(tmp_path, experiment):
    root, out = experiment
    killed_run(tmp_path, root, out, kill_at=4)
    assert not (out / OUTPUT_FILENAME).exists()  # nothing was published
    assert len(list((out / STAGING_FOLDER).glob("*.json"))) == 3
    result = run_experiment(root, ZYX, output_folder=out)
    assert result.written
    assert result.resumed == tuple(IMAGES[:3]) and result.read == tuple(IMAGES[3:])
    assert result.csv_path.read_bytes() == reference_table(tmp_path, root)
    assert not (out / STAGING_FOLDER).exists()
    found = report(out)
    assert found["resumed"] == IMAGES[:3] and sorted(found["recomputed"]) == IMAGES[3:]
    assert "Resumed: 3 images" in (out / "discovery_report.txt").read_text(encoding="utf-8")
    RunInputs.open(out)


@pytest.mark.skipif(os.name != "posix", reason="SIGKILL is POSIX only")
def test_a_resumed_restart_reads_only_the_files_it_must(tmp_path, experiment):
    root, out = experiment
    assert run_experiment(root, ZYX, output_folder=out).written
    for rel in IMAGES[1:4]:
        change(root / rel, seed=50 + len(rel))
    killed_run(tmp_path, root, out, kill_at=3)  # b and r2/a staged, killed at drug/r1/a
    result = run_experiment(root, output_folder=out)
    assert result.written and result.unchanged == (IMAGES[0], IMAGES[4])
    assert result.resumed == tuple(IMAGES[1:3]) and result.read == (IMAGES[3],)
    assert result.csv_path.read_bytes() == reference_table(tmp_path, root)


@pytest.mark.skipif(os.name != "posix", reason="SIGKILL is POSIX only")
def test_staged_rows_of_an_image_changed_since_are_discarded(tmp_path, experiment):
    root, out = experiment
    killed_run(tmp_path, root, out, kill_at=4)
    change(root / IMAGES[1])
    result = run_experiment(root, ZYX, output_folder=out)
    assert result.written
    assert result.resumed == (IMAGES[0], IMAGES[2])
    assert result.read == (IMAGES[1], *IMAGES[3:])
    assert report(out)["staged_discarded"] == {
        IMAGES[1]: "the image changed since its rows were staged"
    }
    assert result.csv_path.read_bytes() == reference_table(tmp_path, root)


@pytest.mark.skipif(os.name != "posix", reason="SIGKILL is POSIX only")
@pytest.mark.parametrize(
    ("given", "why"),
    [
        ({"bin_count": 128}, "its axis roles, labels, folder or row settings changed"),
        ({"mode": "full"}, "a full run reuses no staged row"),
    ],
)
def test_staged_rows_are_not_reused_with_other_settings_or_in_full_mode(
    tmp_path, experiment, given, why
):
    root, out = experiment
    killed_run(tmp_path, root, out, kill_at=3)
    result = run_experiment(root, ZYX, output_folder=out, **given)
    assert result.written and result.resumed == () and result.read == tuple(IMAGES)
    discarded = report(out)["staged_discarded"]
    assert sorted(discarded) == IMAGES[:2]
    assert all(reason.startswith(why) for reason in discarded.values())


@pytest.mark.skipif(os.name != "posix", reason="SIGKILL is POSIX only")
def test_damaged_staged_rows_are_discarded_and_reported(tmp_path, experiment):
    root, out = experiment
    killed_run(tmp_path, root, out, kill_at=3)
    (rows,) = sorted((out / STAGING_FOLDER).glob("*.csv"))[:1]
    rows.write_text(rows.read_text(encoding="utf-8").replace("ctrl", "drug"), encoding="utf-8")
    (half,) = sorted((out / STAGING_FOLDER).glob("*.json"))[1:2]
    half.unlink()  # a run killed between the rows file and its record: never staged
    result = run_experiment(root, ZYX, output_folder=out)
    assert result.written and result.resumed == () and result.read == tuple(IMAGES)
    (reason,) = report(out)["staged_discarded"].values()
    assert reason.startswith("its staged rows could not be read")


def test_a_stopped_run_keeps_its_staged_rows_and_a_published_run_removes_them(
    experiment, monkeypatch
):
    root, out = experiment
    read = run_module.resume._read_file

    def unreadable(path, rel, *args):
        if rel == IMAGES[-1]:
            raise PlaneReadError("simulated")
        return read(path, rel, *args)

    with monkeypatch.context() as patch:
        patch.setattr(run_module.resume, "_read_file", unreadable)
        stopped = run_experiment(root, ZYX, output_folder=out)
    assert not stopped.written and stopped.read == tuple(IMAGES[:-1])
    assert (out / STAGING_FOLDER).is_dir()
    result = run_experiment(root, ZYX, output_folder=out)
    assert result.written and result.read == (IMAGES[-1],)
    assert result.resumed == tuple(IMAGES[:-1])
    assert not (out / STAGING_FOLDER).exists()


# Issue #90: sources that change while the run checks or reads them -------------------------


def test_an_image_changed_while_it_is_read_is_never_published(tmp_path, experiment, monkeypatch):
    root, out = experiment
    assert run_experiment(root, ZYX, output_folder=out).written
    before = outputs(out)
    change(root / IMAGES[2])
    read = run_module.resume._read_file

    def written_meanwhile(path, rel, *args):
        rows = read(path, rel, *args)
        if rel == IMAGES[2]:
            change(path, seed=77)  # an acquisition still writing the file
        return rows

    with monkeypatch.context() as patch:
        patch.setattr(run_module.resume, "_read_file", written_meanwhile)
        result = run_experiment(root, output_folder=out)
    assert not result.written
    (found,) = [i for i in result.issues if i.code == "source_changed_during_run"]
    assert found.is_error and found.paths == (IMAGES[2],)
    assert outputs(out) == before  # the earlier generation is intact
    assert not list((out / STAGING_FOLDER).glob("*.json"))  # its rows were not staged
    again = run_experiment(root, output_folder=out)
    assert again.written and again.read == (IMAGES[2],)
    assert again.csv_path.read_bytes() == reference_table(tmp_path, root)


def test_an_image_changed_after_it_was_read_stops_the_publication(experiment, monkeypatch):
    # Every image is fingerprinted again just before the commit, the reused ones included.
    root, out = experiment
    assert run_experiment(root, ZYX, output_folder=out).written
    before = outputs(out)
    change(root / IMAGES[3])
    changed_since = run_module.pipeline.changed_since

    def meanwhile(entries, where):
        change(root / IMAGES[0], seed=5)  # a reused image
        change(root / IMAGES[3], seed=6)  # the image just read and staged
        return changed_since(entries, where)

    with monkeypatch.context() as patch:
        patch.setattr(run_module.pipeline, "changed_since", meanwhile)
        result = run_experiment(root, output_folder=out)
    assert not result.written
    (found,) = [i for i in result.issues if i.code == "source_changed_during_run"]
    assert found.paths == (IMAGES[0], IMAGES[3])
    assert outputs(out) == before and not (out / ".publish").exists()
    again = run_experiment(root, output_folder=out)
    assert again.written and again.read == (IMAGES[0], IMAGES[3]) and again.resumed == ()
    assert report(out)["staged_discarded"] == {
        IMAGES[3]: "the image changed since its rows were staged"
    }


def test_an_image_changed_after_its_header_was_read_is_not_read(experiment, monkeypatch):
    root, out = experiment
    entries = run_module.steps.file_entries

    def after_the_header(pre, *args):
        change(root / IMAGES[1])  # between the header check and the manifest entry
        return entries(pre, *args)

    with monkeypatch.context() as patch:
        patch.setattr(run_module.steps, "file_entries", after_the_header)
        result = run_experiment(root, ZYX, output_folder=out)
    assert not result.written and IMAGES[1] not in result.read
    (found,) = [i for i in result.issues if i.code == "source_changed_during_run"]
    assert found.paths == (IMAGES[1],)


def test_a_chunk_changed_while_an_ome_zarr_image_is_read_is_never_published(tmp_path, monkeypatch):
    pytest.importorskip("zarr")
    from zarr_tree import write_ngff

    root, out = tmp_path / "exp", tmp_path / "out"
    data = np.stack([np.full((8, 8), 5, np.uint8), np.full((8, 8), 9, np.uint8)])
    image = write_ngff(root / "ctrl" / "a.ome.zarr", data, "zyx", chunks=(1, 8, 8))
    read = run_module.resume._read_file

    def chunk_rewritten(path, rel, *args):
        rows = read(path, rel, *args)
        chunk = sorted(p for p in (image / "0").iterdir() if not p.name.startswith("."))[0]
        stat = chunk.stat()
        chunk.write_bytes(chunk.read_bytes())  # same bytes and size, another time
        # 1 ms later: NTFS keeps times in 100 ns steps, so 1 ns would be rounded away.
        os.utime(chunk, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**6))
        return rows

    monkeypatch.setattr(run_module.resume, "_read_file", chunk_rewritten)
    result = run_experiment(root, ZYX, output_folder=out)
    assert not result.written
    assert [i.code for i in result.issues if i.is_error] == ["source_changed_during_run"]


@pytest.mark.skipif(os.name != "posix", reason="SIGKILL is POSIX only")
def test_the_summary_names_the_resumed_images(tmp_path, experiment):
    from morphobricks_preprocessing.report import run_summary

    root, out = experiment
    killed_run(tmp_path, root, out, kill_at=3)
    result = run_experiment(root, ZYX, output_folder=out)
    summary = run_summary(result)
    assert "Resumed (rows staged by an interrupted run): 2 images" in summary
    # F11 (cross-brick test, 3 November 2026): not "All rows were computed".
    assert "All rows were computed" not in summary
    assert "the rows staged by the interrupted run were resumed (2 images)" in summary


@pytest.mark.skipif(os.name != "posix", reason="process IDs are checked on POSIX only")
def test_a_lock_left_by_a_killed_run_is_called_stale_and_kept(tmp_path, experiment, capsys):
    import json
    import socket

    from morphobricks_preprocessing.cli import main
    from morphobricks_preprocessing.run.lock import LOCK_FILENAME
    from run_files import finished_pid

    root, out = experiment
    out.mkdir(parents=True, exist_ok=True)
    lock = out / LOCK_FILENAME
    lock.write_text(json.dumps({"host": socket.gethostname(), "pid": finished_pid()}))
    assert main(["run", str(root), "--output", str(out), "--axes", "ZYX"]) == 1
    text = capsys.readouterr().out
    assert "Another run is using" not in text
    assert "lock looks stale" in text and "no longer running" in text
    assert "--remove-stale-lock" in text and "Remove the lock of a stopped run" in text
    assert lock.exists()  # never removed without the user's request


@pytest.mark.skipif(os.name != "posix", reason="SIGKILL is POSIX only")
def test_a_discarded_record_is_reported_by_its_image_and_its_true_reason(tmp_path, experiment):
    # Review of PR #132: another pixel type was reported as changed roles or settings, and
    # unreadable rows by the record's hashed file name instead of the image.
    root, out = experiment
    killed_run(tmp_path, root, out, kill_at=3)
    first, second = sorted((out / STAGING_FOLDER).glob("*.json"))
    record = json.loads(first.read_text(encoding="utf-8"))
    record["key"]["dtype"] = "uint16"
    first.write_text(json.dumps(record), encoding="utf-8")
    rows = second.with_suffix(".csv")
    rows.write_text(rows.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    named = json.loads(second.read_text(encoding="utf-8"))["relative_path"]
    result = run_experiment(root, ZYX, output_folder=out)
    assert result.written and result.resumed == ()
    discarded = report(out)["staged_discarded"]
    assert discarded[record["relative_path"]].startswith("the experiment's pixel type changed")
    assert discarded[named].startswith("its staged rows could not be read")
