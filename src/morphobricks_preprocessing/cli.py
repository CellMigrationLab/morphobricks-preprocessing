"""Command-line interface: ``morphobricks-preprocessing check`` and ``run``.

``check`` runs the preflight (folders and image headers, no pixels) and prints
what will be analysed, the problems found and the suggested axis roles.
``run`` processes the experiment with the axis roles given in ``--axes``; roles
are never taken from metadata without being stated. Both exit with status 1
when an error blocks processing.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Sequence

from .axes import AxisMapping, reference_axes
from .discovery import default_output_folder
from .planes import DEFAULT_MEMORY_LIMIT, format_bytes
from .report import format_issues, label_tree, mapping_summary, preflight_summary, run_summary
from .run import preflight, run_experiment
from .validation import plural
from .zarr_export import (
    CHUNK_SIDE,
    COMPRESSIONS,
    MAX_CHUNK_SIZE,
    MAX_LEVELS,
    MIN_CHUNK_SIZE,
    ConversionSettings,
    estimate_conversion,
    free_space,
)

_UNITS = {
    "": 1,
    "B": 1,
    "KIB": 2**10,
    "MIB": 2**20,
    "GIB": 2**30,
    "KB": 10**3,
    "MB": 10**6,
    "GB": 10**9,
}


def parse_size(text: str) -> int:
    """Parse a memory size such as ``"1GiB"``, ``"512 MiB"`` or ``"1073741824"``.

    Raises
    ------
    argparse.ArgumentTypeError
        If the text is not a positive size.
    """
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([A-Za-z]*)\s*", text)
    unit = match.group(2).upper() if match else None
    if not match or unit not in _UNITS or float(match.group(1)) <= 0:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a memory size; use for example 1GiB, 512MiB or a number of bytes."
        )
    return int(float(match.group(1)) * _UNITS[unit])


def parse_pyramid_levels(text: str) -> int | None:
    """Parse ``--pyramid-levels``: ``auto`` (None), ``none`` (0) or a number of levels.

    Raises
    ------
    argparse.ArgumentTypeError
        If the text is none of these.
    """
    value = text.strip().lower()
    if value == "auto":
        return None
    if value == "none":
        return 0
    if value.isdigit() and 0 <= int(value) < MAX_LEVELS:
        return int(value)
    raise argparse.ArgumentTypeError(
        f"{text!r} is not a number of pyramid levels; use auto, none or 1 to {MAX_LEVELS - 1}."
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="morphobricks-preprocessing",
        description="Per-plane intensity histograms for an experiment folder of TIFF and "
        "OME-Zarr images.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser(
        "check", help="check the folder and image headers and suggest axis roles"
    )
    _common(check)

    run = commands.add_parser("run", help="compute or update the histogram table")
    _common(run)
    run.add_argument(
        "--axes",
        required=True,
        metavar="ROLES",
        help="role of each axis of the images, in the order of their axes: X, Y, Z, C, T, or "
        "- for none (for example ZCYX). 'check' prints the suggested value.",
    )
    run.add_argument("--bins", type=int, help="bins per plane (default: depends on pixel type)")
    run.add_argument(
        "--memory-limit",
        type=parse_size,
        default=DEFAULT_MEMORY_LIMIT,
        metavar="SIZE",
        help="working-memory limit per plane, e.g. 2GiB (default: 1GiB)",
    )
    run.add_argument(
        "--missing-as-singleton",
        action="store_true",
        help="confirm that images lacking some axes have one plane along them",
    )
    run.add_argument(
        "--force-rebuild", action="store_true", help="recompute every row of the table"
    )
    run.add_argument(
        "--convert-tiff",
        action="store_true",
        help="convert every TIFF image to OME-Zarr in <output>/ome_zarr first and compute "
        "its histograms from the copy (needs the zarr extra); OME-Zarr images are always "
        "read directly",
    )
    conversion = run.add_argument_group(
        "conversion settings", "used with --convert-tiff; the defaults suit most experiments"
    )
    conversion.add_argument(
        "--chunk-size",
        type=int,
        default=CHUNK_SIDE,
        metavar="PX",
        help=f"side of the stored tiles in pixels ({MIN_CHUNK_SIZE} to {MAX_CHUNK_SIZE}; "
        f"default {CHUNK_SIDE})",
    )
    conversion.add_argument(
        "--compression",
        choices=sorted(COMPRESSIONS),
        default="lz4",
        help="lossless compression: lz4 (default, fast), zstd (smaller, slower) or none",
    )
    conversion.add_argument(
        "--compression-level",
        type=int,
        default=5,
        metavar="N",
        help="1 (fastest) to 9 (smallest files); default 5",
    )
    conversion.add_argument(
        "--pyramid-levels",
        type=parse_pyramid_levels,
        default=None,
        metavar="auto|none|N",
        help="smaller levels for viewing: auto (default, halve until a plane fits 512 px), "
        f"none, or a number from 1 to {MAX_LEVELS - 1}",
    )
    run.add_argument("--log", metavar="FILE", help="write technical details to this file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line; return the exit status."""
    _safe_output()
    args = build_parser().parse_args(argv)
    handler = _attach_log(args.log) if getattr(args, "log", None) else None
    try:
        return _check(args) if args.command == "check" else _run(args)
    finally:
        if handler is not None:
            logging.getLogger("morphobricks_preprocessing").removeHandler(handler)
            handler.close()


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("experiment", help="the experiment folder")
    parser.add_argument(
        "--output",
        metavar="FOLDER",
        help="output folder (default: next to the experiment, <name>_MorphoBricks_Preprocessing)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATH",
        help="image to leave out, relative to the experiment folder (repeatable)",
    )


def _check(args: argparse.Namespace) -> int:
    pre = preflight(args.experiment, args.output, exclude=args.exclude)
    print(preflight_summary(pre))
    if pre.discovery.leaves:
        print()
        print(label_tree(pre.discovery))
    if pre.issues:
        print()
        print(format_issues(pre.issues))
    if pre.suggested_mapping is not None:
        print()
        print(
            f"Suggested option for 'run': --axes {pre.suggested_mapping.roles_string} "
            f"(axes in the files: {pre.suggested_mapping.axes})"
        )
    return 0 if pre.ok else 1


def _run(args: argparse.Namespace) -> int:
    pre = preflight(args.experiment, args.output, exclude=args.exclude)
    axes = reference_axes(pre.metadata.files)
    if axes is None:
        print(format_issues(pre.issues) or "No readable images were found.")
        return 1
    try:
        mapping = AxisMapping(axes, tuple(args.axes))
    except ValueError:
        print(
            f"--axes {args.axes} does not fit the images, whose axes are {axes}: give one role "
            f"(X, Y, Z, C, T or -) for each of the {len(axes)} axes, for example "
            f"--axes {pre.suggested_mapping.roles_string if pre.suggested_mapping else axes}."
        )
        return 1
    try:
        conversion = ConversionSettings(
            chunk_size=args.chunk_size,
            compression=args.compression,
            compression_level=args.compression_level,
            pyramid_levels=args.pyramid_levels,
        )
    except ValueError as exc:
        print(exc)
        return 1
    print("Axis roles:")
    print(mapping_summary(mapping, pre.metadata.files))
    print()
    if args.convert_tiff:
        print(_conversion_note(pre, conversion, args.output))
        print()

    def progress(done: int, total: int, path: str) -> None:
        if path:
            print(f"[{done + 1}/{total}] {path}", flush=True)

    result = run_experiment(
        args.experiment,
        mapping,
        output_folder=args.output,
        bin_count=args.bins,
        memory_limit=args.memory_limit,
        missing_as_singleton=args.missing_as_singleton,
        exclude=args.exclude,
        force_rebuild=args.force_rebuild,
        progress=progress,
        convert_tiffs=args.convert_tiff,
        conversion=conversion,
    )
    if result.issues:
        print(format_issues(result.issues))
        print()
    print(run_summary(result))
    return 0 if result.written else 1


def _conversion_note(pre, conversion: ConversionSettings, output) -> str:
    estimate = estimate_conversion(pre.discovery.root, pre.metadata.files, conversion)
    if not estimate.image_count:
        return "Conversion: there are no TIFF images to convert."
    folder = output or default_output_folder(pre.discovery.root)
    free = free_space(folder)
    lines = [
        f"Conversion ({conversion.describe()}): {plural(estimate.image_count, 'TIFF image')} "
        "will be stored again in OME-Zarr format, so the space they take is duplicated.",
        f"  TIFF images: {format_bytes(estimate.source_bytes)}; copies: up to about "
        f"{format_bytes(estimate.copy_bytes)} before compression; free space on the output "
        f"drive: {format_bytes(free) if free is not None else 'unknown'}.",
    ]
    if free is not None and estimate.copy_bytes > free:
        lines.append("  Warning: this may not be enough space.")
    return "\n".join(lines)


def _attach_log(path: str) -> logging.Handler:
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger = logging.getLogger("morphobricks_preprocessing")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    return handler


def _safe_output() -> None:
    # Messages use characters such as "±"; consoles with a legacy code page would
    # otherwise stop with an encoding error instead of printing them approximately.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
