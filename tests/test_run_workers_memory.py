"""The workers' memory plan counts the staging each worker does (D7, :mod:`.run.workers`).

A worker stages its image's rows (the writer holds the table's header and one row as Python
strings), so each worker's estimate holds one row's staging with the table's channels; the
estimate bounds what the writer allocates, and ``"auto"`` plans and refuses with it.
"""

import tracemalloc

import numpy as np
import pytest
from morphobricks_normalization.csv_io import table_header, write_plane_histograms
from morphobricks_normalization.histogram import compute_plane_histogram
from morphobricks_normalization.records import PlaneRecord

from described_tree import run_described
from morphobricks_preprocessing import prefill_mapping, read_manifest
from morphobricks_preprocessing import run as run_module
from morphobricks_preprocessing.manifest import RunSettings
from morphobricks_preprocessing.run import RunMode
from morphobricks_preprocessing.run import workers as workers_module
from morphobricks_preprocessing.run.workers import (
    PARENT_RESERVE,
    Workers,
    image_memory,
    plan_workers,
    staging_memory,
)
from tiff_tree import write_data

TYX = prefill_mapping("TYX")
IMAGES = [f"{c}/r{r}/{n}.tif" for c in ("ctrl", "drug") for r in (1, 2) for n in ("a", "b")]


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    for seed, rel in enumerate(IMAGES):
        pixels = np.random.default_rng(seed).integers(0, 255, (2, 8, 8), dtype=np.uint8)
        write_data(root / rel, pixels, "TYX")
    return root


def run_with(root, out, workers):
    mode = RunMode("restart", verify_content=True, workers=workers)
    result = run_described(root, TYX, output_folder=out, mode=mode)
    assert result.written, result.issues
    return result


@pytest.mark.parametrize("channels", [1, 3])
def test_staging_memory_bounds_the_writers_peak_for_one_row(tmp_path, channels):
    """The estimate is at least what the writer allocates to write one row (tracemalloc)."""
    ids = tuple(f"C{i}" for i in range(channels))
    pixels = np.random.default_rng(0).integers(0, 65535, (64, 64), dtype=np.uint16)
    record = PlaneRecord(
        "a/r/x.tif",
        "a",
        "r",
        "single",
        histograms=(compute_plane_histogram(pixels),),
        z=None,
        t=0,
        position_id="x",
        channels=ids[:1],
    )
    bins = record.histograms[0].bin_count
    assert bins == 65536
    write_plane_histograms([record], tmp_path / "warm.csv", channel_ids=ids)
    tracemalloc.start()
    try:
        write_plane_histograms([record], tmp_path / "row.csv", channel_ids=ids)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    columns = len(table_header(ids, bins))
    assert staging_memory(bins, channels) == workers_module.STAGING_BYTES_PER_COLUMN * columns
    assert peak <= staging_memory(bins, channels)


def test_auto_counts_the_staging_of_the_tables_channels(experiment, monkeypatch):
    """More table channels, more staging per worker: fewer workers, or the refusal."""
    settings = RunSettings(256, 2**30)
    monkeypatch.setattr(workers_module, "available_cpus", lambda: 8)
    names, mappings = list(IMAGES), dict.fromkeys(IMAGES, TYX)
    own = max(image_memory(experiment / rel, TYX, settings) for rel in names)
    wide = max(image_memory(experiment / rel, TYX, settings, 6) for rel in names)
    assert wide - own == staging_memory(256, 6) - staging_memory(256, 1) > 0
    max_ram = PARENT_RESERVE + 3 * own
    plan = plan_workers(Workers("auto", max_ram), experiment, names, mappings, settings)
    assert plan.count == 3
    plan = plan_workers(Workers("auto", max_ram), experiment, names, mappings, settings, 6)
    assert plan.count == (max_ram - PARENT_RESERVE) // wide < 3
    refused = plan_workers(
        Workers("auto", PARENT_RESERVE + own), experiment, names, mappings, settings, 6
    )
    assert refused.refused is not None and refused.refused.code == "image_over_max_ram"
    assert "the staging of one row" in refused.refused.message


def test_the_run_plans_its_workers_with_the_tables_channels(tmp_path, experiment, monkeypatch):
    planned = []
    plan = run_module.resume.plan_workers

    def recording(*args):
        planned.append(args[-1])
        return plan(*args)

    monkeypatch.setattr(run_module.resume, "plan_workers", recording)
    result = run_with(experiment, tmp_path / "out", "auto")
    manifest = read_manifest(result.manifest_path)
    channels = {c for entry in manifest.files for c in entry.channel_ids}
    assert planned == [len(channels)] and len(channels) >= 1
