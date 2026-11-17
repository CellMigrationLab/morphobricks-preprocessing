"""``max_ram`` is a budget every worker count keeps, refused before any pixel is read (B1).

Before v0.7.1 :func:`.run.workers.plan_workers` returned a whole number of workers as it was,
even with ``max_ram``: only ``"auto"`` was planned from the headers (``RunMode`` refused
``max_ram`` with a whole number, so the gap was in the planner, not yet reachable). Now, with
``max_ram``, every count is planned from the header estimates: a whole number whose images at
once do not fit is refused with ``workers_over_max_ram`` (never lowered for the user), one
image over the budget with ``image_over_max_ram``; ``"auto"`` still takes what fits. Without
``max_ram`` there is no budget at all (not a ceiling of any kind).

Every refusal is checked with the pixel reader instrumented: the run's reads and tifffile's
page reads are counted, and none happens.
"""

import numpy as np
import pytest
import tifffile

from described_tree import main_described, run_described
from morphobricks_preprocessing import prefill_mapping
from morphobricks_preprocessing import run as run_module
from morphobricks_preprocessing.manifest import RunSettings
from morphobricks_preprocessing.run import RunMode
from morphobricks_preprocessing.run import workers as workers_module
from morphobricks_preprocessing.run.workers import (
    OVER_MAX_RAM,
    PARENT_RESERVE,
    WORKERS_OVER_MAX_RAM,
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
    """Count every pixel read: the run's per-image read and tifffile's page reads."""
    reads = []
    read_file = run_module.resume._read_file
    page_read = tifffile.TiffPage.asarray

    def counted_file(path, rel, *args):
        reads.append(rel)
        return read_file(path, rel, *args)

    def counted_page(self, *args, **kwargs):
        reads.append("page")
        return page_read(self, *args, **kwargs)

    monkeypatch.setattr(run_module.resume, "_read_file", counted_file)
    monkeypatch.setattr(tifffile.TiffPage, "asarray", counted_page)
    monkeypatch.setattr(workers_module, "available_cpus", lambda: 8)
    return reads


def largest(experiment):
    return max(image_memory(experiment / rel, TYX, SETTINGS) for rel in IMAGES)


def mode(workers, max_ram):
    return RunMode("restart", verify_content=True, workers=workers, max_ram=max_ram)


def test_a_whole_number_of_workers_is_planned_from_the_headers_with_max_ram(experiment):
    """The defect: ``Workers(4, max_ram)`` was returned as 4 whatever the budget."""
    names, mappings, each = list(IMAGES), dict.fromkeys(IMAGES, TYX), largest(experiment)
    short = PARENT_RESERVE + 4 * each - 1
    plan = plan_workers(Workers(4, short), experiment, names, mappings, SETTINGS)
    assert plan.count == 0 and plan.refused is not None
    assert plan.refused.code == WORKERS_OVER_MAX_RAM
    enough = PARENT_RESERVE + 4 * each
    assert plan_workers(Workers(4, enough), experiment, names, mappings, SETTINGS) == WorkerPlan(4)
    # No budget: the count is used as it is, at any size.
    assert plan_workers(Workers(64), experiment, names, mappings, SETTINGS) == WorkerPlan(64)


def test_only_the_images_read_at_once_count(experiment):
    """Eight workers for four images read four at once: four images' memory is the need."""
    names, mappings, each = list(IMAGES), dict.fromkeys(IMAGES, TYX), largest(experiment)
    plan = plan_workers(
        Workers(8, PARENT_RESERVE + 4 * each), experiment, names, mappings, SETTINGS
    )
    assert plan == WorkerPlan(8)


@pytest.mark.parametrize("workers", [1, 2, 4, "auto"])
def test_max_ram_just_below_the_estimate_is_refused_before_any_pixel_read(
    tmp_path, experiment, pixel_reads, workers
):
    each = largest(experiment)
    at_once = 1 if workers == "auto" else workers
    need = PARENT_RESERVE + at_once * each
    out = tmp_path / "out"
    result = run_described(experiment, TYX, output_folder=out, mode=mode(workers, need - 1))
    assert not result.written and result.read == () and result.workers == 1
    assert pixel_reads == []
    (found,) = [issue for issue in result.issues if issue.is_error]
    if at_once == 1:  # one image alone does not fit: no count would
        assert found.code == OVER_MAX_RAM and found.paths == tuple(IMAGES)
        assert "max_ram" in found.message
        return
    assert found.code == WORKERS_OVER_MAX_RAM
    assert workers_module.format_bytes(need) in found.message
    assert f"workers={workers}" in found.message and "No image was read" in found.message
    assert f"at most {at_once - 1} " in found.action and "raise max_ram" in found.action
    assert workers_module.format_bytes(need) in found.action
    assert not (out / "plane_histograms.csv").exists()


@pytest.mark.parametrize("workers", [1, 2, 4, "auto"])
def test_max_ram_at_or_above_the_estimate_reads_with_the_count_given(
    tmp_path, experiment, pixel_reads, workers
):
    each = largest(experiment)
    at_once = 1 if workers == "auto" else workers
    need = PARENT_RESERVE + at_once * each
    for extra, name in ((0, "at"), (1, "above")):
        result = run_described(
            experiment, TYX, output_folder=tmp_path / name, mode=mode(workers, need + extra)
        )
        assert result.written, result.issues
        assert result.read == tuple(IMAGES)
        assert result.workers == at_once  # auto: what the budget fits; a number: as given
    assert sorted(r for r in pixel_reads if r != "page") == sorted(IMAGES * 2)
    assert "page" in pixel_reads  # the instrument does see the pixel reads


def test_auto_still_takes_what_fits_and_never_refuses_for_the_count(
    tmp_path, experiment, pixel_reads
):
    each = largest(experiment)
    max_ram = PARENT_RESERVE + 3 * each - 1  # two images fit, not three
    result = run_described(
        experiment, TYX, output_folder=tmp_path / "out", mode=mode("auto", max_ram)
    )
    assert result.written and result.workers == 2


def test_resumed_images_are_not_counted(tmp_path, experiment, pixel_reads, monkeypatch):
    """Only the images the run reads are planned: the rest are read from the staging."""
    out = tmp_path / "out"
    first = run_described(experiment, TYX, output_folder=out, mode=mode(1, None))
    assert first.written
    pixel_reads.clear()
    again = run_described(experiment, TYX, output_folder=out, mode=mode(4, PARENT_RESERVE + 1))
    assert again.written and again.read == () and pixel_reads == []


def test_the_command_line_takes_max_ram_with_any_workers(tmp_path, experiment, capsys):
    argv = ["run", experiment, "--axes", "TYX", "--output", tmp_path / "out"]
    short = str(PARENT_RESERVE + 4 * largest(experiment) - 1)  # in bytes
    assert main_described([*argv, "--workers", "4", "--max-ram", short]) == 1
    printed = capsys.readouterr().out
    assert "workers=4 reads up to 4 images at once" in printed and "No image was read" in printed
    assert "Use fewer workers (at most 3 fit in this max_ram)" in printed
    assert not (tmp_path / "out" / "plane_histograms.csv").exists()
    assert main_described([*argv, "--workers", "2", "--max-ram", "1GiB"]) == 0
    assert "Read with 2 workers (one image each)" in capsys.readouterr().out
