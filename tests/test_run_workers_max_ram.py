"""The worker planner and ``max_ram`` (plan §12 item 82: ``max_ram`` only with ``"auto"``).

At v0.7.0 :func:`.run.workers.plan_workers` returned a whole number of workers as it was even
with ``max_ram``, so the budget was not kept. ``RunMode`` and the command line refuse that
combination before the planner is reached, so it is guarded, not reachable: the planner now
refuses it with the same ``ValueError``. With ``"auto"``, an image over the budget stops the
run before any pixel is read: the run's reads and tifffile's page reads are counted.
"""

import numpy as np
import pytest
import tifffile

from described_tree import run_described
from morphobricks_preprocessing import prefill_mapping
from morphobricks_preprocessing import run as run_module
from morphobricks_preprocessing.manifest import RunSettings
from morphobricks_preprocessing.run import RunMode
from morphobricks_preprocessing.run import workers as workers_module
from morphobricks_preprocessing.run.modes import MAX_RAM_ONLY_WITH_AUTO
from morphobricks_preprocessing.run.workers import (
    OVER_MAX_RAM,
    PARENT_RESERVE,
    WorkerPlan,
    Workers,
    image_memory,
    plan_workers,
)
from tiff_tree import write_data

TYX = prefill_mapping("TYX")
IMAGES = [f"{c}/r1/{n}.tif" for c in ("ctrl", "drug") for n in ("a", "b")]
SETTINGS = RunSettings(256, 2**30)


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    for seed, rel in enumerate(IMAGES):
        pixels = np.random.default_rng(seed).integers(0, 255, (2, 8, 8), dtype=np.uint8)
        write_data(root / rel, pixels, "TYX")
    return root


@pytest.fixture
def pixel_reads(monkeypatch):
    """Count every pixel read: the run's per-image read and tifffile's two routes to pixels.

    A page's own read (``TiffPage.asarray``) and a contiguous series' read of the file
    (``FileHandle.read_array``, as :mod:`..tiff_pieces` and ``tifffile.imread`` use it). The
    images here are TIFFs only, so no OME-Zarr read can happen.
    """
    reads = []
    read_file = run_module.resume._read_file
    page_read = tifffile.TiffPage.asarray
    array_read = tifffile.FileHandle.read_array

    def counted_file(path, rel, *args):
        reads.append(rel)
        return read_file(path, rel, *args)

    def counted_page(self, *args, **kwargs):
        reads.append("page")
        return page_read(self, *args, **kwargs)

    def counted_array(self, *args, **kwargs):
        reads.append("array")
        return array_read(self, *args, **kwargs)

    monkeypatch.setattr(run_module.resume, "_read_file", counted_file)
    monkeypatch.setattr(tifffile.TiffPage, "asarray", counted_page)
    monkeypatch.setattr(tifffile.FileHandle, "read_array", counted_array)
    monkeypatch.setattr(workers_module, "available_cpus", lambda: 8)
    return reads


def largest(experiment):
    return max(image_memory(experiment / rel, TYX, SETTINGS) for rel in IMAGES)


@pytest.mark.parametrize("count", [1, 2, 4])
def test_the_planner_refuses_a_whole_number_of_workers_with_max_ram(experiment, count):
    """v0.7.0 returned ``WorkerPlan(count)``: the budget was silently not kept."""
    names, mappings = list(IMAGES), dict.fromkeys(IMAGES, TYX)
    for max_ram in (PARENT_RESERVE + 1, 2**40):  # under and over the estimate alike
        with pytest.raises(ValueError, match=MAX_RAM_ONLY_WITH_AUTO):
            plan_workers(Workers(count, max_ram), experiment, names, mappings, SETTINGS)
    assert plan_workers(Workers(count), experiment, names, mappings, SETTINGS) == WorkerPlan(count)


def test_the_planner_and_run_mode_refuse_with_the_same_message(tmp_path, experiment):
    mode = RunMode("restart", workers=4, max_ram=2**30)
    with pytest.raises(ValueError) as public:
        run_described(experiment, TYX, output_folder=tmp_path / "out", mode=mode)
    with pytest.raises(ValueError) as planner:
        plan_workers(Workers(4, 2**30), experiment, list(IMAGES), {}, SETTINGS)
    assert str(public.value) == str(planner.value) == "max_ram is used only with workers='auto'"


def _auto(tmp_path, experiment, name, max_ram):
    mode = RunMode("restart", verify_content=True, workers="auto", max_ram=max_ram)
    return run_described(experiment, TYX, output_folder=tmp_path / name, mode=mode)


def test_auto_just_below_one_image_is_refused_before_any_pixel_read(
    tmp_path, experiment, pixel_reads
):
    result = _auto(tmp_path, experiment, "out", PARENT_RESERVE + largest(experiment) - 1)
    assert not result.written and result.read == () and result.workers == 1
    assert pixel_reads == []
    (found,) = [issue for issue in result.issues if issue.is_error]
    assert found.code == OVER_MAX_RAM and found.paths == tuple(IMAGES)
    assert not (tmp_path / "out" / "plane_histograms.csv").exists()


def test_auto_at_or_above_the_estimate_reads(tmp_path, experiment, pixel_reads):
    each = largest(experiment)
    for name, max_ram, together in (
        ("at", PARENT_RESERVE + each, 1),
        ("above", PARENT_RESERVE + each + 1, 1),
        ("two", PARENT_RESERVE + 3 * each - 1, 2),  # two fit, not three
    ):
        result = _auto(tmp_path, experiment, name, max_ram)
        assert result.written, result.issues
        assert result.read == tuple(IMAGES) and result.workers == together
    assert sorted(r for r in pixel_reads if r not in ("page", "array")) == sorted(IMAGES * 3)
    assert {"page", "array"} & set(pixel_reads)  # the instrument does see the pixel reads


def test_a_pixel_read_before_the_refusal_is_counted(tmp_path, experiment, pixel_reads, monkeypatch):
    """The counter's own check: a mutation reading an image with ``tifffile.imread`` before
    the plan (as a run that read before refusing would) is seen, though the run is refused."""
    plan = run_module.resume.plan_workers

    def reading_first(workers, root, to_read, *args):
        tifffile.imread(root / to_read[0])
        return plan(workers, root, to_read, *args)

    monkeypatch.setattr(run_module.resume, "plan_workers", reading_first)
    result = _auto(tmp_path, experiment, "out", PARENT_RESERVE + largest(experiment) - 1)
    assert not result.written and result.read == ()
    assert pixel_reads and set(pixel_reads) <= {"page", "array"}
