"""Strict reuse by content SHA-256, ``verify_content`` (owner decision, 3 November 2026).

The default identity rule is the fingerprint (size and modification time; for OME-Zarr the
inventory). Its blind spot, a rewrite of the same size with the time set back, is shown
first; a ``verify_content`` run closes it for earlier rows, staged rows and ``RunInputs``.
Turning the mode on or off recomputes every row and names the change.
"""

import json
import os

import pytest

from morphobricks_preprocessing import (
    PlaneReadError,
    RunInputs,
    RunInputsError,
    RunMode,
    prefill_mapping,
    read_manifest,
    run_experiment,
    source_content,
)
from morphobricks_preprocessing import run as run_module
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.discovery_report import REPORT_FILENAME
from morphobricks_preprocessing.source_content import source_content_sha256
from morphobricks_preprocessing.testing import make_clean_run
from strict_tree import IMAGES, STRICT, ZYX, rewrite_keeping_metadata, stack, write_experiment
from tiff_tree import write_data


@pytest.fixture
def experiment(tmp_path):
    return write_experiment(tmp_path)


def reference_table(tmp_path, root):
    result = run_experiment(root, ZYX, output_folder=tmp_path / "fresh", mode="full")
    assert result.written
    return result.csv_path.read_bytes()


def report(out):
    return json.loads((out / REPORT_FILENAME).read_text(encoding="utf-8"))


def test_the_default_rule_does_not_see_a_rewrite_with_its_metadata_kept(experiment):
    # The documented blind spot of the metadata fingerprint: the earlier rows are reused.
    root, out = experiment
    assert run_experiment(root, ZYX, output_folder=out).written
    rewrite_keeping_metadata(root / IMAGES[0])
    again = run_experiment(root, output_folder=out)
    assert again.written and again.read == () and IMAGES[0] in again.unchanged
    RunInputs.open(out)  # the fingerprint is all RunInputs checks by default
    manifest = read_manifest(out / "run_manifest.json")
    assert not manifest.settings.verify_content
    assert all(entry.content_sha256 is None for entry in manifest.files)
    # A default run's manifest has no new key (review of #140): rc4's shape is kept.
    data = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert "verify_content" not in data["settings"]
    assert not any("content_sha256" in entry for entry in data["files"])


def test_a_strict_run_records_each_content_sha256(experiment):
    root, out = experiment
    assert run_experiment(root, ZYX, output_folder=out, mode=STRICT).written
    manifest = read_manifest(out / "run_manifest.json")
    assert manifest.settings.verify_content
    for entry in manifest.files:
        assert entry.content_sha256 == source_content_sha256(root / entry.relative_path)
    data = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert data["settings"]["verify_content"] is True


def test_a_strict_restart_reads_an_image_rewritten_with_its_metadata_kept(tmp_path, experiment):
    root, out = experiment
    assert run_experiment(root, ZYX, output_folder=out, mode=STRICT).written
    rewrite_keeping_metadata(root / IMAGES[0])
    # Given no mode setting, the recorded one is kept.
    again = run_experiment(root, output_folder=out)
    assert again.written and again.read == (IMAGES[0],)
    assert again.rebuild_reason is None and again.changed == (IMAGES[0],)
    assert again.csv_path.read_bytes() == reference_table(tmp_path, root)


def test_turning_verification_on_or_off_recomputes_every_row_and_says_so(experiment):
    root, out = experiment
    assert run_experiment(root, ZYX, output_folder=out).written
    on = run_experiment(root, output_folder=out, mode=STRICT)
    assert on.written and on.read == tuple(IMAGES)
    assert on.rebuild_reason == "content verification (verify_content) was turned on"
    (change,) = [c for c in on.input_changes if c.name == "verify_content"]
    assert (change.recorded, change.given) == ("False", "True")
    kept = run_experiment(root, output_folder=out)
    assert kept.written and kept.read == () and kept.unchanged == tuple(IMAGES)
    off = run_experiment(root, output_folder=out, mode=RunMode("restart", verify_content=False))
    assert off.written and off.read == tuple(IMAGES)
    assert off.rebuild_reason == "content verification (verify_content) was turned off"


def test_staged_rows_are_resumed_only_for_the_same_content(experiment, monkeypatch):
    root, out = experiment
    read = run_module.resume._read_file

    def unreadable(path, rel, *args):
        if rel == IMAGES[-1]:
            raise PlaneReadError("simulated")
        return read(path, rel, *args)

    with monkeypatch.context() as patch:
        patch.setattr(run_module.resume, "_read_file", unreadable)
        stopped = run_experiment(root, ZYX, output_folder=out, mode=STRICT)
    assert not stopped.written and stopped.read == tuple(IMAGES[:-1])
    rewrite_keeping_metadata(root / IMAGES[0])
    result = run_experiment(root, ZYX, output_folder=out, mode=STRICT)
    assert result.written and result.resumed == (IMAGES[1],)
    assert result.read == (IMAGES[0], IMAGES[2])
    assert report(out)["staged_discarded"] == {
        IMAGES[0]: "the image's content (SHA-256) changed since its rows were staged"
    }


def test_staged_rows_of_the_other_mode_are_discarded_with_the_reason(experiment, monkeypatch):
    root, out = experiment
    read = run_module.resume._read_file

    def unreadable(path, rel, *args):
        if rel == IMAGES[-1]:
            raise PlaneReadError("simulated")
        return read(path, rel, *args)

    with monkeypatch.context() as patch:
        patch.setattr(run_module.resume, "_read_file", unreadable)
        assert not run_experiment(root, ZYX, output_folder=out).written
    result = run_experiment(root, ZYX, output_folder=out, mode=STRICT)
    assert result.written and result.resumed == () and result.read == tuple(IMAGES)
    assert set(report(out)["staged_discarded"].values()) == {
        "content verification (verify_content) was turned on or off since then"
    }


def test_an_image_whose_content_changes_while_it_is_read_is_never_published(
    experiment, monkeypatch
):
    root, out = experiment
    assert run_experiment(root, ZYX, output_folder=out, mode=STRICT).written
    before = (out / "plane_histograms.csv").read_bytes()
    write_data(root / "drug/r2/a.tif", stack(7), "ZYX")
    hashed = run_module.resume.current_content

    def changed_after_read(path):
        return "0" * 64 if path.name == "a.tif" and "r2" in path.parts else hashed(path)

    monkeypatch.setattr(run_module.resume, "current_content", changed_after_read)
    result = run_experiment(root, output_folder=out)
    assert not result.written
    (issue,) = [i for i in result.issues if i.code == "source_changed_during_run"]
    # A strict run compared the content too, and says so (review of #147).
    assert "(their size, time, files or content (SHA-256) are no longer" in issue.message
    assert (out / "plane_histograms.csv").read_bytes() == before


def test_an_image_that_cannot_be_hashed_is_unreadable_and_never_read(experiment, monkeypatch):
    # Review of #140: a strict run never reads or reuses an image without its SHA-256.
    root, out = experiment
    hashed = source_content.current_content

    def unhashable(path):
        return None if path.as_posix().endswith(IMAGES[1]) else hashed(path)

    read, opened = run_module.resume._read_file, []

    def recording(path, rel, *args):
        opened.append(rel)
        return read(path, rel, *args)

    monkeypatch.setattr(source_content, "current_content", unhashable)
    monkeypatch.setattr(run_module.resume, "_read_file", recording)
    result = run_experiment(root, ZYX, output_folder=out, mode=STRICT)
    assert not result.written and IMAGES[1] not in opened
    assert sorted(opened) == [IMAGES[0], IMAGES[2]]
    issue = next(issue for issue in result.issues if issue.code == "unreadable_planes")
    assert issue.paths == (IMAGES[1],)


def test_run_inputs_of_a_strict_run_refuse_a_rewrite_with_its_metadata_kept(experiment):
    root, out = experiment
    assert run_experiment(root, ZYX, output_folder=out, mode=STRICT).written
    RunInputs.open(out)
    rewrite_keeping_metadata(root / IMAGES[1])
    with pytest.raises(RunInputsError, match="ctrl/r1/b.tif"):
        RunInputs.open(out)


def test_an_ome_zarr_chunk_rewritten_with_its_metadata_kept_is_read_again(tmp_path):
    pytest.importorskip("zarr")
    from zarr_tree import write_ngff

    root, out = tmp_path / "exp", tmp_path / "out"
    store = write_ngff(root / "ctrl/r1/z.ome.zarr", stack(1)[None, :, :, :])
    write_data(root / "ctrl/r1/a.tif", stack(2), "ZYX")
    mapping = prefill_mapping("ZYX")
    first = run_experiment(root, mapping, output_folder=out, mode=STRICT)
    assert first.written, first.issues
    chunks = sorted(p for p in store.rglob("*") if p.is_file() and p.stat().st_size > 32)
    chunk = chunks[-1]
    data, before = bytearray(chunk.read_bytes()), chunk.stat()
    data[-1] ^= 0xFF
    chunk.write_bytes(bytes(data))
    os.utime(chunk, ns=(before.st_atime_ns, before.st_mtime_ns))
    again = run_experiment(root, output_folder=out)
    assert again.written and again.read == ("ctrl/r1/z.ome.zarr",)


def test_the_cli_turns_content_verification_on(tmp_path):
    clean = make_clean_run(tmp_path / "run", design=True)
    out = clean.output
    assert main(["run", "--output", str(out), "--verify-content"]) == 0
    assert read_manifest(out / "run_manifest.json").settings.verify_content
    assert main(["run", "--output", str(out)]) == 0
    assert read_manifest(out / "run_manifest.json").settings.verify_content
    assert main(["run", "--output", str(out), "--no-verify-content"]) == 0
    assert not read_manifest(out / "run_manifest.json").settings.verify_content


def test_a_mode_with_a_verify_content_that_is_not_a_flag_is_refused(experiment):
    root, out = experiment
    with pytest.raises(ValueError, match="verify_content"):
        run_experiment(root, ZYX, output_folder=out, mode=RunMode("restart", 1))
    with pytest.raises(ValueError, match="mode"):
        run_experiment(root, ZYX, output_folder=out, mode=RunMode("again", True))
