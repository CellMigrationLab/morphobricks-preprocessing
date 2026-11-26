"""The run's own folder and header check shows its progress, and costs O(n) (v0.9.0 owner test).

Before its first image, a run checks the folder and every image header again
(:func:`.run.preflight.preflight` with ``bind_headers``). On a large experiment in Colab the
notebook showed "Preparing: checking the folders and the image headers again · 0:00 elapsed"
for 10-15 minutes: nothing reported that check's progress, and the elapsed time was only
redrawn on a progress report. Two steps of it were also quadratic in the number of images:
the pixel type of :class:`.metadata.MetadataReport` (a pass over every header) taken once per
image for its staging key, and the mask-name screen comparing each image with every other.
"""

import random
import re
import threading
import time
from pathlib import PurePosixPath

import pytest

from described_tree import run_described
from morphobricks_preprocessing import prefill_mapping
from morphobricks_preprocessing.mask_names import _mask_owner, _stems, mask_name_issues
from morphobricks_preprocessing.metadata import MetadataReport
from morphobricks_preprocessing.run import RunMode
from morphobricks_preprocessing.run.phases import CONTENTS, PHASES
from test_notebook_check_save import answer_all, exp  # noqa: F401
from tiff_tree import write_tiff

IMAGES = (
    "ctrl/rep1/a.tif",
    "ctrl/rep1/b.tif",
    "ctrl/rep2/a.tif",
    "drug/rep1/a.tif",
    "drug/rep1/b.tif",
    "drug/rep2/c.tif",
)


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    for rel in IMAGES:
        write_tiff(root / rel)
    return root


# --- the run reports its own check --------------------------------------------------------------


def test_the_run_reports_each_header_it_checks_again(tmp_path, experiment):
    calls = []
    result = run_described(
        experiment,
        prefill_mapping("YX"),
        output_folder=tmp_path / "out",
        mode=RunMode(checking=lambda *call: calls.append(call)),
    )
    assert result.written
    order = [phase for phase, *_ in calls]
    assert [p for p in PHASES if p in order] == list(PHASES)
    reading = [(done, total, path) for phase, done, total, path in calls if phase == "headers"]
    assert [entry for entry in reading if entry[2]] == [
        (index, len(IMAGES), rel) for index, rel in enumerate(sorted(IMAGES))
    ]
    assert reading[-1] == (len(IMAGES), len(IMAGES), "")


def test_the_check_is_reported_before_any_image_is_read(tmp_path, experiment):
    events = []
    run_described(
        experiment,
        prefill_mapping("YX"),
        output_folder=tmp_path / "out",
        mode=RunMode(checking=lambda phase, *_: events.append(phase)),
        progress=lambda *_: events.append("read"),
    )
    first_read = events.index("read")
    # A fresh output folder verifies content by default: each image is hashed, and said so.
    assert set(events[:first_read]) == {*PHASES, CONTENTS}
    assert set(events[first_read:]) == {"read"}


@pytest.mark.parametrize("verify", [True, False])
def test_each_content_sha256_is_reported_before_the_first_read(tmp_path, experiment, verify):
    calls = []
    run_described(
        experiment,
        prefill_mapping("YX"),
        output_folder=tmp_path / "out",
        mode=RunMode(verify_content=verify, checking=lambda *call: calls.append(call)),
    )
    hashed = [(done, total, path) for phase, done, total, path in calls if phase == CONTENTS]
    if not verify:  # nothing is read whole before the pixels
        assert hashed == []
        return
    assert [entry for entry in hashed if entry[2]] == [
        (index, len(IMAGES), rel) for index, rel in enumerate(sorted(IMAGES))
    ]
    assert hashed[-1] == (len(IMAGES), len(IMAGES), "")


def test_the_outputs_are_the_same_with_and_without_the_check_reported(tmp_path, experiment):
    run_described(experiment, prefill_mapping("YX"), output_folder=tmp_path / "plain")
    run_described(
        experiment,
        prefill_mapping("YX"),
        output_folder=tmp_path / "told",
        mode=RunMode(checking=lambda *_: None),
    )
    for name in ("plane_histograms.csv", "experiment_design.csv"):
        assert (tmp_path / "plain" / name).read_bytes() == (tmp_path / "told" / name).read_bytes()


# --- no step of the check is quadratic --------------------------------------------------------


def test_the_pixel_type_is_taken_once_per_run_not_once_per_image(tmp_path, experiment, monkeypatch):
    calls = []
    real = MetadataReport.dtype.fget

    def counted(self):
        calls.append(1)
        return real(self)

    monkeypatch.setattr(MetadataReport, "dtype", property(counted))
    assert run_described(experiment, prefill_mapping("YX"), output_folder=tmp_path / "out").written
    # A handful of steps take it once each; one per image (the staging keys) made it O(n²).
    assert len(calls) < len(IMAGES)


class _NoScan(dict):
    """A stems mapping that refuses to be scanned: only looked up."""

    def __iter__(self):
        raise AssertionError("every position was compared with this image (quadratic)")

    def keys(self):
        raise AssertionError("every position was compared with this image (quadratic)")


def test_a_mask_owner_is_looked_up_not_scanned_for():
    stems = _NoScan({"f1": ["a/f1.tif"], "f1_x": ["a/f1_x.tif"], "g": ["b/g.tif"]})
    assert _mask_owner("f1_x_mask", stems, "a", {"_mask"}) == "a/f1_x.tif"
    assert _mask_owner("f1_x_mask", stems, "a", None) == "a/f1_x.tif"
    assert _mask_owner("f1_mask", stems, "a", {"_mask"}) == "a/f1.tif"
    assert _mask_owner("f1_dna", stems, "a", {"_mask"}) is None
    assert _mask_owner("g", stems, "b", None) is None


def _brute_owner(stem, stems, folder, suffixes):
    """The owner as the screen found it before v0.9.0's fix: every position compared."""
    keys = [
        key
        for key in stems
        if key != stem
        and stem.startswith(key + "_")
        and (suffixes is None or stem[len(key) :] in suffixes)
    ]
    if not keys:
        return None
    owners = stems[max(keys, key=len)]
    near = [owner for owner in owners if str(PurePosixPath(owner).parent) == folder]
    return (near or owners)[0]


def test_the_looked_up_owner_is_the_one_every_comparison_found():
    rng = random.Random(7)
    names = ["a", "b", "f1", "A"]
    parts = ["a", "b", "f1", "x", "mask", "cells", "", "A"]
    images = sorted(
        {
            rng.choice(["", "c1/", "c2/r1/", "c2/masks/"])  # top-level images too
            + "_".join([rng.choice(names), *(rng.choice(parts) for _ in range(rng.randint(0, 3)))])
            + ".tif"
            for _ in range(400)
        }
    )
    stems = _stems(images)
    for suffixes in (None, {"_mask", "_cells"}):
        for rel in images:
            path = PurePosixPath(rel)
            stem, folder = path.stem.lower(), str(path.parent)
            assert _mask_owner(stem, stems, folder, suffixes) == _brute_owner(
                stem, stems, folder, suffixes
            ), (rel, suffixes)


def test_the_mask_name_screen_finds_the_same_masks(tmp_path):
    from morphobricks_preprocessing.discovery import discover_experiment

    root = tmp_path / "exp"
    for rel in ("c/r/f1.tif", "c/r/f1_mask.tif", "c/r/f1_2.tif", "c/masks/f1.tif", "d/r/g.tif"):
        write_tiff(root / rel)
    issues = {i.code: list(i.paths) for i in mask_name_issues(discover_experiment(root).leaves)}
    assert issues["image_named_as_mask"] == ["c/r/f1_mask.tif"]
    assert "mask_folder_in_experiment" in issues


# --- a wait whose elapsed time goes on --------------------------------------------------------


def test_a_ticking_wait_redraws_its_elapsed_time_and_stops_with_its_block():
    pytest.importorskip("ipywidgets")
    import ipywidgets as widgets

    from morphobricks_preprocessing.notebook_progress import Wait

    target = widgets.HTML()
    seen = []
    target.observe(lambda change: seen.append(change["new"]), names="value")
    now = [100.0]
    wait = Wait(target, bar=False, clock=lambda: now[0])
    wait.start("Preparing")
    with wait.ticking(interval=0.01):
        deadline = time.monotonic() + 5
        while len(seen) < 4 and time.monotonic() < deadline:
            now[0] += 61
            time.sleep(0.01)
    drawn = len(seen)
    target.value = "the result"
    time.sleep(0.05)
    assert drawn >= 4, "the elapsed time was never redrawn"
    assert any("1:01 elapsed" in line or re.search(r"[1-9]:\d\d elapsed", line) for line in seen)
    assert target.value == "the result" and len(seen) == drawn + 1
    assert not [t for t in threading.enumerate() if t.name == "morphobricks-wait-tick"]


# --- the notebook's Run ---------------------------------------------------------------------------


@pytest.mark.usefixtures("legacy_grid")  # the per-unit grid (the guided step has its own)
def test_the_notebook_run_shows_each_header_it_checks_again(exp):  # noqa: F811
    pytest.importorskip("ipywidgets")
    pytest.importorskip("matplotlib")
    from test_notebook_axes_quick import stepper

    nb = stepper(*exp)
    nb.quick_confirm.click()
    answer_all(nb)
    assert nb.save_experiment()
    notes = []
    nb.run_next.observe(lambda change: notes.append(change["new"]), names="value")
    seen = []
    nb.run_status.observe(
        lambda change: seen.append(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", change["new"]))),
        names="value",
    )
    assert nb.run().written
    lines = [line.strip() for line in seen]
    assert lines[0].startswith("Preparing: checking the folders and the image headers again")
    headers = [line for line in lines if line.startswith("Preparing: reading the image headers")]
    assert any(
        re.match(r"Preparing: reading the image headers again · 0 of \d+ .*elapsed", line)
        for line in headers
    ), lines[:5]
    first_read = next(i for i, line in enumerate(lines) if line.startswith("Reading images"))
    assert any(line.startswith("Preparing: comparing the images") for line in lines[:first_read])
    assert any(
        re.match(r"Preparing: reading each image whole for its content SHA-256 · 0 of \d+", line)
        for line in lines[:first_read]
    ), lines
    assert "elapsed" not in lines[-1]  # the result replaced the wait, and stayed
    # While it goes, the run says that a cell run meanwhile waits for it; then that goes.
    assert "A cell run meanwhile (this one too) waits for it" in notes[0]
    assert "waits for it" not in nb.run_next.value


def test_the_run_wait_counts_real_totals_and_ends_at_n_of_n():
    pytest.importorskip("ipywidgets")
    from types import SimpleNamespace

    import ipywidgets as widgets

    from morphobricks_preprocessing.notebook_progress import Wait
    from morphobricks_preprocessing.notebook_run import _RunStep

    target = widgets.HTML()
    seen = []
    target.observe(
        lambda change: seen.append(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", change["new"]))),
        names="value",
    )
    now = [0.0]
    step = SimpleNamespace(_run_wait=Wait(target, bar=False, clock=lambda: now[0]))
    step._run_wait.start("Preparing")
    # What preflight reports for 3 images and no masks (its placeholders included).
    calls = [
        ("folders", 0, 1, ""),
        ("folders", 1, 1, ""),
        ("headers", 0, 3, ""),
        *(("headers", i, 3, f"c/{i}.tif") for i in range(3)),
        ("headers", 3, 3, ""),
        ("masks", 0, 1, ""),
        ("masks", 1, 1, ""),
        ("acquisitions", 0, 1, ""),
        ("acquisitions", 1, 1, ""),
        ("contents", 0, 3, "c/0.tif"),
        ("contents", 3, 3, ""),
    ]
    for call in calls:
        now[0] += 1  # past the redraw interval
        _RunStep._checking(step, *call)
    lines = [line.strip() for line in seen]
    assert any(
        line.startswith("Preparing: reading the image headers again · 3 of 3") for line in lines
    )
    masks = [line for line in lines if "mask headers" in line]
    assert masks and not any(" of " in line for line in masks), masks  # no masks: no "0 of 1"
    assert lines[-1].startswith(
        "Preparing: reading each image whole for its content SHA-256 · 3 of 3"
    )
