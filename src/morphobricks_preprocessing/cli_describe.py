"""The command line's ``describe``: show the experiment description draft (Stage 1, PR 17).

``describe EXPERIMENT [--output FOLDER]`` prints what the folders and the output folder say
about the experiment description (experiment-v1) in plain language: the IDs proposed for
the conditions, technical repeats and biological repeats, the repeats and their folders, the
references, and every question still open. It reads the folder structure and, when it compares
a published description, the image headers (no pixel is read); it writes nothing. A folder
label that is not Unicode NFC is refused (``R_FOLDER_NOT_NFC``, experiment-v1 section 3.3):
nothing is drafted from it.

The draft comes from the existing functions only: it is built from the folders
(:func:`.draft.draft_from_discovery`); no file of the output folder, ``experiment_design.csv``
included, is read to propose or prefill it (experiment-v1 section 7); when ``morphobricks.toml``
is published, the folders and the headers of their images are compared with it
(:func:`.reconcile_observe.observe_folders` with the headers, then
:func:`.description_reconcile.reconcile`),
exactly as the notebook compares them on reopening, so both say the same. This module
adds no rule.

**Nothing is confirmed here.** An ID, a repeat's comparability (N35), a reference and a removal
are the user's answers, given in the notebook; the command never answers for the user and never
reads the clock. ``--publish`` publishes only a draft that has no open question and holds every
decision confirmed; otherwise it exits with status 1 and lists what is missing. In this version
the command line cannot hold answers (the draft file the notebook writes is not part of the
package yet), so ``--publish`` refuses every draft built from the folders and publishes nothing;
for a published description that matches the folders it reports that there is nothing to
publish.

Exit status: 1 for an error (a folder that cannot be described, a description that cannot be
read, a publication refused), else 0, as every command.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .description import Description
from .description_reconcile import (
    CHANNELS_CHANGED,
    NEW_UNIT,
    REMOVED_UNIT,
    RENAMED_FOLDER,
    Reconciliation,
    Status,
    UnitFolders,
    reconcile,
)
from .discovery import DiscoveryResult, default_output_folder, discover_experiment
from .draft import Draft, draft_from_discovery
from .draft_ids import IdKind, id_problems
from .metadata import inspect_images
from .name_issues import non_nfc_folders_issue
from .reconcile_observe import observe_folders
from .report import format_issues
from .run.description_run import load_description
from .run.recorded import normalise_relative_path
from .validation import has_errors

QUESTION_TEXT = {
    "id_pending": "choose the ID of {subject} (accept the proposal or type another)",
    "repeat_not_confirmed": (
        "confirm that the folders of {subject} were prepared and acquired under the same "
        "circumstances, so they are comparable"
    ),
    "confirmed_on_missing": "{subject} is confirmed but has no date: confirm it again",
}
"""What the user must do for each open question of a draft, in plain words."""

NOT_IN_DRAFT = (
    "the channels of each acquisition, the image type and the meaning of the masks are not "
    "part of this draft and are answered in the notebook; the command line cannot publish "
    "without them"
)
REASON_TEXT = {
    NEW_UNIT: "a new folder joined it",
    REMOVED_UNIT: "a folder of it is gone",
    CHANNELS_CHANGED: "the channels in its image headers no longer fit the published ones",
    RENAMED_FOLDER: "a folder of it was renamed",
}


def add_describe_parser(commands: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Add the ``describe`` command and its options; return its parser."""
    describe = commands.add_parser(
        "describe",
        help="show the draft experiment description (proposals, open questions) in plain "
        "language; writes nothing, and confirms nothing",
    )
    describe.add_argument("experiment", help="the experiment folder")
    describe.add_argument(
        "--output",
        metavar="FOLDER",
        help="output folder holding morphobricks.toml "
        "(default: next to the experiment, <name>_MorphoBricks_Preprocessing)",
    )
    describe.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATH",
        help="image to leave out, relative to the experiment folder (repeatable)",
    )
    describe.add_argument(
        "--publish",
        action="store_true",
        help="publish the description only when no question is open and every decision is "
        "confirmed; otherwise exit with status 1 and list what is missing. Nothing is "
        "confirmed for you",
    )
    return describe


def describe_command(args: argparse.Namespace) -> int:
    """Print the draft (or the comparison with the published description); return the status."""
    root = Path(args.experiment).resolve()
    output = Path(args.output).resolve() if args.output else default_output_folder(root)
    excluded = sorted({normalise_relative_path(path) for path in args.exclude})
    discovery = discover_experiment(root, output)
    # A description is drafted from the folders: a label that is not NFC is refused (Spec
    # experiment-v1 section 3.3), never normalized or renamed.
    issues = [*discovery.issues, *non_nfc_folders_issue(discovery.leaves)]
    if has_errors(issues):
        print(format_issues(issues))
        return 1
    if issues:
        print(format_issues(issues), end="\n\n")
    found, problems = load_description(output)
    if problems:
        print(format_issues(problems))
        return 1
    if found is not None:
        lines, blockers = _published(found.description, discovery, excluded, output)
    else:
        draft = draft_from_discovery(discovery, excluded)
        lines, blockers = draft_lines(draft, root), draft_blockers(draft)
    print("\n".join(lines))
    if not args.publish:
        return 0
    print()
    if not blockers:
        print("Nothing to publish: the published description matches the folders.")
        return 0
    print("Not published. What is missing:")
    print("\n".join(f"  - {item}" for item in blockers))
    return 1


def _proposal(kind: IdKind, folder: str, slot_proposed: str | None) -> str:
    """Say what is proposed for a folder, or why nothing is."""
    if slot_proposed is not None:
        return f"proposed ID {slot_proposed!r}"
    why = ", ".join(id_problems(kind, folder))
    return f"no ID can be proposed ({why or 'in use'}): you name it"


def draft_lines(draft: Draft, root: Path) -> list[str]:
    """Return the plain-language report of a draft."""
    out = [
        f"Draft experiment description of {root}",
        "Drawn from the folders alone. Nothing here is confirmed: IDs below are proposals.",
        "",
        "Conditions (folder: proposal)",
    ]
    out += [
        f"  {folder}: {_proposal('conditions', folder, slot.proposed)}"
        for folder, slot in sorted(draft.conditions.items())
    ]
    out += ["", "Technical repeats (folder: proposal)"]
    out += [
        f"  {folder}: {_proposal('technical_repeats', folder, slot.proposed)}"
        for folder, slot in sorted(draft.technical_repeats.items())
    ]
    out += ["", "Biological repeats"]
    for group, repeat in sorted(draft.repeats.items()):
        state = f"confirmed on {repeat.confirmed_on}" if repeat.confirmed else "not confirmed"
        out.append(f"  {group}: {_proposal('repeats', group, repeat.id.proposed)}; {state}")
        out += [
            f"    {m.condition} / {m.source_repeat}: {m.image_count} images" for m in repeat.members
        ]
    if draft.references:
        out += ["", "References (condition is compared against)"]
        out += [f"  {c} -> {r}" for c, r in sorted(draft.references.items())]
    out += _question_lines(draft)
    return out


def _question_lines(draft: Draft) -> list[str]:
    """Return what the user still has to answer, one line each."""
    questions = draft.open_questions()
    if not questions:
        return ["", "No question is open."]
    out = ["", f"{len(questions)} open questions (answered by you, in the notebook):"]
    out += [f"  - {QUESTION_TEXT[q.code].format(subject=q.subject)}" for q in questions]
    return out


def draft_blockers(draft: Draft) -> list[str]:
    """Return everything that stops this draft from being published, in plain words."""
    blockers = [QUESTION_TEXT[q.code].format(subject=q.subject) for q in draft.open_questions()]
    return blockers or [NOT_IN_DRAFT]


def _published(
    published: Description, discovery: DiscoveryResult, excluded: list[str], output: Path
) -> tuple[list[str], list[str]]:
    """Compare the published description with the folders and headers; return report, blockers.

    The headers of every image to analyse are read (no pixel), so a header rewritten under the
    same files is a change here as in the notebook (:func:`.description_reopen.published_state`).
    """
    images = [image for image in discovery.images if image not in excluded]
    headers = inspect_images(discovery.root, images).files
    rec = reconcile(published, observe_folders(discovery, excluded, headers))
    return reconciliation_lines(rec, output), reconciliation_blockers(rec)


def _folders(unit: UnitFolders) -> str:
    return f"{unit.condition} / {unit.source_repeat} / {unit.technical}"


def reconciliation_lines(rec: Reconciliation, output: Path) -> list[str]:
    """Return the plain-language comparison of the published description with the folders."""
    out = [
        f"The published description ({output / 'morphobricks.toml'}) compared with the folders.",
        "The folder structure, the image counts and the channels the image headers state are "
        "compared (no pixel is read).",
        "",
        f"{len(rec.unchanged)} acquisitions as published.",
    ]
    out += [f"  new, no ID yet: {_folders(u.folders)}" for u in rec.new]
    out += [
        f"  headers changed: {_folders(u.folders)} (its image headers no longer fit the "
        "published channels)"
        for u in rec.unchanged
        if CHANNELS_CHANGED in u.reasons
    ]
    out += [f"  gone, kept until you confirm: {_folders(u.folders)}" for u in rec.removed]
    for change in rec.counts:
        out.append(
            f"  {change.repeat_id} / {change.condition_id}: {change.before} images published, "
            f"{change.after} now"
        )
    for repeat, reasons in rec.reopened.items():
        why = ", ".join(REASON_TEXT.get(reason, reason) for reason in reasons)
        out.append(f"  repeat {repeat} must be confirmed again: {why}")
    if not (rec.new or rec.removed or rec.counts or rec.reopened):
        out.append("  No change.")
    return out


def reconciliation_blockers(rec: Reconciliation) -> list[str]:
    """Return what stops a publication from the comparison, in plain words."""
    blockers = [
        f"{_folders(u.folders)} is new: say which repeat, condition and technical repeat it is"
        for u in rec.new
    ]
    blockers += [
        f"{_folders(u.folders)} is gone: confirm that it is removed, or restore it"
        for u in rec.with_status(Status.REMOVED)
    ]
    blockers += [f"repeat {repeat}: confirm it again" for repeat in rec.reopened]
    blockers += [
        f"{c.repeat_id} / {c.condition_id}: the image count changed ({c.before} to {c.after}); "
        "this command cannot publish it"
        for c in rec.counts
    ]
    return blockers
