"""Benchmark the hot kernels of a preprocessing run, one call at a time (issue #106).

Usage::

    python tools/benchmark_kernels.py [--quick] [--repeats N] [--group NAME ...]
        [--folder DIR] [--output FILE]

The whole-run benchmarks (``benchmark_run_workers.py``, ``benchmark_image_qc.py``) time a run
end to end; this one times the kernels under it, each on fixed seeded inputs
(``benchmark_inputs.py``), so a later optimisation of one kernel can be measured alone and
shown to give identical outputs. The groups (``--group``, all by default):

- ``histogram``: ``compute_plane_histogram`` of one plane (normalization brick, as
  ``Plane.histogram`` calls it) of each pixel type (``uint8``, ``uint16``, ``float32``) at
  2048 and 4096 px, over the whole plane and over a region (``check_region`` of the cells
  mask, then the histogram of the pixels in it: the region route of Spec ADR 0005; the
  preprocessing run itself has no masked histogram);
- ``reads``: ``read_plane`` (whole plane) and ``read_window`` (a diagonal of 512 px windows)
  of a ``uint16`` plane from TIFF files tiled or stripped, zlib or uncompressed, and from an
  OME-Zarr (NGFF 0.4) store (``benchmark_reads.py``; the files are written first, untimed);
- ``masks``: the mask step's kernels on two instance masks of one plane (cells, and one
  nucleus inside each, ``int32``, about 1,000 objects at 2048 px and 4,000 at 4096 px):
  ``check_labels``, ``piece_counts``, ``pair_counts`` and the scan of one position
  (``mask_scan._scan_plane``: the pieces of both masks and both pairs);
- ``channel_identity``: ``check_channel_identity`` of the image summaries of 72 and 400
  units of three channels.

``object_sides()`` and its region helpers are MorphoFeature's, not this repository's, and are
not measured here.

``--quick`` is the smoke mode (256 px planes, 64 objects, 12 units, 3 timed calls): it shows
the benchmarks still run and still give a digest, never a result. Each case is measured by
``benchmark_harness.measure`` (median of ``--repeats`` timed calls after one warm-up call,
peak of the traced memory, SHA-256 of the output), and the report holds the machine
(``benchmark_harness.machine``) and this process's peak resident memory at the end. Reads are
from a warm disk cache (the warm-up call reads the file first). Timings depend on the computer
and its load: quote them with the machine and load of the report.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NamedTuple

import benchmark_inputs as inputs  # tools/, next to this script
from benchmark_harness import machine, measure, process_peak

FULL_SIDES = (2048, 4096)
QUICK_SIDES = (256,)
OBJECTS = {256: 64, 2048: 1024, 4096: 4096}
"""Cells per mask at each side: one per 64 x 64 px."""
FULL_UNITS = ((72, 10), (400, 5))
QUICK_UNITS = ((12, 2),)
"""(units, images per unit) of the channel-identity cases."""
FULL_REPEATS = 7
QUICK_REPEATS = 3


class Case(NamedTuple):
    """One benchmark: its group, name, parameters, and the call to time (inputs built)."""

    group: str
    name: str
    params: dict
    run: Callable[[], object]


def histogram_cases(sides: tuple[int, ...]) -> Iterator[Case]:
    """Yield the histogram of a whole plane and of a region, per pixel type and side."""
    from morphobricks_normalization.histogram import compute_plane_histogram
    from morphobricks_normalization.regions import check_region

    for side in sides:
        cells, _ = inputs.label_masks(side, OBJECTS[side])
        for dtype in inputs.DTYPES:
            pixels = inputs.plane(side, dtype)
            params = {"side": side, "dtype": dtype, "bins": "default"}

            def whole(pixels: object = pixels) -> object:
                return compute_plane_histogram(pixels)

            def region(pixels: object = pixels, cells: object = cells) -> object:
                inside = check_region(cells, pixels.shape, name="cells")
                return compute_plane_histogram(pixels[inside])

            yield Case("histogram", f"whole_{dtype}_{side}", params, whole)
            share = round(float((cells != 0).mean()), 4)
            yield Case("histogram", f"region_{dtype}_{side}", {**params, "share": share}, region)


def mask_cases(sides: tuple[int, ...]) -> Iterator[Case]:
    """Yield the mask step's kernels on the cells and nuclei masks of one plane, per side."""
    from morphobricks_preprocessing.mask_scan import (
        _scan_plane,
        mask_thresholds,
        pair_counts,
        piece_counts,
    )
    from morphobricks_preprocessing.masks import check_labels

    limits = mask_thresholds()
    for side in sides:
        cells, nuclei = inputs.label_masks(side, OBJECTS[side])
        params = {"side": side, "dtype": "int32", "objects": int(cells.max())}

        def labels(cells: object = cells) -> object:
            check_labels(cells)
            return True

        def scan(cells: object = cells, nuclei: object = nuclei) -> object:
            masks = {"cells": cells, "nuclei": nuclei}
            pieces: dict = {name: [] for name in masks}
            single: dict = {name: [] for name in masks}
            totals: dict = {}
            _scan_plane(masks, pieces, single, totals, limits)
            return pieces, single, totals

        yield Case("masks", f"check_labels_{side}", params, labels)
        yield Case("masks", f"piece_counts_{side}", params, lambda m=cells: piece_counts(m))
        yield Case(
            "masks",
            f"pair_counts_{side}",
            params,
            lambda c=nuclei, p=cells: pair_counts(c, p, thresholds=limits),
        )
        yield Case("masks", f"scan_position_{side}", {**params, "mask_types": 2}, scan)


def channel_identity_cases(sizes: tuple[tuple[int, int], ...]) -> Iterator[Case]:
    """Yield the channel-identity check of the summaries of each number of units."""
    from morphobricks_preprocessing.channel_identity import check_channel_identity

    for units, images in sizes:
        found = inputs.summaries(units, images)
        params = {"units": units, "images_per_unit": images, "channels": 3}
        yield Case(
            "channel_identity",
            f"check_{units}_units",
            params,
            lambda s=found: check_channel_identity(s),
        )


def cases(group: str, quick: bool, folder: Path) -> Iterator[Case]:
    """Yield the cases of one group, in quick or full size."""
    sides = QUICK_SIDES if quick else FULL_SIDES
    if group == "histogram":
        yield from histogram_cases(sides)
    elif group == "reads":
        from benchmark_reads import read_cases

        for group_name, name, params, run in read_cases(sides, quick, folder):
            yield Case(group_name, name, params, run)
    elif group == "masks":
        yield from mask_cases(sides)
    elif group == "channel_identity":
        yield from channel_identity_cases(QUICK_UNITS if quick else FULL_UNITS)


GROUPS = ("histogram", "reads", "masks", "channel_identity")


def run_groups(groups: list[str], quick: bool, repeats: int, folder: Path) -> list[dict]:
    """Measure every case of the groups; print each to standard error as it finishes."""
    results = []
    for group in groups:
        for case in cases(group, quick, folder):
            found = {"group": case.group, "name": case.name, "params": case.params}
            if isinstance(case.run, str):  # a case that cannot run here says why
                found["skipped"] = case.run
            else:
                found.update(measure(case.run, repeats))
            results.append(found)
            shown = found.get("wall_seconds", {}).get("median", found.get("skipped"))
            print(f"{case.group}/{case.name}: {shown}", file=sys.stderr)
    return results


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Return the command-line arguments; ``--repeats`` defaults by mode."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--quick", action="store_true", help="smoke mode: small inputs")
    parser.add_argument("--repeats", type=int, default=None, help="timed calls per case")
    parser.add_argument("--group", nargs="+", choices=GROUPS, default=list(GROUPS))
    parser.add_argument("--folder", type=Path, default=None, help="where the read files go")
    parser.add_argument("--output", type=Path, default=None, help="also write the JSON here")
    args = parser.parse_args(argv)
    if args.repeats is None:
        args.repeats = QUICK_REPEATS if args.quick else FULL_REPEATS
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    return args


def main(argv: list[str] | None = None) -> dict:
    """Run the benchmarks, print the report as JSON and return it."""
    args = parse_arguments(argv)
    with tempfile.TemporaryDirectory(prefix="kernel_benchmark_") as scratch:
        folder = args.folder or Path(scratch)
        report = {
            "benchmark": "kernels",
            "mode": "quick" if args.quick else "full",
            "repeats": args.repeats,
            "machine": machine(),
            "cases": run_groups(args.group, args.quick, args.repeats, folder),
            "process": process_peak(),
        }
    text = json.dumps(report, indent=2)
    if args.output is not None:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return report


if __name__ == "__main__":
    main()
