"""The command line's ``convert``: named-axis copies of an experiment (design v0.4 §5).

``convert EXPERIMENT --to FOLDER --axes ROLES`` writes the copy tree with
:func:`.conversion.convert_experiment`: every image with an axis error or unnamed axes as
a named-axis OME-TIFF with the roles given in ``--axes`` (never taken from the headers),
every other image copied as it is, and the conversion record last. It prints what it
wrote and the next commands: ``check`` and ``run`` on the copy tree, with the output
folder of the runs on the originals (``--output``, by default the experiment's default
one). The command is offered for any experiment.

``--project max|mean|sum`` (issue #176) writes every image as a recorded 2-D projection of
its Z-stack instead (no default method; ``--z-range START:STOP`` for the whole experiment).
The projected copies are a different source, so the next commands name a new output folder,
by default ``<--to folder>_MorphoBricks_Preprocessing``, a new lineage of descriptions;
``--output`` and ``--masks`` still say where the runs on the originals recorded their masks,
which are scanned (headers only) for 3-D masks that stop the projection.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .cli_run import mapping_option, parse_size, print_progress
from .conversion import ConversionResult, convert_experiment
from .conversion_record import RECORD_FILENAME
from .discovery import default_output_folder
from .planes import DEFAULT_MEMORY_LIMIT
from .projection import METHODS, Projection
from .report import format_issues
from .run import preflight
from .run.output_guard import recorded_masks_of
from .validation import plural


def add_convert_parser(commands: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Add the ``convert`` command and its options; return its parser.

    Parameters
    ----------
    commands
        The command line's subcommands.

    Returns
    -------
    argparse.ArgumentParser
        The ``convert`` command's parser.
    """
    convert = commands.add_parser(
        "convert",
        help="save the images whose axes are wrong or not named as named-axis OME-TIFF copies "
        "with your roles, in a new folder (the originals are only read)",
    )
    convert.add_argument("experiment", help="the experiment folder")
    convert.add_argument(
        "--to",
        metavar="FOLDER",
        required=True,
        help="the folder for the copies: new or empty, outside the experiment (or the folder "
        "of an earlier conversion of this experiment, whose unchanged copies are kept)",
    )
    convert.add_argument(
        "--axes",
        metavar="ROLES",
        required=True,
        help="the role of each axis of the images, in the order of their axes (X, Y, Z, C, T, "
        "or - for none): the roles the copies are written with",
    )
    convert.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATH",
        help="image to leave unread, relative to the experiment folder; it is copied as it "
        "is (repeatable)",
    )
    convert.add_argument(
        "--missing-as-singleton",
        action="store_true",
        help="confirm that images lacking some axes have one plane along them",
    )
    convert.add_argument(
        "--memory-limit",
        type=parse_size,
        default=DEFAULT_MEMORY_LIMIT,
        metavar="SIZE",
        help="working-memory limit per plane, e.g. 2GiB (default: 1GiB)",
    )
    convert.add_argument(
        "--remove-stale-lock",
        action="store_true",
        help="remove the lock of an earlier conversion into --to that was stopped; give it "
        "only when you are sure no other conversion writes there",
    )
    _add_folder_options(convert)
    _add_projection_options(convert)
    convert.add_argument("--log", metavar="FILE", help="write technical details to this file")
    return convert


def _add_folder_options(convert: argparse.ArgumentParser) -> None:
    """Add ``--output`` and ``--masks``: the folders of the runs on the originals."""
    convert.add_argument(
        "--output",
        metavar="FOLDER",
        help="the output folder of your runs on the originals, named in the next commands "
        "(default: the experiment folder's name with _MorphoBricks_Preprocessing)",
    )
    convert.add_argument(
        "--masks",
        metavar="FOLDER",
        help="the masks folder of your runs, which the copies may not be inside; by default "
        "the one recorded by the run in the output folder (--output)",
    )


def _add_projection_options(convert: argparse.ArgumentParser) -> None:
    """Add ``--project`` and ``--z-range`` (issue #176) to the ``convert`` parser."""
    convert.add_argument(
        "--project",
        nargs="?",
        const="",
        type=_method,
        metavar="METHOD",
        help="write every image as a recorded 2-D projection of its Z-stack: max, mean or "
        "sum (there is no default). Z must be among the roles in --axes. The copies are "
        "named-axis OME-TIFFs without Z; a 16-bit sum is written in 32 bits, a mean in 64-bit "
        "floats, a maximum keeps the type; the Z size is not written but recorded. Masks "
        "are never projected: masks with depth slices stop the projection",
    )
    convert.add_argument(
        "--z-range",
        type=_z_range,
        metavar="START:STOP",
        help="with --project: use only these slices of every image (zero-based, STOP "
        "excluded, for example 2:10; 4:5 is the single slice 4). Every image must have "
        "them. Default: all slices",
    )


def _method(text: str) -> str:
    """Return a projection method, or refuse with the message that names the choices."""
    if text not in METHODS:
        raise argparse.ArgumentTypeError(
            "--project needs a method: max, mean or sum. There is no default: choose the one "
            "that fits your analysis."
        )
    return text


def _z_range(text: str) -> tuple[int, int]:
    """Return ``(start, stop)`` from ``START:STOP``, or refuse with what is expected."""
    first, _, last = text.partition(":")
    try:
        bounds = (int(first), int(last))
        Projection("max", bounds)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--z-range needs START:STOP as whole numbers with 0 <= START < STOP (zero-based, "
            f"STOP excluded, for example 2:10), not {text!r}"
        ) from None
    return bounds


def convert_command(args: argparse.Namespace) -> int:
    """Write the copy tree with the command's options, print what was done; return the status."""
    if args.z_range is not None and args.project is None:
        print("--z-range needs --project: it chooses the slices to project.", file=sys.stderr)
        return 2
    pre = preflight(args.experiment, args.to, exclude=args.exclude)
    mapping = mapping_option(args, pre, None)
    if mapping is None or mapping is False:
        return 1
    given = args.output is not None
    output = (
        Path(args.output).resolve()
        if given
        else default_output_folder(Path(args.experiment).resolve())
    )
    result = convert_experiment(
        args.experiment,
        mapping,
        args.to,
        exclude=args.exclude,
        missing_as_singleton=args.missing_as_singleton,
        memory_limit=args.memory_limit,
        progress=print_progress,
        masks_folder=args.masks or recorded_masks_of(output),
        projection=None if args.project is None else Projection(args.project, args.z_range),
    )
    if result.issues:
        print(format_issues(result.issues))
        print()
    if result.projection is not None:
        output, given = default_output_folder(Path(args.to).resolve()), True
    print(conversion_text(result, output, given=given))
    return 0 if result.written else 1


def conversion_text(result: ConversionResult, output: Path, *, given: bool = True) -> str:
    """Say what a conversion wrote and what to run next, in plain ASCII-safe words.

    Parameters
    ----------
    result
        What :func:`.conversion.convert_experiment` returned.
    output
        The output folder of the runs on the originals, named in the next commands.
    given
        False when ``output`` is the default one, not given with ``--output``: the text then
        says to give the folder of the runs on the originals if it is another one.

    Returns
    -------
    str
    """
    dest = result.destination
    if not result.written:
        return f"No copies were written in {dest}: read the messages above."
    if result.projection is not None:
        return projection_text(result, output)
    # Cross-brick test F8: the next commands name the output folder of the user's runs,
    # given with --output; a default one may hold no run (the user then gives theirs).
    where = "" if given else f" (by default {output}; if your runs used another one, give it)"
    lines = [
        f"Named-axis copies in {dest}:",
        f"  written as OME-TIFF with your roles: {plural(len(result.converted), 'image')}",
        f"  copied as they are: {plural(len(result.copied), 'image')}",
        f"  already there and unchanged: {plural(len(result.reused), 'image')}",
        f"Conversion record: {dest / RECORD_FILENAME} (SHA-256 {result.record_sha256})",
        "",
        "Next, run preprocessing on the copies with the output folder of your runs on the "
        f"originals{where}:",
        f"  morphobricks-preprocessing check {dest} --output {output}",
    ]
    axes = f" --axes {result.suggested_axes}" if result.suggested_axes else " --axes ROLES"
    lines.append(f"  morphobricks-preprocessing run {dest} --output {output}{axes}")
    return "\n".join(lines)


def projection_text(result: ConversionResult, output: Path) -> str:
    """Say what a projection wrote and what to run next (a new output folder, a new source).

    Parameters
    ----------
    result
        What :func:`.conversion.convert_experiment` returned for a projection.
    output
        The output folder for the runs on the projected copies: the projected tree is a
        different source, so its description starts a new lineage.

    Returns
    -------
    str
    """
    dest, projection = result.destination, result.projection
    bounds = projection.z_range
    slices = "all slices" if bounds is None else f"slices {bounds[0]}:{bounds[1]}"
    axes = f" --axes {result.suggested_axes}" if result.suggested_axes else " --axes ROLES"
    return "\n".join(
        [
            f"Projected copies in {dest}:",
            f"  {projection.method} projections of {slices}, written as 2-D OME-TIFF: "
            f"{plural(len(result.projected), 'image')}",
            f"  copied as they are (excluded): {plural(len(result.copied), 'image')}",
            f"  already there and unchanged: {plural(len(result.reused), 'image')}",
            "  The Z size is not written to the copies; the record keeps it, the slices used "
            "and the SHA-256 of every source.",
            f"Conversion record: {dest / RECORD_FILENAME} (SHA-256 {result.record_sha256})",
            "",
            "The projected copies are a different source: run preprocessing on them with a new "
            f"output folder ({output}), which starts a new description; the design and the "
            "pixel sizes are then entered again. Masks are never projected: 2-D masks pair "
            "with the projected images as usual.",
            f"  morphobricks-preprocessing check {dest} --output {output}",
            f"  morphobricks-preprocessing run {dest} --output {output}{axes}",
        ]
    )
