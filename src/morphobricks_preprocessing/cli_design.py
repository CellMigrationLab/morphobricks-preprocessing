"""The command line's ``design``: show, check and change the experiment design.

``design --output FOLDER`` prints the ``experiment_design.csv`` that ``check`` or ``run``
wrote to the output folder, as a table, with the repeat groups still to confirm and any
problem. Its options change it without editing the file: ``--repeat-group
CONDITION/REPEAT=GROUP`` moves a folder into another repeat group, ``--control`` and
``--no-control`` mark conditions, and ``--confirm GROUP`` or ``--confirm-all`` confirm
repeat groups (:func:`.run.design_file.edit_design`, under the output folder's lock).
Nothing is confirmed unless asked.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .design import DESIGN_FILENAME, DesignFormatError, design_issues
from .design_edit import DesignEdit, DesignEditError, parse_repeat_group
from .report import design_table, format_issues
from .run import OutputLockedError
from .run.design_file import edit_design
from .run.lock import lock_issue


def add_design_parser(commands: argparse._SubParsersAction) -> None:
    """Add the ``design`` command and its options.

    Parameters
    ----------
    commands
        The command line's subcommands.
    """
    design = commands.add_parser(
        "design",
        help="show and check the experiment design in the output folder; confirm repeat "
        "groups, mark controls and pair repeats without editing the file",
    )
    design.add_argument(
        "--output",
        metavar="FOLDER",
        required=True,
        help="the output folder holding experiment_design.csv (written by check or run)",
    )
    design.add_argument(
        "--repeat-group",
        action="append",
        default=[],
        metavar="CONDITION/REPEAT=GROUP",
        help="move a biological-repeat folder into a repeat group, to pair repeats named "
        "differently (repeatable; REPEAT is single without repeat folders); the groups it "
        "leaves and joins must be confirmed again",
    )
    design.add_argument(
        "--control", action="append", default=[], metavar="CONDITION", help="mark a control"
    )
    design.add_argument(
        "--no-control",
        action="append",
        default=[],
        metavar="CONDITION",
        help="unmark a control (repeatable)",
    )
    design.add_argument(
        "--confirm",
        action="append",
        default=[],
        metavar="GROUP",
        help="confirm that the repeat group's images were prepared and acquired under the "
        "same circumstances, so they are comparable (repeatable)",
    )
    design.add_argument(
        "--confirm-all",
        action="store_true",
        help="confirm every repeat group not confirmed yet",
    )


def design_command(args: argparse.Namespace) -> int:
    """Change the design as asked, print it and return the exit status."""
    try:
        moves = [parse_repeat_group(text) for text in args.repeat_group]
        if len({key for key, _ in moves}) != len(moves):
            raise DesignEditError("--repeat-group names a folder more than once")
        edit = DesignEdit(
            dict(moves),
            tuple(args.control),
            tuple(args.no_control),
            tuple(args.confirm),
            args.confirm_all,
        )
        design, written = edit_design(args.output, edit)
    except (DesignEditError, DesignFormatError) as exc:
        print(f"The experiment design was not changed: {exc}")
        return 1
    except OutputLockedError as exc:
        print(format_issues([lock_issue(exc)]))
        return 1
    path = Path(args.output).resolve() / DESIGN_FILENAME
    print(f"Experiment design ({path}{', updated' if written else ''}):")
    print(design_table(design))
    problems = [issue for issue in design_issues(design) if issue.code != "design_not_confirmed"]
    if problems:
        print()
        print(format_issues(problems))
    return 0


def design_instructions(path: Path, output: Path) -> list[str]:
    """Say how to fill in a design template: edit the file, or use ``design``.

    Parameters
    ----------
    path
        The template written.
    output
        Its output folder.

    Returns
    -------
    list of str
        The lines to print.
    """
    return [
        f"The experiment design was written to {path}. Open it in a spreadsheet program "
        "or a text editor, one row per biological-repeat folder of each condition:",
        "  - repeat_group: rows with the same value are the same biological repeat across "
        "conditions (by default the folder name). Change it to pair repeats whose folders "
        "are named differently; each group may hold one folder per condition. It must not "
        "be empty or NA.",
        "  - confirmed_on: the date of the confirmation as YYYY-MM-DD, or empty (the run "
        "fills it in).",
        "  - confirmed: set it to true in every row of a repeat group once you are sure its "
        "images were prepared and acquired under the same circumstances, so they are "
        "comparable.",
        "  - control: set it to true in the rows of control conditions (optional; recorded only).",
        "Or, without editing the file: morphobricks-preprocessing design --output "
        f'"{output}" --confirm GROUP (or --confirm-all), --control CONDITION, '
        "--repeat-group CONDITION/REPEAT=GROUP.",
    ]
