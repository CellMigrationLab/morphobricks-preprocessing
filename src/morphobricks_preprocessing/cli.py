"""Command-line interface: ``morphobricks-preprocessing check``, ``run`` and ``masks``.

``check`` runs the preflight (folders and image headers, no pixels) and prints
what will be analysed, the problems found and the suggested axis roles.
``run`` processes the experiment with the axis roles given in ``--axes``; roles
are never taken from metadata without being stated. It also needs the
experiment design confirmed in ``experiment_design.csv``: without it, ``run``
writes a template to the output folder for the user to fill in, and stops.
The image type (fluorescence or RGB) is given with ``--image-type``, or the
suggestion from the image headers is used and printed. After writing, ``run``
prints the intensity checks of the table (warnings only; plots are drawn in the
notebook only).
``masks`` is the optional mask step: it pairs every image with its masks (``--masks``, and the
label images of OME-Zarr images), reads them, and writes ``mask_relationships.json`` with each
mask type and the relationships between them (contract §15). All three exit with status 1 when
an error blocks processing.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from morphobricks_normalization.checks import IntensityCheckSettings
from morphobricks_normalization.ranges import (
    DEFAULT_ROBUST_QUANTILES,
    HISTOGRAM_RANGES,
    HistogramSettings,
)

from .axes import AxisMapping, reference_axes, review_axes
from .design import (
    DESIGN_FILENAME,
    DesignFormatError,
    ExperimentDesign,
    design_issues,
    load_design,
    read_experiment_design,
    reconcile_design,
    write_experiment_design,
)
from .discovery import check_output_folder, default_output_folder
from .image_type import IMAGE_TYPES, RGB, ImageTypeReview, review_image_type
from .intensity import intensity_text
from .mask_scan import (
    DEFAULT_THRESHOLDS,
    MASK_REPORT_FILENAME,
    mask_thresholds,
    scan_masks,
    write_mask_report,
)
from .planes import DEFAULT_MEMORY_LIMIT, format_bytes
from .report import (
    balance_summary,
    design_summary,
    format_issues,
    label_tree,
    mapping_summary,
    mask_report_summary,
    mask_summary,
    preflight_summary,
    run_summary,
)
from .run import preflight, run_experiment
from .validation import has_errors, plural
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
    run.add_argument(
        "--design",
        metavar="FILE",
        help="the confirmed experiment design (default: experiment_design.csv in the output "
        "folder); without a confirmed design, run writes a template there and stops",
    )
    run.add_argument(
        "--image-type",
        choices=IMAGE_TYPES,
        help="fluorescence (each channel a separate stain) or rgb (colour images with "
        "three channels); default: the type suggested from the image headers, printed",
    )
    run.add_argument("--bins", type=int, help="bins per plane (default: depends on pixel type)")
    run.add_argument(
        "--histogram-range",
        choices=HISTOGRAM_RANGES,
        default="minmax",
        help="range of histograms that are not exact (float images, and integer images with "
        "more values than bins): minmax (default, each plane's minimum to maximum) or robust "
        "(between the --robust-quantiles, the pixels outside counted separately); 8- and "
        "16-bit histograms are the same either way",
    )
    run.add_argument(
        "--robust-quantiles",
        type=float,
        nargs=2,
        default=list(DEFAULT_ROBUST_QUANTILES),
        metavar=("LOW", "HIGH"),
        help="quantiles in percent of the robust range, also used by the range check "
        "(default 0.01 99.99)",
    )
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
    checks = run.add_argument_group(
        "intensity checks", "limits of the warnings printed after the run; the defaults suit most"
    )
    checks.add_argument(
        "--outlier-limit",
        type=float,
        default=3.0,
        metavar="N",
        help="robust deviations beyond which an image or biological repeat is reported (default 3)",
    )
    checks.add_argument(
        "--saturation-limit",
        type=float,
        default=0.01,
        metavar="FRACTION",
        help="fraction of saturated pixels above which an image is reported (default 0.01, 1 %%)",
    )
    checks.add_argument(
        "--min-images",
        type=int,
        default=5,
        metavar="N",
        help="fewest images a comparison needs (default 5)",
    )
    run.add_argument("--log", metavar="FILE", help="write technical details to this file")

    masks = commands.add_parser(
        "masks",
        help="find every image's masks, describe them and how they relate (reads the masks)",
    )
    _common(masks)
    masks.add_argument("--log", metavar="FILE", help="write technical details to this file")
    limits = masks.add_argument_group(
        "relationship thresholds", "change the defaults (contract §15.1); shares are 0 to 1"
    )
    for name, text in _THRESHOLD_HELP.items():
        limits.add_argument(
            "--" + name.replace("_", "-"),
            dest=name,
            type=float,
            metavar="X",
            help=f"{text} (default {DEFAULT_THRESHOLDS[name]:g})",
        )
    return parser


_THRESHOLD_HELP = {
    "inside_pixel_share": "share of a child's pixels in one parent for it to be inside",
    "inside_object_share": "share of the children inside for a mask to be inside another",
    "parent_larger_share": "share of the inside children whose parent is larger",
    "same_objects_iou": "intersection over union for two objects to match one to one",
    "same_objects_share": "share of the objects of both masks matched for the same objects",
    "grouped_label_share": "share of a grouped mask's pixels carrying the parent's label",
    "grouped_median_pieces": "median pieces per label above which a mask is grouped",
}


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line; return the exit status."""
    _safe_output()
    args = build_parser().parse_args(argv)
    handler = _attach_log(args.log) if getattr(args, "log", None) else None
    try:
        return {"check": _check, "run": _run, "masks": _masks}[args.command](args)
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
    parser.add_argument(
        "--masks",
        metavar="FOLDER",
        help="masks folder: one subfolder per mask type, each mirroring the experiment's "
        "folders, outside the experiment; the label images of OME-Zarr images are added",
    )


def _check(args: argparse.Namespace) -> int:
    pre = preflight(args.experiment, args.output, exclude=args.exclude, masks_folder=args.masks)
    print(preflight_summary(pre))
    if pre.discovery.leaves:
        print()
        print(label_tree(pre.discovery))
    if pre.masks is not None:
        print()
        print(mask_summary(pre.masks))
    if pre.dataset.balance.cells:
        print()
        print(balance_summary(pre.dataset.balance))
    if pre.issues:
        print()
        print(format_issues(pre.issues))
    if pre.dataset.repeats:
        output = Path(args.output) if args.output else default_output_folder(pre.discovery.root)
        design, _, problem = load_design(output, pre.dataset.repeats)
        print()
        if problem:
            print(f"Experiment design: {problem}")
        elif (output / DESIGN_FILENAME).exists():
            print(f"Experiment design ({output / DESIGN_FILENAME}):")
            print(design_summary(design))
        else:
            print(
                "Experiment design: not confirmed yet. 'run' writes experiment_design.csv to "
                "the output folder for you to confirm."
            )
    if pre.suggested_mapping is not None:
        print()
        print(
            f"Suggested option for 'run': --axes {pre.suggested_mapping.roles_string} "
            f"(axes in the files: {pre.suggested_mapping.axes})"
        )
        type_review = review_image_type(
            pre.metadata, review_axes(pre.metadata, pre.suggested_mapping)
        )
        print(
            f"Suggested image type: {type_review.suggested}, because {type_review.reason}. "
            "'run' uses it unless you give --image-type."
        )
    return 0 if pre.ok else 1


def _masks(args: argparse.Namespace) -> int:
    changes = {name: getattr(args, name) for name in _THRESHOLD_HELP}
    try:
        thresholds = mask_thresholds({k: v for k, v in changes.items() if v is not None})
    except ValueError as exc:
        print(exc)
        return 1
    pre = preflight(
        args.experiment,
        args.output,
        exclude=args.exclude,
        masks_folder=args.masks,
        ome_zarr_labels=True,
    )
    print(mask_summary(pre.masks))
    blocking = [*pre.discovery.issues, *pre.exclusion_issues, *pre.masks.issues]
    if blocking:
        print()
        print(format_issues(blocking))
    if has_errors(blocking):
        return 1
    if not pre.masks.types:
        print("Give the masks folder with --masks, or use OME-Zarr images with label images.")
        return 1

    def progress(done: int, total: int, path: str) -> None:
        if path:
            print(f"[{done + 1}/{total}] {path}", flush=True)

    print()
    try:
        report = scan_masks(pre.masks, pre.discovery.root, progress=progress, thresholds=thresholds)
    except ValueError as exc:  # a mask whose pixels cannot be used
        print(f"The masks cannot be used: {exc}")
        return 1
    output = Path(args.output) if args.output else default_output_folder(pre.discovery.root)
    path = write_mask_report(report, output / MASK_REPORT_FILENAME)
    print()
    print(mask_report_summary(report))
    print()
    print(f"Report written to {path}")
    return 0


def _run(args: argparse.Namespace) -> int:
    pre = preflight(args.experiment, args.output, exclude=args.exclude, masks_folder=args.masks)
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
    try:
        intensity_settings = IntensityCheckSettings(
            outlier_limit=args.outlier_limit,
            saturation_limit=args.saturation_limit,
            min_images=args.min_images,
        )
    except ValueError as exc:
        print(exc)
        return 1
    try:
        histogram_settings = HistogramSettings(
            histogram_range=args.histogram_range, robust_quantiles=tuple(args.robust_quantiles)
        )
    except ValueError as exc:
        print(exc)
        return 1
    design = None
    # Folder problems are reported by the run itself; the design is only asked about
    # once the folders can be processed.
    if not has_errors([*pre.discovery.issues, *pre.exclusion_issues]):
        design, message = _design_for_run(pre, args)
        if design is None:
            print(message)
            return 1
    print("Axis roles:")
    print(mapping_summary(mapping, pre.metadata.files))
    print()
    review = review_axes(pre.metadata, mapping, missing_as_singleton=args.missing_as_singleton)
    type_review = review_image_type(pre.metadata, review)
    image_type = args.image_type or type_review.suggested
    print(f"Image type: {image_type}")
    print(_image_type_note(type_review, args.image_type))
    print()
    if design is not None:
        print("Experiment design:")
        print(design_summary(design))
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
        design=design,
        masks_folder=args.masks,
        image_type=image_type,
        intensity_settings=intensity_settings,
        histogram_settings=histogram_settings,
    )
    if result.issues:
        print(format_issues(result.issues))
        print()
    print(run_summary(result))
    if result.intensity is not None:
        print()
        print(intensity_text(result.intensity))
    return 0 if result.written else 1


def _design_for_run(pre, args: argparse.Namespace) -> tuple[ExperimentDesign | None, str]:
    """Return the confirmed experiment design, or None and what the user has to do.

    Without a fully confirmed design, a template (keeping every answer already
    given) is written to the output folder for the user to fill in.
    """
    root = pre.discovery.root
    output = Path(args.output) if args.output else default_output_folder(root)
    source = Path(args.design) if args.design else output / DESIGN_FILENAME
    previous = None
    if source.exists():
        try:
            previous = read_experiment_design(source)
        except DesignFormatError as exc:
            return None, (
                f"The experiment design cannot be read: {exc}\n"
                f"Correct the file, or delete it to get a new template."
            )
    elif args.design:
        return None, f"The experiment design {source} does not exist."
    design, _ = reconcile_design(previous, pre.dataset.repeats)
    # Rows confirmed by hand in the file get today's date.
    design = design.dated(date.today().isoformat())
    problems = design_issues(design)
    if not problems:
        return design, ""
    if check_output_folder(root, output):
        # The run reports the output folder; nothing is written inside the experiment.
        return design, ""
    target = write_experiment_design(design, output / DESIGN_FILENAME)
    lines = [
        format_issues(problems),
        "",
        f"The experiment design was written to {target}. Open it in a spreadsheet program "
        "or a text editor, one row per biological-repeat folder of each condition:",
        "  - repeat_group: rows with the same value are the same biological repeat across "
        "conditions (by default the folder name). Change it to pair repeats whose folders "
        "are named differently; each group may hold one folder per condition.",
        "  - confirmed: set it to true in every row of a repeat group once you are sure its "
        "images were prepared and acquired under the same circumstances, so they are "
        "comparable.",
        "  - control: set it to true in the rows of control conditions (optional; recorded only).",
        "Then run again.",
    ]
    return None, "\n".join(lines)


def _image_type_note(review: ImageTypeReview, given: str | None) -> str:
    """Say where the image type comes from and how to choose the other one."""
    if given is not None:
        return "  given with --image-type"
    other = "fluorescence" if review.suggested == RGB else RGB
    lines = [f"  suggested, because {review.reason}"]
    if other == RGB and not review.rgb_allowed:
        lines.append(f"  rgb is not possible: {review.rgb_problem}")
    else:
        lines.append(f"  Give --image-type {other} if these are {other} images.")
    return "\n".join(lines)


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
