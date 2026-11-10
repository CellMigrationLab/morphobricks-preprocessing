"""Command-line interface: ``check``, ``run``, ``masks``, ``calibrate``, ``describe``, ``convert``.

Every run needs a published experiment description (``morphobricks.toml``) in its output
folder: ``check``, ``run`` and ``masks`` refuse a folder without one (``A_DESCRIPTION_REQUIRED``,
Spec ADR 0002 AR10), naming the fix (the notebook's describe steps; ``describe`` shows what is
still open). ``check`` runs the preflight (folders and image headers, no pixels) and prints
what will be analysed, the problems found and the suggested axis roles; it writes the
report (``discovery_report.json`` and ``.txt``) to the output folder.
``describe`` (:mod:`.cli_describe`) prints the draft experiment description in plain language;
it writes nothing and confirms nothing. ``convert`` is described below.
``run`` (:mod:`.cli_run`) processes the experiment with the axis roles given in ``--axes``,
or those of the earlier run in the same output folder; roles are never taken from metadata
without being stated. It writes the ``experiment_design.csv`` derived from the description.
The image type (fluorescence or RGB) is given with ``--image-type``, or the
suggestion from the image headers is used and printed. After writing, ``run``
prints the intensity checks of the table (warnings only; plots are drawn in the
notebook only).
``masks`` is the optional mask step: it pairs every image with its masks (``--masks``, and the
label images of OME-Zarr images), reads them, and writes ``mask_relationships.json`` with each
mask type and the relationships between them (contract §15).
``calibrate`` (:mod:`.cli_calibrate`) shows or states the pixel sizes in the output folder's
``calibration.csv`` (design v0.4 §3). ``convert`` (:mod:`.cli_convert`) saves the images whose
axes are wrong or not named as named-axis OME-TIFF copies in a new folder (design v0.4 §5).
All exit with status 1 when they report an error (an error blocks processing) and 0
otherwise; argparse exits with 2 on options it cannot read.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from .cli_calibrate import add_calibrate_parser, calibrate_command, calibration_summary
from .cli_convert import add_convert_parser, convert_command
from .cli_describe import add_describe_parser, describe_command
from .cli_masks import add_masks_arguments, masks_command
from .cli_run import (
    add_missing_as_singleton,
    add_run_arguments,
    command_preflight,
    description_refusal,
    missing_as_singleton,
    parse_size,
    recorded_run,
    reused_missing_as_singleton,
    run_command,
    use_recorded_experiment,
)
from .discovery import default_output_folder
from .image_type import review_image_type
from .log_target import log_refusal, run_log_refusal
from .mask_text import mask_summary
from .masks import mask_axes_option
from .report import (
    axis_check_summary,
    balance_summary,
    format_issues,
    label_tree,
    preflight_summary,
)
from .run import (
    OutputLockedError,
    remove_stale_lock,
    write_check_report,
)
from .run.check_step import suggested_review

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
    add_missing_as_singleton(check)

    run = commands.add_parser("run", help="compute or update the histogram table")
    _common(run, recorded=True)
    add_run_arguments(run)

    masks = commands.add_parser(
        "masks",
        help="find every image's masks, describe them and how they relate (reads the masks)",
    )
    _common(masks, recorded=True)
    add_masks_arguments(masks)
    _remove_stale_lock_option(add_calibrate_parser(commands))
    add_describe_parser(commands)
    add_convert_parser(commands)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line; return the exit status."""
    _safe_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "describe":
        return describe_command(args)
    if args.command == "calibrate":
        if args.remove_stale_lock and not _remove_stale_lock(args):
            return 1
        return calibrate_command(args)
    if getattr(args, "no_exclude", False) and args.exclude:
        parser.error("--exclude and --no-exclude cannot be given together")
    if args.command != "convert":
        try:
            args.mask_axes = mask_axes_option(args.mask_axes)
        except ValueError as exc:
            print(exc)
            return 1
    if args.command in ("check", "run", "masks") and (args.output or args.experiment):
        # Every run is described: refused before anything is read (the recorded experiment)
        # or written (the log, the stale lock's removal).
        refusal = description_refusal(args)
        if refusal is not None:
            print(format_issues([refusal]))
            return 1
    if args.command in ("run", "masks") and not use_recorded_experiment(args):
        return 1
    # The log is opened only now, once the arguments are checked, and never inside the
    # experiment, the masks or the copies, or over an output file (issue #99).
    refusal = _log_refusal(args) if getattr(args, "log", None) else None
    if refusal is not None:
        print(refusal)
        return 1
    handler = _attach_log(args.log) if getattr(args, "log", None) else None
    try:
        if args.command == "convert":
            return convert_command(args) if _remove_stale_lock(args) else 1
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
    _remove_stale_lock_option(parser)


def _remove_stale_lock_option(parser: argparse.ArgumentParser) -> None:
    """Add ``--remove-stale-lock``, which every command holding the folder's lock takes."""
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
        help="the axes of a mask type, one role (Y, X, Z or T) per axis as stored, stated "
        "once per type: Cells=TYX for plain stacks of time points whose files do not name "
        "their axes; a 2D mask needs none, and Cells=YX (one 2D mask shared by every plane "
        "of its image) is refused: a mask has its image's time points (repeatable)",
    )


def _remove_stale_lock(args: argparse.Namespace) -> bool:
    """Remove the output folder's lock the user said is stale; False when it is not."""
    if not args.remove_stale_lock:
        return True
    if getattr(args, "to", None):  # 'convert' locks the folder of the copies
        output = Path(args.to).resolve()
    elif args.output:
        output = Path(args.output).resolve()
    elif getattr(args, "experiment", None):  # 'calibrate' may take only --output
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
    refusal = description_refusal(args)
    if refusal is not None:  # every run is described: nothing is read or written
        print(format_issues([refusal]))
        return 1
    # As 'run' reads images lacking some axes (F10); no Z or T size without the axis.
    recorded = recorded_run(args)
    missing = missing_as_singleton(args, recorded)
    pre, review = suggested_review(command_preflight(args), missing_as_singleton=missing)
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
    axis_errors = ()
    if review is not None:
        # The suggested roles are the header's own, so they never contradict it; 'run'
        # checks the roles given with --axes.
        axis_errors = review.axis_errors
        print()
        print(axis_check_summary(review))
        reused = reused_missing_as_singleton(args, recorded)
        if reused:
            print(reused)
        if review.axis_check:
            print()
            print(format_issues(review.axis_check))
        print()
        # Suggested only where an image lacks some of the axes and is read with one plane.
        lacking = any(found.singleton_roles for found in review.file_mappings.values())
        singleton = " --missing-as-singleton" if missing and lacking else ""
        print(
            f"Suggested option for 'run': --axes {pre.suggested_mapping.roles_string}"
            f"{singleton} (axes in the files: {pre.suggested_mapping.axes})"
        )
        type_review = review_image_type(pre.metadata, review)
        print(
            f"Suggested image type: {type_review.suggested}, because {type_review.reason}. "
            "'run' uses it unless you give --image-type."
        )
    path, refusal = write_check_report(pre, args.output, review)
    print()
    print(format_issues([refusal]) if refusal else f"Report written to {path}" if path else "")
    blocked = not pre.ok or axis_errors or refusal is not None
    return 1 if blocked else 0  # every error printed exits with 1, as 'run' does


def _log_refusal(args: argparse.Namespace) -> str | None:
    """Return why ``--log`` may not be written for this command, or None."""
    if args.command == "convert":
        folders = {"experiment folder": args.experiment, "folder of the copies": args.to}
        return log_refusal(args.log, folders)
    experiment = Path(args.experiment).resolve()
    output = Path(args.output) if args.output else default_output_folder(experiment)
    return run_log_refusal(args.log, experiment, output, args.masks)


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
