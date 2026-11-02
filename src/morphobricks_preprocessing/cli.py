"""Command-line interface: ``morphobricks-preprocessing check``, ``run``, ``masks``, ``calibrate``.

``check`` runs the preflight (folders and image headers, no pixels) and prints
what will be analysed, the problems found and the suggested axis roles; it writes only
the report (``discovery_report.json`` and ``.txt``) to the output folder.
``run`` (:mod:`.cli_run`) processes the experiment with the axis roles given in ``--axes``,
or those of the earlier run in the same output folder; roles are never taken from metadata
without being stated. It also needs the
experiment design confirmed in ``experiment_design.csv``: without it, ``run``
writes a template to the output folder for the user to fill in, and stops.
The image type (fluorescence or RGB) is given with ``--image-type``, or the
suggestion from the image headers is used and printed. After writing, ``run``
prints the intensity checks of the table (warnings only; plots are drawn in the
notebook only).
``masks`` is the optional mask step: it pairs every image with its masks (``--masks``, and the
label images of OME-Zarr images), reads them, and writes ``mask_relationships.json`` with each
mask type and the relationships between them (contract §15).
``calibrate`` (:mod:`.cli_calibrate`) shows or states the pixel sizes in the output folder's
``calibration.csv`` (design v0.4 §3). All four exit with status 1 when an error blocks
processing.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from .axis_review import review_axes
from .cli_calibrate import add_calibrate_parser, calibrate_command, calibration_summary
from .cli_masks import add_masks_arguments, masks_command
from .cli_run import add_run_arguments, command_preflight, parse_size, run_command
from .design import (
    DESIGN_FILENAME,
    DesignCheckError,
    load_design,
)
from .discovery import default_output_folder
from .image_type import review_image_type
from .masks import mask_axes_option
from .report import (
    axis_check_summary,
    balance_summary,
    design_summary,
    format_issues,
    label_tree,
    mask_summary,
    preflight_summary,
)
from .run import (
    OutputLockedError,
    Preflight,
    remove_stale_lock,
    write_check_report,
)
from .run.preflight import fit_calibration

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
        "check",
        help="check the folder and image headers, suggest axis roles and write the report",
    )
    _common(check)

    run = commands.add_parser("run", help="compute or update the histogram table")
    _common(run, recorded=True)
    add_run_arguments(run)

    masks = commands.add_parser(
        "masks",
        help="find every image's masks, describe them and how they relate (reads the masks)",
    )
    _common(masks, recorded=True)
    add_masks_arguments(masks)
    add_calibrate_parser(commands)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line; return the exit status."""
    _safe_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "calibrate":
        return calibrate_command(args)
    if getattr(args, "no_exclude", False) and args.exclude:
        parser.error("--exclude and --no-exclude cannot be given together")
    handler = _attach_log(args.log) if getattr(args, "log", None) else None
    try:
        try:
            args.mask_axes = mask_axes_option(args.mask_axes)
        except ValueError as exc:
            print(exc)
            return 1
        if args.remove_stale_lock and not _remove_stale_lock(args):
            return 1
        return {"check": _check, "run": run_command, "masks": masks_command}[args.command](args)
    finally:
        if handler is not None:
            logging.getLogger("morphobricks_preprocessing").removeHandler(handler)
            handler.close()


def _common(parser: argparse.ArgumentParser, *, recorded: bool = False) -> None:
    """Add the experiment and the options every command shares.

    With ``recorded``, the experiment may be left out: ``run`` and ``masks`` then use the
    one the run in ``--output`` recorded (design v0.4 §1).
    """
    if recorded:
        parser.add_argument(
            "experiment",
            nargs="?",
            help="the experiment folder (default: the one the run in --output recorded)",
        )
    else:
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
    parser.add_argument(
        "--remove-stale-lock",
        action="store_true",
        help="remove the lock of an earlier run that was stopped or whose computer restarted; "
        "give it only when you are sure no other run uses the output folder",
    )


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


def _remove_stale_lock(args: argparse.Namespace) -> bool:
    """Remove the output folder's lock the user said is stale; False when it is not."""
    if args.output:
        output = Path(args.output).resolve()
    elif args.experiment:
        output = default_output_folder(Path(args.experiment).resolve())
    else:
        print("Give the experiment folder or --output to say whose lock to remove.")
        return False
    try:
        holder = remove_stale_lock(output)
    except OutputLockedError as exc:
        print(f"The lock was kept: {exc.holder.describe()} is still running on this computer.")
        return False
    print("No lock to remove." if holder is None else f"Removed the lock of {holder.describe()}.")
    return True


def _check(args: argparse.Namespace) -> int:
    """Print what the preflight found and the suggested roles, and write the report."""
    pre = command_preflight(args)
    review = None
    if pre.suggested_mapping is not None:
        review = review_axes(pre.metadata, pre.suggested_mapping)
        pre, _ = fit_calibration(pre, review)  # no Z or T size for images without one
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
    if pre.calibration.calibration is not None:
        print()
        print(calibration_summary(pre.calibration.calibration))
    if pre.issues:
        print()
        print(format_issues(pre.issues))
    if pre.dataset.repeats:
        _print_design(args, pre)
    axis_errors = ()
    if review is not None:
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
    path, refusal = write_check_report(pre, args.output, review)
    print()
    print(format_issues([refusal]) if refusal else f"Report written to {path}" if path else "")
    return 0 if pre.ok and not axis_errors and refusal is None else 1


def _print_design(args: argparse.Namespace, pre: Preflight) -> None:
    """Print the experiment design in the output folder; ``check`` never writes it."""
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
