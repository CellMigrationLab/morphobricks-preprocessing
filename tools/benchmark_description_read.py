"""Benchmark the strict reader of an experiment description by number of repeats (issue #505).

Usage::

    python tools/benchmark_description_read.py [--quick] [--repeats N]
        [--sizes 100 1000 3000 10000] [--profile N] [--top 15] [--no-trace] [--output FILE]

For each number of biological repeats (``--sizes``) it writes, untimed, one valid
``morphobricks.toml`` with the canonical writer: two conditions (so every repeat has two
members), one technical repeat per member with two channels, and two mask types. It then
measures ``read_description_bytes`` (phases 1 to 5 and the warnings) with
``benchmark_harness.measure`` (no warm-up call at each size: one small read first makes the
imports; median of ``--repeats`` timed calls, peak of
the traced memory unless ``--no-trace``, SHA-256 of the output: the phase, the issues, and the
canonical bytes of the model built from the accepted document, so a faster reader must give
the same digest). It also times each phase once (``description_read.decode``, every phase of
``description_read.PHASES``, ``description_warnings.check_warnings``, and each check of
phase 5 on its own, :data:`SEMANTIC_CHECKS`) and reports the growth
between consecutive sizes as an exponent: ``log(t2 / t1) / log(n2 / n1)``, 1 for linear, 2
for quadratic.

``--profile N`` also runs the reader once at ``N`` repeats under ``cProfile`` and reports the
``--top`` functions by own time, with their calls and cumulative time. ``--quick`` is the smoke
mode (30 and 100 repeats, one timed call, profile at 100): it shows the benchmark still runs,
never a result. Each size records the SHA-256 of the description bytes it reads
(``input_digest``). Timings depend on the computer and its load: quote them with the machine of
the report.

Private names used on purpose (not public API; update the tool when they change):
``description_read.PHASES``, ``description_read.decode``,
``description_warnings.check_warnings`` and the ``description_semantics._...`` checks of
:data:`SEMANTIC_CHECKS`, looked up by name (a check this version lacks is skipped, and the
test of this tool fails when one of the expected phases or checks is missing from a report).
"""

from __future__ import annotations

import argparse
import cProfile
import json
import math
import pstats
import sys
import time
from pathlib import Path

from benchmark_harness import digest, machine, measure, process_peak

FULL_SIZES = (100, 1000, 3000, 10000)
QUICK_SIZES = (30, 100)
CONDITIONS = ("ctrl", "drug")
CHANNELS = ("actin", "dapi")
MASKS = ("cells", "nuclei")
SEMANTIC_CHECKS = (
    "_repeat_membership",
    "_unit_channels",
    "_channel_tables",
    "_mask_channels",
    "_display_names",
    "_used_ids",
    "_source_folders",
    "_mask_types",
    "_references",
)
"""The checks ``description_semantics.check_semantics`` makes, each timed on its own."""


def description_bytes(repeats: int) -> bytes:
    """Return the canonical bytes of a valid description of ``repeats`` repeats."""
    from morphobricks_preprocessing.description import (
        Channel,
        Condition,
        Description,
        Mask,
        Member,
        Repeat,
        Unit,
    )
    from morphobricks_preprocessing.description_write import canonical_bytes

    unit = {"single": Unit("single", CHANNELS)}
    found = {}
    for index in range(repeats):
        members = {c: Member(f"r{index:05d}", 4, unit) for c in CONDITIONS}
        found[f"rep{index:05d}"] = Repeat(True, "2026-01-01", members)
    described = Description(
        conditions={c: Condition(c) for c in CONDITIONS},
        channels={c: Channel() for c in CHANNELS},
        repeats=found,
        used_ids={
            "conditions": CONDITIONS,
            "channels": CHANNELS,
            "repeats": tuple(found),
            "technical_repeats": ("single",),
        },
        masks={m: Mask(m, "instances") for m in MASKS},
    )
    return canonical_bytes(described)


def read(data: bytes) -> tuple:
    """Read the bytes strictly; return the phase, the issue codes and the model's bytes."""
    from morphobricks_preprocessing.description import from_document
    from morphobricks_preprocessing.description_read import read_description_bytes
    from morphobricks_preprocessing.description_write import canonical_bytes

    report = read_description_bytes(data)
    codes = tuple((str(i.code), i.path) for i in (*report.errors, *report.warnings))
    model = canonical_bytes(from_document(report.document)) if report.ok else None
    return report.phase, codes, model


def phase_seconds(data: bytes) -> dict[str, float]:
    """Time each phase of the reader once, in order, on bytes every phase accepts."""
    from morphobricks_preprocessing.description_read import PHASES, decode
    from morphobricks_preprocessing.description_warnings import check_warnings

    start = time.perf_counter()
    doc, _ = decode(data)
    found = {"1_decode": time.perf_counter() - start}
    for number, check in PHASES:
        start = time.perf_counter()
        check(doc)
        found[f"{number}_{check.__name__}"] = time.perf_counter() - start
    start = time.perf_counter()
    check_warnings(doc)
    found["warnings"] = time.perf_counter() - start
    found.update(semantic_seconds(doc))
    return {name: round(seconds, 6) for name, seconds in found.items()}


def semantic_seconds(doc: dict) -> dict[str, float]:
    """Time each check of phase 5 once (:data:`SEMANTIC_CHECKS`, those this version has)."""
    from morphobricks_preprocessing import description_semantics
    from morphobricks_preprocessing.description_report import Issues

    found = {}
    for name in SEMANTIC_CHECKS:
        check = getattr(description_semantics, name, None)
        if check is None:
            continue
        start = time.perf_counter()
        check(doc, Issues())
        found[f"5.{name}"] = time.perf_counter() - start
    return found


def growth(sizes: list[dict]) -> list[dict]:
    """Return the exponent of the median time between consecutive sizes."""
    found = []
    for small, large in zip(sizes, sizes[1:], strict=False):
        t1, t2 = small["wall_seconds"]["median"], large["wall_seconds"]["median"]
        n1, n2 = small["repeats_in_description"], large["repeats_in_description"]
        exponent = math.log(t2 / t1) / math.log(n2 / n1) if t1 > 0 and t2 > 0 else None
        found.append({"from": n1, "to": n2, "exponent": exponent and round(exponent, 2)})
    return found


def profile(repeats: int, top: int) -> dict:
    """Run the reader once under cProfile; return its top functions by own time."""
    from morphobricks_preprocessing.description_read import read_description_bytes

    data = description_bytes(repeats)
    profiler = cProfile.Profile()
    profiler.runcall(read_description_bytes, data)
    stats = pstats.Stats(profiler)
    rows = []
    for (file, line, name), (_, calls, own, cumulative, _) in stats.stats.items():
        where = f"{Path(file).name}:{line}({name})"
        rows.append({"function": where, "calls": calls, "own_s": own, "cumulative_s": cumulative})
    rows.sort(key=lambda row: row["own_s"], reverse=True)
    for row in rows:
        row["own_s"], row["cumulative_s"] = round(row["own_s"], 4), round(row["cumulative_s"], 4)
    total = round(stats.total_tt, 4)
    return {"repeats_in_description": repeats, "total_s": total, "top": rows[:top]}


def _measured(size: int, repeats: int, trace: bool) -> dict:
    """Measure the reader at one size, with its phases."""
    data = description_bytes(size)
    found = {"repeats_in_description": size, "members": size * len(CONDITIONS)}
    found["bytes"] = len(data)
    found["input_digest"] = digest(data)
    found.update(measure(lambda: read(data), repeats, warmup=0, trace=trace))
    found["phase_seconds"] = phase_seconds(data)
    print(f"{size} repeats: {found['wall_seconds']['median']} s", file=sys.stderr)
    return found


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Return the command-line arguments; sizes, repeats and profile default by mode."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--quick", action="store_true", help="smoke mode: small sizes")
    parser.add_argument("--sizes", type=int, nargs="+", default=None)
    parser.add_argument("--repeats", type=int, default=None, help="timed calls per size")
    parser.add_argument("--profile", type=int, default=None, help="repeats to profile at")
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--no-trace", action="store_true", help="skip the traced memory call")
    parser.add_argument("--output", type=Path, default=None, help="also write the JSON here")
    args = parser.parse_args(argv)
    args.sizes = args.sizes or list(QUICK_SIZES if args.quick else FULL_SIZES)
    args.repeats = args.repeats or (1 if args.quick else 3)
    if args.quick and args.profile is None:
        args.profile = QUICK_SIZES[-1]
    if args.repeats < 1 or min(args.sizes) < 1:
        parser.error("--repeats and --sizes must be at least 1")
    return args


def main(argv: list[str] | None = None) -> dict:
    """Run the benchmark, print the report as JSON and return it."""
    args = parse_arguments(argv)
    read(description_bytes(10))  # imports and first calls, untimed (the sizes need no warm-up)
    sizes = [_measured(n, args.repeats, not args.no_trace) for n in sorted(set(args.sizes))]
    report = {
        "benchmark": "description_read",
        "mode": "quick" if args.quick else "full",
        "repeats": args.repeats,
        "machine": machine(),
        "sizes": sizes,
        "growth": growth(sizes),
        "profile": None if args.profile is None else profile(args.profile, args.top),
        "process": process_peak(),
    }
    text = json.dumps(report, indent=2)
    if args.output is not None:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return report


if __name__ == "__main__":
    main()
