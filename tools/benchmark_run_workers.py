"""Benchmark a whole preprocessing run, serial and with image-level workers (#106, #107).

Usage::

    python tools/benchmark_run_workers.py [--folder DIR] [--workers 1 2 4 auto]
        [--repeats N] [--quick] [--seed 0] [--order balanced|shuffled|fixed] [--cpus 0-3]
        [--scale 1] [--memory-limit-mib 64] [--max-ram-mib 0] [--phases]

It writes (once, into ``--folder``, by default a new temporary folder) a fixed synthetic
experiment of seeded ``uint16`` images in two conditions: tiled zlib OME-TIFFs and OME-Zarr
(OME-NGFF 0.4) stores, small ones whose planes are read whole and large ones whose planes are
over ``--memory-limit-mib`` and are read in blocks, and publishes its experiment description.
After one serial warm-up run that is not counted, it runs ``--repeats`` rounds; each round
runs every ``--workers`` value once, each run in a fresh Python process into a new output
folder, and records the wall time of ``run_experiment``, the process's peak resident memory
(``ru_maxrss``), the load average before and after, and the output digests.

Protocol (acceptance mode, the default)
---------------------------------------
- **Repeats**: at least 10 rounds (``--repeats``, default 10; fewer is refused). ``--quick``
  is the smoke mode: 3 rounds by default, any number allowed, and the report says
  ``"mode": "quick"``: never quote a quick run as a result.
- **Order**: counterbalanced and seeded (``--order balanced``, the default): the rounds go in
  blocks of as many rounds as configurations; each block draws a permutation from
  ``random.Random(--seed)`` and rotates it by one place per round, so within a block every
  configuration runs once in every position (first, second... last) and no configuration is
  always favoured by the disk cache or thermal drift. ``--order shuffled`` draws every round's
  order independently; ``--order fixed`` keeps the order given, for comparison only. The order
  of every round is recorded in the report (``rounds[].order``), with the seed. Use a number
  of rounds that is a multiple of the configurations for a complete balance.
- **Paired comparison**: every round holds one run of each configuration, so each worker
  value is compared with the serial run *of the same round* (``paired_speedup``: per round
  ``serial wall / wall``, and their median, min and max). Report the paired median with its
  spread, not a ratio of two independent medians.
- **CPU pinning**: ``--cpus LIST`` (e.g. ``0-3``) runs every child under
  ``taskset -c LIST`` where ``taskset`` exists (Linux, util-linux); elsewhere the report says
  ``"pinning": "unavailable"`` and the runs are not pinned. Pin to CPUs no other job uses, and
  give ``auto`` the same set (it counts the CPUs this process may use).
- **Load**: ``os.getloadavg()`` is recorded before and after every run (``null`` where the
  system has none) and summarised; a run on a loaded machine is not a result. Close other
  jobs, and quote the load with the numbers.
- **Memory**: every run's peak RSS is kept; each configuration reports the distribution
  (min, median, max, every value) and the largest of all.
- **Identical outputs**: each run's digests (SHA-256 of ``plane_histograms.csv``, of
  ``experiment_design.csv`` and of the manifest without its ``written_at`` time) are printed
  as the run finishes (standard error) and kept in the report; every run must give the serial
  digests, else the script exits with status 1: no performance claim without identical
  outputs.

It prints, as JSON: the dataset (images, which are read in blocks, bytes on disk), the
machine, the protocol (mode, repeats, seed, order, pinning), every run in the order it ran,
and for each worker value its wall times, peak RSS distribution, paired speed-up, load and
digests. On a version without ``RunMode(workers=...)`` only ``1`` can be run (the baseline).
Timings depend on the computer, its disk cache and its load.

With ``--phases`` each run also times, in the calling thread only, the steps of the run that
are not the workers' (:data:`PHASES`: the header checks, the content SHA-256 passes, staging,
the table, the intensity checks...) and the report gives each worker value's median per step:
where the serial time of a run goes. A step a version does not have is left out, and the
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
import random
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
ACCEPTANCE_REPEATS = 10
"""The fewest rounds an acceptance run (not ``--quick``) may have."""
QUICK_REPEATS = 3
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
    if workers != "1" or max_ram:  # workers 1 without a budget: the baseline's call
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
    """Run preprocessing in a fresh process into a new output folder; return what it gave.

    The child runs under ``taskset -c`` with ``--cpus`` where it exists; the load average is
    recorded before and after.
    """
    output = folder / f"output_{workers}"
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir()
    shutil.copy(folder / PUBLISHED_NAME, output / PUBLISHED_NAME)
    command = [
        *pin_prefix(args.cpus),
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
    load_before = load_average()
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    load_after = load_average()
    if done.returncode != 0:
        raise SystemExit(f"workers={workers} failed:\n{done.stderr}")
    found = json.loads(done.stdout.strip().splitlines()[-1])
    if not found["written"]:
        raise SystemExit(f"workers={workers} published nothing: {found['issues']}")
    found["digests"] = digests(output)
    found["read_in_blocks"] = _in_blocks(output)
    found["load_before"], found["load_after"] = load_before, load_after
    return found


def pin_prefix(cpus: str | None) -> list[str]:
    """Return ``["taskset", "-c", cpus]`` when pinning is asked and ``taskset`` exists, else []."""
    if not cpus:
        return []
    taskset = shutil.which("taskset")
    return [taskset, "-c", cpus] if taskset else []


def load_average() -> list[float] | None:
    """Return the 1, 5 and 15 minute load averages, or None where the system has none."""
    try:
        return [round(value, 2) for value in os.getloadavg()]
    except (AttributeError, OSError):
        return None


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


def summary(rounds: list[dict], workers: list[str]) -> tuple[dict, bool]:
    """Return each worker value's summary, and whether every run gave the serial digests.

    Its wall times, paired speed-up, peak RSS distribution, load and digests.
    """
    reference = rounds[0]["runs"]["1"]["digests"]
    found = {w: [r["runs"][w] for r in rounds] for w in workers}
    same = all(run["digests"] == reference for runs in found.values() for run in runs)
    table = {
        w: {
            "workers_used": runs[0]["workers"],
            "median_wall_seconds": round(statistics.median(r["wall_seconds"] for r in runs), 3),
            "wall_seconds": [round(r["wall_seconds"], 3) for r in runs],
            "paired_speedup": _paired(rounds, w),
            "peak_rss_mib": _distribution([r["peak_rss_bytes"] / MIB for r in runs]),
            "load_1min_before": _distribution(
                [r["load_before"][0] for r in runs if r["load_before"] is not None]
            ),
            "digests": runs[0]["digests"],
            "digests_equal_to_serial": all(r["digests"] == reference for r in runs),
            **_phase_medians(runs),
        }
        for w, runs in found.items()
    }
    return table, same


def _paired(rounds: list[dict], workers: str) -> dict:
    """Return per round ``serial wall / wall`` of ``workers`` (same round), and its spread."""
    ratios = [r["runs"]["1"]["wall_seconds"] / r["runs"][workers]["wall_seconds"] for r in rounds]
    return _distribution(ratios)  # "values": one per round, in the order of the rounds


def _distribution(values: list[float]) -> dict:
    """Return the min, median and max of ``values`` (rounded), and every value; {} for none."""
    if not values:
        return {}
    rounded = [round(v, 3) for v in values]
    return {
        "min": min(rounded),
        "median": round(statistics.median(values), 3),
        "max": max(rounded),
        "values": rounded,
    }


def round_orders(workers: list[str], repeats: int, how: str, seed: int) -> list[list[str]]:
    """Return the order of the configurations in each round (module docstring, *Order*).

    ``"balanced"``: per block of ``len(workers)`` rounds, a seeded permutation rotated by one
    place per round (every configuration once in every position per block); ``"shuffled"``:
    an independent seeded permutation per round; ``"fixed"``: ``workers`` every round.
    """
    rng = random.Random(seed)
    orders: list[list[str]] = []
    base = list(workers)
    for number in range(repeats):
        order = list(workers)
        if how == "balanced":
            if number % len(workers) == 0:
                base = list(workers)
                rng.shuffle(base)
            shift = number % len(workers)
            order = base[shift:] + base[:shift]
        elif how == "shuffled":
            rng.shuffle(order)
        orders.append(order)
    return orders


def run_rounds(folder: Path, experiment: Path, args: argparse.Namespace) -> list[dict]:
    """Run every round, its configurations in a seeded (recorded) order; return the rounds.

    Each run's digests are printed to standard error as it finishes.
    """
    orders = round_orders(args.workers, args.repeats, args.order, args.seed)
    rounds = []
    for number, order in enumerate(orders):
        runs = {}
        for workers in order:
            runs[workers] = one_run(folder, experiment, workers, args)
            wall = runs[workers]["wall_seconds"]
            print(
                f"round {number + 1}/{args.repeats} workers={workers}: {wall:.2f} s, "
                f"peak RSS {runs[workers]['peak_rss_bytes'] / MIB:.0f} MiB, "
                f"digests {json.dumps(runs[workers]['digests'])}",
                file=sys.stderr,
            )
        rounds.append({"round": number + 1, "order": order, "runs": runs})
    return rounds


def _phase_medians(found: list[dict]) -> dict:
    """Return ``{"phases_median_seconds": {step: median}}`` of runs timed by step, else {}."""
    steps = [step for step in found[0].get("phases", {})]
    if not steps:
        return {}
    medians = {s: round(statistics.median(r["phases"][s] for r in found), 3) for s in steps}
    return {"phases_median_seconds": medians}


def parse_arguments() -> argparse.Namespace:
    """Parse the command line; refuse an acceptance run of fewer than 10 rounds."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--folder", type=Path, default=None)
    parser.add_argument("--workers", nargs="+", default=["1", "2", "4", "auto"])
    parser.add_argument("--repeats", type=int, default=None, help="rounds (default 10; 3 quick)")
    parser.add_argument("--quick", action="store_true", help="smoke mode: not a result")
    parser.add_argument("--seed", type=int, default=0, help="seed of the run order")
    parser.add_argument("--order", choices=("balanced", "shuffled", "fixed"), default="balanced")
    parser.add_argument("--cpus", default=None, help="pin the runs with taskset -c CPUS")
    parser.add_argument("--scale", type=int, default=1, help="multiply each side by this")
    parser.add_argument("--memory-limit-mib", type=int, default=64)
    parser.add_argument("--max-ram-mib", type=int, default=0, help="0: no budget")
    parser.add_argument("--phases", action="store_true", help="time the calling thread's steps")
    args = parser.parse_args()
    if args.repeats is None:
        args.repeats = QUICK_REPEATS if args.quick else ACCEPTANCE_REPEATS
    if args.repeats < 1 or (not args.quick and args.repeats < ACCEPTANCE_REPEATS):
        parser.error(f"an acceptance run needs --repeats {ACCEPTANCE_REPEATS} or more (--quick)")
    args.workers = list(dict.fromkeys(["1", *args.workers]))  # the serial run, once, first
    return args


def protocol(args: argparse.Namespace) -> dict:
    """Return how the runs were made: mode, rounds, order seed, pinning and settings."""
    pinned = pin_prefix(args.cpus)
    return {
        "mode": "quick" if args.quick else "acceptance",
        "repeats": args.repeats,
        "order": args.order,
        "seed": args.seed,
        "pinning": f"taskset -c {args.cpus}"
        if pinned
        else ("unavailable" if args.cpus else "none"),
        "max_ram_mib": args.max_ram_mib or None,
        "load_at_start": load_average(),
    }


def main() -> None:
    """Parse the arguments, write the dataset if needed, run every configuration and report."""
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        experiment, output, workers, limit, max_ram, phases = sys.argv[2:8]
        limits = (int(limit), int(max_ram))
        child(Path(experiment), Path(output), workers, limits, phases == "1")
        return
    args = parse_arguments()
    has_workers = "workers" in {f.name for f in dataclasses.fields(RunMode)}
    if not has_workers and args.workers != ["1"]:
        raise SystemExit("This version has no workers option: run with --workers 1 only.")
    folder = args.folder or Path(tempfile.mkdtemp(prefix="run_workers_"))
    experiment = write_dataset(folder, args.scale)
    setup = protocol(args)
    warm_up = one_run(folder, experiment, "1", args)  # the disk cache, not counted
    rounds = run_rounds(folder, experiment, args)
    table, same = summary(rounds, args.workers)
    files = sorted(p for p in experiment.rglob("*") if p.is_file())
    every_peak = [r["runs"][w]["peak_rss_bytes"] for r in rounds for w in args.workers]
    report = {
        "folder": str(folder),
        "machine": machine(),
        "protocol": {**setup, "load_at_end": load_average()},
        "dataset": {
            "images": 2 * len(IMAGES),
            "read_in_blocks": warm_up["read_in_blocks"],
            "bytes_on_disk": sum(p.stat().st_size for p in files),
            "scale": args.scale,
            "memory_limit_mib": args.memory_limit_mib,
        },
        "rounds": rounds,
        "runs": table,
        "max_peak_rss_mib": round(max(every_peak) / MIB, 1),
        "outputs_identical": same,
    }
    print(json.dumps(report, indent=2))
    if not same:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
