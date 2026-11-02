"""The command line's ``calibrate``: state pixel sizes in ``calibration.csv`` (design v0.4 §3).

``calibrate`` shows the statements of the output folder's ``calibration.csv`` and, with
``--size``, ``--pixels`` or ``--remove``, changes them and writes the file (holding the
folder's lock). Each statement names its target (``.`` for the whole experiment, or a folder
or image relative to the experiment folder) and the axes it sizes; each axis is stated on its
own, so nothing exchanges the sizes of Y and X. ``run`` reads the file, records every image's
sizes with their source, and names the change in its report; no row is recomputed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .calibration import (
    CALIBRATION_AXES,
    CALIBRATION_FILENAME,
    Calibration,
    CalibrationEntry,
    calibration_entry,
)
from .calibration_io import CalibrationFormatError, read_calibration
from .discovery import default_output_folder
from .recorded_sizes import PIXELS
from .report import format_issues
from .run import OutputLockedError, write_calibration
from .run.lock import lock_issue


def add_calibrate_parser(commands: argparse._SubParsersAction) -> None:
    """Add the ``calibrate`` command and its options."""
    parser = commands.add_parser(
        "calibrate",
        help="show or state the pixel sizes in calibration.csv in the output folder",
    )
    parser.add_argument(
        "experiment",
        nargs="?",
        help="the experiment folder, to find its default output folder (or give --output)",
    )
    parser.add_argument("--output", metavar="FOLDER", help="the output folder")
    parser.add_argument(
        "--size",
        nargs=4,
        action="append",
        default=[],
        metavar=("TARGET", "AXES", "SIZE", "UNIT"),
        help="state a size: TARGET is . for the whole experiment, or a folder or image "
        "relative to the experiment folder; AXES are X, Y and/or Z (XY: the same size for "
        "both); UNIT is a length unit such as um or nm (repeatable)",
    )
    parser.add_argument(
        "--pixels",
        nargs=2,
        action="append",
        default=[],
        metavar=("TARGET", "AXES"),
        help="state that these axes are measured in pixels: deliberately uncalibrated (repeatable)",
    )
    parser.add_argument(
        "--remove",
        nargs=2,
        action="append",
        default=[],
        metavar=("TARGET", "AXES"),
        help="remove the statements for TARGET along AXES; the image metadata are used "
        "again (repeatable)",
    )


def calibrate_command(args: argparse.Namespace) -> int:
    """Show the statements, or change and write them; return the exit status."""
    if args.output:
        output = Path(args.output).resolve()
    elif args.experiment:
        output = default_output_folder(Path(args.experiment).resolve())
    else:
        print("Give the experiment folder or --output to say whose calibration to show.")
        return 1
    path = output / CALIBRATION_FILENAME
    try:
        current = read_calibration(path)[0] if path.is_file() else Calibration()
        changed = _changed(current, args)
    except (CalibrationFormatError, OSError, ValueError) as exc:
        print(f"Nothing was changed: {exc}")
        return 1
    if changed != current:
        try:
            path = write_calibration(changed, output)
        except OutputLockedError as exc:
            print(format_issues([lock_issue(exc)]))
            return 1
        print(f"Written to {path}. The next run records these sizes; no row is recomputed.")
    print(calibration_summary(changed))
    return 0


def _changed(calibration: Calibration, args: argparse.Namespace) -> Calibration:
    """Apply the removals, then the statements given, in the order given."""
    for target, axes in args.remove:
        calibration = calibration.without(target, _axes(axes))
    stated: list[CalibrationEntry] = []
    for target, axes, size, unit in args.size:
        if unit == PIXELS:
            raise ValueError("give --pixels TARGET AXES to state that axes are in pixels")
        stated.extend(calibration_entry(target, axis, size, unit) for axis in _axes(axes))
    for target, axes in args.pixels:
        stated.extend(calibration_entry(target, axis, None, PIXELS) for axis in _axes(axes))
    return calibration.stating(stated)


def _axes(text: str) -> tuple[str, ...]:
    """Return the axes of ``XY``, ``Z``, ...: X, Y and Z, each at most once."""
    if not text or any(axis not in CALIBRATION_AXES for axis in text) or len(set(text)) < len(text):
        raise ValueError(f"the axes {text!r} must be X, Y and/or Z, each at most once")
    return tuple(text)


def calibration_summary(calibration: Calibration) -> str:
    """Describe the statements, one per line, or say there are none."""
    if not calibration.entries:
        return "No pixel size is stated: every image uses the sizes in its metadata."
    lines = ["Stated pixel sizes (calibration.csv):"]
    lines.extend(
        f"  {entry.target} {entry.axis}: {entry.describe()}" for entry in calibration.entries
    )
    return "\n".join(lines)
