"""Benchmark a whole preprocessing run, serial and with image-level workers (#106, #107).

Usage::

    python tools/benchmark_run_workers.py [--folder DIR] [--workers 1 2 4 auto]
        [--repeats 3] [--scale 1] [--memory-limit-mib 64] [--max-ram-mib 0] [--phases]

It writes (once, into ``--folder``, by default a new temporary folder) a fixed synthetic
experiment of seeded ``uint16`` images in two conditions: tiled zlib OME-TIFFs and OME-Zarr
(OME-NGFF 0.4) stores, small ones whose planes are read whole and large ones whose planes are
over ``--memory-limit-mib`` and are read in blocks, and publishes its experiment description.
Then, for each ``--workers`` value in turn (``--repeats`` rounds, the values in the same order
each round, after one serial warm-up run that is not counted), it runs preprocessing into a
new output folder, each run in a fresh Python process, and records the wall time of
``run_experiment`` and the process's peak resident memory (``ru_maxrss``).

It prints, as JSON: the dataset (images, which are read in blocks, bytes on disk), the
machine, and for each worker value the median and every wall time, the largest peak RSS, and
the SHA-256 of ``plane_histograms.csv``, of ``experiment_design.csv`` and of the manifest
without its ``written_at`` time. Every run must give the same three digests as the serial one,
else the script exits with status 1: no performance claim without identical outputs. On a
version without ``RunMode(workers=...)`` only ``1`` can be run (the baseline). Timings are
indicative: they depend on the computer, its disk cache and its load.

With ``--phases`` each run also times, in the calling thread only, the steps of the run that
are not the workers' (:data:`PHASES`: the header checks, the content SHA-256 passes, staging,
the table, the intensity checks...) and the report gives each worker value's median per step:
where the serial time of a run goes (D7). A step a version does not have is left out, and the
wrappers time the calling thread only, so a step a worker does is not counted.
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import hashlib
import importlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import tifffile

import morphobricks_preprocessing
from morphobricks_preprocessing import prefill_mapping, run_experiment
from morphobricks_preprocessing.description import (
    Channel,
    Condition,
    Description,
    Member,
    Repeat,
    Unit,
)
from morphobricks_preprocessing.run import RunMode
from morphobricks_preprocessing.testing_described import PUBLISHED_NAME, description_of

MIB = 2**20
CONDITIONS = ("ctrl", "drug")
CHANNELS = ("ch1", "ch2")
SMALL = (3, 2, 1024, 1024)
"""T, C, Y, X of an image whose planes are read whole (under the default 64 MiB limit)."""
LARGE = (2, 2, 3072, 3072)
"""T, C, Y, X of an image whose planes are read in blocks (over the default 64 MiB limit)."""
IMAGES = (
    ("s1.ome.tif", SMALL),
    ("s2.ome.tif", SMALL),
    ("s3.ome.tif", SMALL),
    ("L1.ome.tif", LARGE),
    ("s4.ome.zarr", SMALL),
    ("s5.ome.zarr", SMALL),
    ("L2.ome.zarr", LARGE),
)
"""Every condition's images: OME-TIFF and OME-Zarr, read whole and in blocks."""
PHASES = (
    ("run.pipeline", "preflight", "preflight"),
    ("run.pipeline", "run_checks", "header checks"),
    ("run.steps", "with_content", "content SHA-256 before the reads"),
    ("run.steps", "new_rows", "resume and read (all)"),
    ("run.resume", "_read_checked", "reads in the calling thread"),
    ("run.resume", "stage_file", "staging in the calling thread"),
    ("run.pipeline", "_pixel_issues", "pixel checks"),
    ("run.pipeline", "_range_issues", "range checks"),
    ("run.pipeline", "check_intensities", "intensity checks"),
    ("run.pipeline", "write_plane_histograms", "table"),
    ("run.pipeline", "write_table", "table"),
    ("run.pipeline", "_write_generation", "table, design and manifest"),
    ("run.pipeline", "content_changed", "content SHA-256 before the commit"),
    ("run.pipeline", "_publish", "publication (all)"),
)
"""``(module of the package, function, step)`` the calling thread's time is taken of."""


def pixels(shape: tuple[int, ...], seed: int, scale: int) -> np.ndarray:
    """Return seeded ``uint16`` pixels: smooth gradients with noise, never constant."""
    shape = (*shape[:2], shape[2] * scale, shape[3] * scale)
    rng = np.random.default_rng(seed)
    y, x = np.ogrid[: shape[2], : shape[3]]
    data = np.empty(shape, dtype=np.uint16)
    for t in range(shape[0]):
        for c in range(shape[1]):
            base = ((y * (c + 1) + x * (t + 2)) // 8 % 3000).astype(np.uint16)
            data[t, c] = base + rng.integers(0, 500, shape[2:], dtype=np.uint16)
    return data


def write_zarr(path: Path, data: np.ndarray) -> None:
    """Write ``data`` (TCYX) as a one-level OME-NGFF 0.4 image in 1024 px chunks."""
    import zarr

    major = int(zarr.__version__.split(".")[0])
    group = (
        zarr.open_group(str(path), mode="w", zarr_format=2)
        if major >= 3
        else zarr.open_group(str(path), mode="w")
    )
    chunks = (1, 1, min(1024, data.shape[2]), min(1024, data.shape[3]))
    create = group.create_array if major >= 3 else group.create_dataset
    array = create("0", shape=data.shape, chunks=chunks, dtype=data.dtype)
    array[...] = data
    names = (("t", "time"), ("c", "channel"), ("y", "space"), ("x", "space"))
    group.attrs.update(
        {
            "multiscales": [
                {
                    "version": "0.4",
                    "axes": [{"name": n, "type": t} for n, t in names],
                    "datasets": [
                        {
                            "path": "0",
                            "coordinateTransformations": [{"type": "scale", "scale": [1.0] * 4}],
                        }
                    ],
                }
            ]
        }
    )


def write_dataset(folder: Path, scale: int) -> Path:
    """Write the experiment and its description once; return the experiment folder."""
    experiment = folder / "experiment"
    done = folder / "dataset.done"
    if done.exists():
        return experiment
    shutil.rmtree(experiment, ignore_errors=True)
    seed = 0
    for condition in CONDITIONS:
        for name, shape in IMAGES:
            seed += 1
            path = experiment / condition / "rep1" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            data = pixels(shape, seed, scale)
            if name.endswith(".zarr"):
                write_zarr(path, data)
            else:
                tifffile.imwrite(
                    path,
                    data,
                    ome=True,
                    photometric="minisblack",
                    metadata={"axes": "TCYX"},
                    tile=(256, 256),
                    compression="zlib",
                )
    unit = {"single": Unit("single", CHANNELS)}
    members = {c: Member("rep1", len(IMAGES), unit) for c in CONDITIONS}
    described = Description(
        conditions={c: Condition(c) for c in CONDITIONS},
        channels={c: Channel() for c in CHANNELS},
        repeats={"rep1": Repeat(True, "2026-01-01", members)},
        used_ids={
            "conditions": CONDITIONS,
            "channels": CHANNELS,
            "repeats": ("rep1",),
            "technical_repeats": ("single",),
        },
    )
    (folder / PUBLISHED_NAME).write_bytes(description_of(described)[1])
    done.write_text("ok\n", encoding="utf-8")
    return experiment


def time_phases() -> dict[str, float]:
    """Wrap every function of :data:`PHASES` this version has; return the seconds per step.

    Only calls from the calling thread are timed (a worker's own calls are not). Each function
    is replaced in the module that calls it, so the wrapper sees every call.
    """
    seconds: dict[str, float] = {}
    for module_name, name, step in PHASES:
        module = importlib.import_module(f"morphobricks_preprocessing.{module_name}")
        function = getattr(module, name, None)
        if function is not None:
            seconds.setdefault(step, 0.0)
            setattr(module, name, _timed(function, step, seconds))
    return seconds


def _timed(function: Callable, step: str, seconds: dict[str, float]) -> Callable:
    """Return ``function`` adding the time of its calls from the calling thread to ``step``."""
    caller = threading.main_thread()

    @functools.wraps(function)
    def timed(*args: object, **kwargs: object) -> object:
        if threading.current_thread() is not caller:
            return function(*args, **kwargs)
        start = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            seconds[step] += time.perf_counter() - start

    return timed


def child(
    experiment: Path, output: Path, workers: str, limits: tuple[int, int], phases: bool
) -> None:
    """Run preprocessing once in this process and print its wall time and peak RSS as JSON.

    ``limits`` are the memory limit and ``max_ram`` (0: none); ``phases`` times the steps.
    """
    import resource

    seconds = time_phases() if phases else {}
    memory_limit, max_ram = limits
    options: dict = {"memory_limit": memory_limit}
    if workers != "1":
        count = workers if workers == "auto" else int(workers)
        options["mode"] = RunMode("restart", workers=count, max_ram=max_ram or None)
    start = time.perf_counter()
    result = run_experiment(experiment, prefill_mapping("TCYX"), output_folder=output, **options)
    wall = time.perf_counter() - start
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024  # kilobytes on Linux
    found = {
        "written": result.written,
        "issues": [f"{issue.code}: {issue.message}" for issue in result.issues if issue.is_error],
        "read": len(result.read),
        "workers": getattr(result, "workers", 1),
        "wall_seconds": wall,
        "peak_rss_bytes": peak,
        "phases": {step: round(value, 3) for step, value in seconds.items()},
    }
    print(json.dumps(found))


def digests(output: Path) -> dict[str, str]:
    """Return the SHA-256 of the table, the design and the manifest without ``written_at``."""
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    manifest.pop("written_at", None)
    canonical = json.dumps(manifest, sort_keys=True).encode("utf-8")
    return {
        "plane_histograms.csv": _sha256((output / "plane_histograms.csv").read_bytes()),
        "experiment_design.csv": _sha256((output / "experiment_design.csv").read_bytes()),
        "run_manifest.json (without written_at)": _sha256(canonical),
    }


def _sha256(data: bytes) -> str:
    """Return the hexadecimal SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def one_run(folder: Path, experiment: Path, workers: str, args: argparse.Namespace) -> dict:
    """Run preprocessing in a fresh process into a new output folder; return what it gave."""
    output = folder / f"output_{workers}"
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir()
    shutil.copy(folder / PUBLISHED_NAME, output / PUBLISHED_NAME)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--child",
        str(experiment),
        str(output),
        workers,
        str(args.memory_limit_mib * MIB),
        str(args.max_ram_mib * MIB),
        "1" if args.phases else "0",
    ]
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise SystemExit(f"workers={workers} failed:\n{done.stderr}")
    found = json.loads(done.stdout.strip().splitlines()[-1])
    if not found["written"]:
        raise SystemExit(f"workers={workers} published nothing: {found['issues']}")
    found["digests"] = digests(output)
    found["read_in_blocks"] = _in_blocks(output)
    return found


def _in_blocks(output: Path) -> list[str]:
    """Return the images the run read in blocks, from its manifest."""
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    return sorted(f["relative_path"] for f in manifest["files"] if f.get("read_in_blocks"))


def machine() -> dict:
    """Return a description of this computer and the versions the run used."""
    import zarr

    return {
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "cpus": os.cpu_count(),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "tifffile": tifffile.__version__,
        "zarr": zarr.__version__,
        "morphobricks_preprocessing": morphobricks_preprocessing.__version__,
    }


def summary(runs: dict[str, list[dict]]) -> tuple[dict, bool]:
    """Return each worker value's times, peak RSS and digests, and whether all digests agree."""
    reference = runs["1"][0]["digests"]
    same = all(run["digests"] == reference for found in runs.values() for run in found)
    table = {
        workers: {
            "workers_used": found[0]["workers"],
            "median_wall_seconds": round(statistics.median(r["wall_seconds"] for r in found), 2),
            "wall_seconds": [round(r["wall_seconds"], 2) for r in found],
            "peak_rss_mib": round(max(r["peak_rss_bytes"] for r in found) / MIB, 1),
            "digests": found[0]["digests"],
            "digests_equal_to_serial": all(r["digests"] == reference for r in found),
            **_phase_medians(found),
        }
        for workers, found in runs.items()
    }
    return table, same


def _phase_medians(found: list[dict]) -> dict:
    """Return ``{"phases_median_seconds": {step: median}}`` of runs timed by step, else {}."""
    steps = [step for step in found[0].get("phases", {})]
    if not steps:
        return {}
    medians = {s: round(statistics.median(r["phases"][s] for r in found), 3) for s in steps}
    return {"phases_median_seconds": medians}


def main() -> None:
    """Parse the arguments, write the dataset if needed, run every configuration and report."""
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        experiment, output, workers, limit, max_ram, phases = sys.argv[2:8]
        limits = (int(limit), int(max_ram))
        child(Path(experiment), Path(output), workers, limits, phases == "1")
        return
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--folder", type=Path, default=None)
    parser.add_argument("--workers", nargs="+", default=["1", "2", "4", "auto"])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--scale", type=int, default=1, help="multiply each side by this")
    parser.add_argument("--memory-limit-mib", type=int, default=64)
    parser.add_argument("--max-ram-mib", type=int, default=0, help="with auto; 0: none")
    parser.add_argument("--phases", action="store_true", help="time the calling thread's steps")
    args = parser.parse_args()
    if "1" not in args.workers:
        args.workers.insert(0, "1")
    has_workers = "workers" in {f.name for f in dataclasses.fields(RunMode)}
    if not has_workers and args.workers != ["1"]:
        raise SystemExit("This version has no workers option: run with --workers 1 only.")
    folder = args.folder or Path(tempfile.mkdtemp(prefix="run_workers_"))
    experiment = write_dataset(folder, args.scale)
    one_run(folder, experiment, "1", args)  # warm-up: the disk cache, not counted
    runs: dict[str, list[dict]] = {workers: [] for workers in args.workers}
    for _ in range(args.repeats):
        for workers in args.workers:
            runs[workers].append(one_run(folder, experiment, workers, args))
    table, same = summary(runs)
    files = sorted(p for p in experiment.rglob("*") if p.is_file())
    report = {
        "folder": str(folder),
        "machine": machine(),
        "dataset": {
            "images": 2 * len(IMAGES),
            "read_in_blocks": runs["1"][0]["read_in_blocks"],
            "bytes_on_disk": sum(p.stat().st_size for p in files),
            "scale": args.scale,
            "memory_limit_mib": args.memory_limit_mib,
        },
        "repeats": args.repeats,
        "runs": table,
        "outputs_identical": same,
    }
    print(json.dumps(report, indent=2))
    if not same:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
