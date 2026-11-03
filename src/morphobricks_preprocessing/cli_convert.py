"""The command line's ``convert``: named-axis copies of an experiment (design v0.4 §5).

``convert EXPERIMENT --to FOLDER --axes ROLES`` writes the copy tree with
:func:`.conversion.convert_experiment`: every image with an axis error or unnamed axes as
a named-axis OME-TIFF with the roles given in ``--axes`` (never taken from the headers),
every other image copied as it is, and the conversion record last. It prints what it
wrote and the next commands: ``check`` and ``run`` on the copy tree, with the output
folder of the runs on the originals (``--output``, by default the experiment's default
one). The command is offered for any experiment.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .cli_run import mapping_option, parse_size, print_progress
from .conversion import ConversionResult, convert_experiment
from .conversion_record import RECORD_FILENAME
from .discovery import default_output_folder
from .planes import DEFAULT_MEMORY_LIMIT
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
    convert.add_argument("--log", metavar="FILE", help="write technical details to this file")
    return convert


def convert_command(args: argparse.Namespace) -> int:
    """Write the copy tree with the command's options, print what was done; return the status."""
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
    )
    if result.issues:
        print(format_issues(result.issues))
        print()
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
