"""A strict run hashes every source again right before its commit (release review of rc5).

Content is hashed when the entries are made and again after each fresh pixel read, but
the check just before the commit compared only the fingerprint: a rewrite that restored
the size and modification time after the last hashing published stale rows under a strict
manifest. Now the last step before the manifest's rename hashes every recorded source,
reused or freshly read (an OME-Zarr store by its store digest), and refuses with
``source_changed_during_run``. The rewrite is injected right before that last check.
"""

import os

import pytest

from morphobricks_preprocessing import run as run_module
from morphobricks_preprocessing import run_experiment
from morphobricks_preprocessing.run.generation import PUBLISH_FOLDER
from strict_tree import IMAGES, STRICT, ZYX, rewrite_keeping_metadata, stack, write_experiment
from tiff_tree import write_data

OUTPUTS = ("run_manifest.json", "plane_histograms.csv")


@pytest.fixture
def experiment(tmp_path):
    return write_experiment(tmp_path)


def rewrite_before_final_check(monkeypatch, paths):
    """Rewrite ``paths`` (size and time kept) right before the last content check."""
    hashed, calls = run_module.pipeline.content_changed, []

    def rewritten_first(entries, root):
        calls.append(sorted(entry.relative_path for entry in entries))
        # The generation is staged and recorded; the manifest is not yet replaced.
        assert (root.parent / "out" / PUBLISH_FOLDER / "publication.json").exists()
        for path in paths:
            rewrite_keeping_metadata(path)
        return hashed(entries, root)

    monkeypatch.setattr(run_module.pipeline, "content_changed", rewritten_first)
    return calls


def outputs(out):
    return {name: (out / name).read_bytes() for name in OUTPUTS}


@pytest.mark.parametrize("which", ["reused", "read", "both"])
def test_a_rewrite_right_before_the_commit_is_refused(experiment, monkeypatch, which):
    root, out = experiment
    assert run_experiment(root, ZYX, output_folder=out, mode=STRICT).written
    earlier = outputs(out)
    # IMAGES[0] changes for real (it is read again); IMAGES[1] and [2] are reused.
    write_data(root / IMAGES[0], stack(5), "ZYX")
    os.utime(root / IMAGES[0], ns=(1, 1_000_000_000))
    read, reused = root / IMAGES[0], root / IMAGES[1]
    paths = {"reused": [reused], "read": [read], "both": [read, reused]}[which]
    calls = rewrite_before_final_check(monkeypatch, paths)
    result = run_experiment(root, output_folder=out)
    assert calls == [IMAGES]  # one last check, of every recorded image
    assert result.read == (IMAGES[0],) and result.unchanged == tuple(IMAGES[1:])
    assert not result.written and result.report_written
    (issue,) = [i for i in result.issues if i.code == "source_changed_during_run"]
    assert set(issue.paths) == {p.relative_to(root).as_posix() for p in paths}
    # The earlier generation is intact, and the publication folder discarded.
    assert outputs(out) == earlier
    assert not (out / PUBLISH_FOLDER).exists()
    assert (out / "discovery_report.json").exists()


def test_the_next_strict_run_reads_the_rewritten_images_and_publishes(experiment, monkeypatch):
    root, out = experiment
    assert run_experiment(root, ZYX, output_folder=out, mode=STRICT).written
    with monkeypatch.context() as patch:
        rewrite_before_final_check(patch, [root / IMAGES[1]])
        assert not run_experiment(root, output_folder=out).written
    again = run_experiment(root, output_folder=out)
    assert again.written and again.read == (IMAGES[1],)


def test_a_default_run_has_no_final_content_check(experiment, monkeypatch):
    root, out = experiment
    assert run_experiment(root, ZYX, output_folder=out).written
    calls = rewrite_before_final_check(monkeypatch, [root / IMAGES[1]])
    result = run_experiment(root, ZYX, output_folder=out, mode="full")
    assert result.written and calls == []


def test_an_ome_zarr_chunk_rewritten_right_before_the_commit_is_refused(tmp_path, monkeypatch):
    pytest.importorskip("zarr")
    from zarr_tree import write_ngff

    root, out = tmp_path / "exp", tmp_path / "out"
    store = write_ngff(root / "ctrl/r1/z.ome.zarr", stack(1)[None, :, :, :])
    write_data(root / "ctrl/r1/a.tif", stack(2), "ZYX")
    assert run_experiment(root, ZYX, output_folder=out, mode=STRICT).written
    earlier = outputs(out)
    chunk = sorted(p for p in store.rglob("*") if p.is_file() and p.stat().st_size > 32)[-1]
    hashed = run_module.pipeline.content_changed

    def chunk_rewritten_first(entries, folder):
        data, before = bytearray(chunk.read_bytes()), chunk.stat()
        data[-1] ^= 0xFF
        chunk.write_bytes(bytes(data))
        os.utime(chunk, ns=(before.st_atime_ns, before.st_mtime_ns))
        return hashed(entries, folder)

    monkeypatch.setattr(run_module.pipeline, "content_changed", chunk_rewritten_first)
    result = run_experiment(root, output_folder=out)
    assert not result.written and result.unchanged == ("ctrl/r1/a.tif", "ctrl/r1/z.ome.zarr")
    (issue,) = [i for i in result.issues if i.code == "source_changed_during_run"]
    assert issue.paths == ("ctrl/r1/z.ome.zarr",)
    assert outputs(out) == earlier
