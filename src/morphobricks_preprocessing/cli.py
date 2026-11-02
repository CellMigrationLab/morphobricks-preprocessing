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
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from morphobricks_normalization.ranges import (
    DEFAULT_ROBUST_QUANTILES,
    HISTOGRAM_RANGES,
)

from .axis_review import review_axes
from .cli_run import command_preflight, parse_size, run_command
from .design import (
    DESIGN_FILENAME,
    DesignCheckError,
    load_design,
)
from .discovery import default_output_folder
from .image_type import IMAGE_TYPES, review_image_type
from .mask_scan import (
    DEFAULT_THRESHOLDS,
    MASK_REPORT_FILENAME,
    mask_thresholds,
    scan_masks,
    write_mask_report,
)
from .masks import MaskInventory, mask_axes_option
from .planes import DEFAULT_MEMORY_LIMIT
from .report import (
    axis_check_summary,
    balance_summary,
    design_summary,
    format_issues,
    label_tree,
    mask_report_summary,
    mask_summary,
    preflight_summary,
)
from .validation import has_errors

__all__ = ["build_parser", "main", "parse_size"]


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
        try:
            args.mask_axes = mask_axes_option(args.mask_axes)
        except ValueError as exc:
            print(exc)
            return 1
        return {"check": _check, "run": run_command, "masks": _masks}[args.command](args)
    finally:
        if handler is not None:
            logging.getLogger("morphobricks_preprocessing").removeHandler(handler)
            handler.close()


def _common(parser: argparse.ArgumentParser) -> None:
    """Add the experiment and the options every command shares."""
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
    _mask_axes_option(parser)


def _mask_axes_option(parser: argparse.ArgumentParser) -> None:
    """Add ``--mask-axes TYPE=ROLES``, stated once per mask type (design v0.4 §3)."""
    parser.add_argument(
        "--mask-axes",
        action="append",
        default=[],
        metavar="TYPE=ROLES",
        help="the axes of a mask type whose files do not name them, one role (Y, X, Z or T) "
        "per axis as stored: Cells=TYX for plain stacks of time points, Cells=YX for one 2D "
        "mask shared by every plane of its image (repeatable)",
    )


def _check(args: argparse.Namespace) -> int:
    pre = command_preflight(args)
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
        try:
            design, _ = load_design(output, pre.dataset.repeats)
        except DesignCheckError as exc:
            # Refused and left as it is: 'check' never writes the design.
            design, refusal = None, exc.issues[0]
        print()
        if design is None:
            print(f"Experiment design: {refusal.message}")
            print(f"  Suggested action: {refusal.action}")
        elif (output / DESIGN_FILENAME).exists():
            print(f"Experiment design ({output / DESIGN_FILENAME}):")
            print(design_summary(design))
        else:
            print(
                "Experiment design: not confirmed yet. 'run' writes experiment_design.csv to "
                "the output folder for you to confirm."
            )
    axis_errors = ()
    if pre.suggested_mapping is not None:
        review = review_axes(pre.metadata, pre.suggested_mapping)
        # The suggested roles are the header's own, so they never contradict it; 'run'
        # checks the roles given with --axes.
        axis_errors = review.axis_errors
        print()
        print(axis_check_summary(review))
        if review.axis_check:
            print()
            print(format_issues(review.axis_check))
        print()
        print(
            f"Suggested option for 'run': --axes {pre.suggested_mapping.roles_string} "
            f"(axes in the files: {pre.suggested_mapping.axes})"
        )
        type_review = review_image_type(pre.metadata, review)
        print(
            f"Suggested image type: {type_review.suggested}, because {type_review.reason}. "
            "'run' uses it unless you give --image-type."
        )
    return 0 if pre.ok and not axis_errors else 1


def _masks(args: argparse.Namespace) -> int:
    changes = {name: getattr(args, name) for name in _THRESHOLD_HELP}
    try:
        thresholds = mask_thresholds({k: v for k, v in changes.items() if v is not None})
    except ValueError as exc:
        print(exc)
        return 1
    pre = command_preflight(args)
    pre = replace(pre, masks=pre.masks or MaskInventory(folder=None))  # none found
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
    except ValueError as exc:  # masks whose pixels cannot be used (planes, labels)
        print(f"The masks cannot be used: {exc}")
        return 1
    output = Path(args.output) if args.output else default_output_folder(pre.discovery.root)
    path = write_mask_report(report, output / MASK_REPORT_FILENAME)
    print()
    print(mask_report_summary(report))
    print()
    print(f"Report written to {path}")
    return 0


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
