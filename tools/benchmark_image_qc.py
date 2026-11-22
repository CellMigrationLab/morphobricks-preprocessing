"""Benchmark the histogram step with and without the image QC metrics (``image_qc.csv``).

Usage::

    python tools/benchmark_image_qc.py --baseline-src DIR [--folder DIR] [--repeats 10]
        [--images 10] [--side 2048] [--channels 4] [--cpus 0-3]

``--baseline-src`` is the ``src`` folder of a checkout without the metrics (for example a
worktree of the commit before them); the code under test is the installed package. The script
writes (once, into ``--folder``) a seeded experiment of ``--images`` uncompressed ``uint16``
OME-TIFFs of ``--channels`` x ``--side`` x ``--side`` pixels, and a second experiment of one
such image that is read in blocks (memory limit 16 MiB), and publishes their descriptions.

Each round runs the four configurations (with and without the metrics, whole and in blocks),
each in a fresh Python process into a new output folder (``mode="full"``), in an order that
alternates between rounds (ABBA), and records the wall time of ``run_experiment`` and of its
histogram step (``run.steps.new_rows``: reading every image, the histograms, the metrics and
the staging; the calling thread, one worker). The report gives, per configuration, the median
and range of both times, and the paired ratio (with / without, per round) of the histogram
step. Every run's ``plane_histograms.csv`` and ``experiment_design.csv`` must have the
baseline's SHA-256 (identical outputs), else the script exits with status 1; the runs with
the metrics must also all write the same ``image_qc.csv``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import tifffile

MIB = 2**20
BLOCK_LIMIT = 16 * MIB
"""The memory limit of the experiment read in blocks."""
CONFIGURATIONS = (
    ("without", "whole"),
    ("with", "whole"),
    ("without", "blocks"),
    ("with", "blocks"),
)


def write_experiment(folder: Path, images: int, shape: tuple[int, int, int]) -> Path:
    """Write one experiment of seeded smooth-plus-noise images and its description."""
    from morphobricks_preprocessing.description import (
        Channel,
        Condition,
        Description,
        Member,
        Repeat,
        Unit,
    )
    from morphobricks_preprocessing.testing_described import PUBLISHED_NAME, description_of

    experiment = folder / "experiment"
    channels = tuple(f"ch{c}" for c in range(shape[0]))
    rng = np.random.default_rng(images)
    y, x = np.ogrid[: shape[1], : shape[2]]
    for index in range(images):
        data = np.empty(shape, dtype=np.uint16)
        for c in range(shape[0]):
            blobs = (np.sin(y / (6.0 + 4 * c)) * np.cos(x / (9.0 + c)) * 900 + 1500).astype(
                np.uint16
            )
            data[c] = blobs + rng.integers(0, 300, shape[1:], dtype=np.uint16)
        path = experiment / "ctrl" / "rep1" / f"image{index:02d}.ome.tif"
        path.parent.mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(path, data, ome=True, photometric="minisblack", metadata={"axes": "CYX"})
    unit = {"single": Unit("single", channels)}
    described = Description(
        conditions={"ctrl": Condition("ctrl")},
        channels={c: Channel() for c in channels},
        repeats={"rep1": Repeat(True, "2026-01-01", {"ctrl": Member("rep1", images, unit)})},
        used_ids={
            "conditions": ("ctrl",),
            "channels": channels,
            "repeats": ("rep1",),
            "technical_repeats": ("single",),
        },
    )
    (folder / PUBLISHED_NAME).write_bytes(description_of(described)[1])
    return experiment


def child(experiment: str, output: str, memory_limit: str) -> None:
    """Run preprocessing once (mode full) and print the wall and histogram-step times."""
    from morphobricks_preprocessing import prefill_mapping, run_experiment
    from morphobricks_preprocessing.run import steps

    spent = {"histogram_step": 0.0}
    new_rows = steps.new_rows

    def timed(*args: object, **kwargs: object) -> object:
        start = time.perf_counter()
        try:
            return new_rows(*args, **kwargs)
        finally:
            spent["histogram_step"] += time.perf_counter() - start

    steps.new_rows = timed
    start = time.perf_counter()
    result = run_experiment(
        experiment,
        prefill_mapping("CYX"),
        output_folder=output,
        memory_limit=int(memory_limit),
        mode="full",
    )
    wall = time.perf_counter() - start
    errors = [issue.code for issue in result.issues if issue.is_error]
    print(json.dumps({"written": result.written, "errors": errors, "wall": wall, **spent}))


def one_run(folder: Path, configuration: tuple[str, str], args: argparse.Namespace) -> dict:
    """Run one configuration in a fresh process; return its times and output digests."""
    metrics, reading = configuration
    data = folder / reading
    output = folder / f"output_{metrics}_{reading}"
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir()
    shutil.copy(data / "morphobricks.toml", output / "morphobricks.toml")
    env = dict(os.environ)
    if metrics == "without":
        env["PYTHONPATH"] = str(Path(args.baseline_src).resolve())
    limit = BLOCK_LIMIT if reading == "blocks" else 2**30
    command = [sys.executable, str(Path(__file__).resolve()), "--child"]
    command += [str(data / "experiment"), str(output), str(limit)]
    if args.cpus and shutil.which("taskset"):
        command = ["taskset", "-c", args.cpus, *command]
    done = subprocess.run(command, capture_output=True, text=True, check=False, env=env)
    if done.returncode != 0:
        raise SystemExit(f"{configuration} failed:\n{done.stderr}")
    found = json.loads(done.stdout.strip().splitlines()[-1])
    if not found["written"]:
        raise SystemExit(f"{configuration} published nothing: {found['errors']}")
    names = ["plane_histograms.csv", "experiment_design.csv", "image_qc.csv"]
    found["digests"] = {
        name: hashlib.sha256((output / name).read_bytes()).hexdigest()
        for name in names
        if (output / name).exists()
    }
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    found["read_in_blocks"] = [f["relative_path"] for f in manifest["files"] if f["read_in_blocks"]]
    return found


def summary(rounds: list[dict]) -> tuple[dict, bool]:
    """Return the times per configuration, the paired ratios, and whether outputs agree."""
    report: dict = {}
    agree = True
    for reading in ("whole", "blocks"):
        runs = {m: [r[f"{m}_{reading}"] for r in rounds] for m in ("without", "with")}
        for metrics, found in runs.items():
            report[f"{metrics}_{reading}"] = {
                key: _spread([run[key] for run in found]) for key in ("histogram_step", "wall")
            }
        ratios = [
            w["histogram_step"] / b["histogram_step"]
            for b, w in zip(runs["without"], runs["with"], strict=True)
        ]
        report[f"ratio_{reading}"] = _spread(ratios)
        for name in ("plane_histograms.csv", "experiment_design.csv"):
            agree &= len({run["digests"][name] for m in runs for run in runs[m]}) == 1
        agree &= len({run["digests"].get("image_qc.csv") for run in runs["with"]}) == 1
        agree &= all("image_qc.csv" not in run["digests"] for run in runs["without"])
    return report, agree


def _spread(values: list[float]) -> dict:
    """Return the median, minimum and maximum of ``values``, rounded."""
    return {
        "median": round(statistics.median(values), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
    }


def main() -> None:
    """Write the experiments once, run the rounds and print the report as JSON."""
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child(*sys.argv[2:5])
        return
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline-src", required=True)
    parser.add_argument("--folder", default="image_qc_benchmark")
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--images", type=int, default=10)
    parser.add_argument("--side", type=int, default=2048)
    parser.add_argument("--channels", type=int, default=4)
    parser.add_argument("--cpus", default=None)
    args = parser.parse_args()
    folder = Path(args.folder)
    shape = (args.channels, args.side, args.side)
    if not (folder / "done").exists():
        write_experiment(folder / "whole", args.images, shape)
        write_experiment(folder / "blocks", 1, shape)
        (folder / "done").write_text("ok\n", encoding="utf-8")
    one_run(folder, CONFIGURATIONS[0], args)  # warm-up (disk cache), not counted
    rounds = []
    for index in range(args.repeats):
        order = CONFIGURATIONS if index % 2 == 0 else CONFIGURATIONS[::-1]
        found = {f"{m}_{r}": one_run(folder, (m, r), args) for m, r in order}
        rounds.append(found)
        print(
            json.dumps({k: round(v["histogram_step"], 3) for k, v in found.items()}),
            file=sys.stderr,
        )
    report, agree = summary(rounds)
    blocks = rounds[0]["with_blocks"]["read_in_blocks"]
    report["dataset"] = {"images": args.images, "shape_cyx": shape, "blocks_images": blocks}
    report["identical_outputs"] = agree
    report["load_average"] = os.getloadavg() if hasattr(os, "getloadavg") else None
    report["cpus"] = args.cpus
    print(json.dumps(report, indent=2))
    if not agree:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
