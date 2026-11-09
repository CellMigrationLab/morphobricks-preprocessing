"""HTML pieces of the *Check and save* screen (presentation only: the words of the review).

The sentences come from :mod:`.description_review`; here they are escaped and laid out, with the
colour chips of *Your channels* and orange for what needs attention.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence

from . import notebook_style as ui
from .description_candidate import CandidateCheck
from .description_reconcile import Reconciliation, UnitFolders
from .description_removal import RemovalQuestion
from .description_review import MemberView, ReplicateView, unit_text
from .draft import OpenQuestion
from .draft_answers import UnitKey
from .notebook_channels_view import ORANGE, RGB_COLOURS, blank_chip, chip, rgb_chip
from .validation import ValidationIssue, plural

SAME_WAY = "Were these prepared and imaged the same way, so they count as one replicate?"
SAME_WAY_HELP = (
    "Look at the folders, their pictures and their channels: answer yes only if the samples of "
    "these folders were prepared and imaged under the same circumstances (the same day, the same "
    "settings). They are then one biological replicate, compared with each other."
)
QUESTION_WORDS = {
    "id_pending": "a short name is missing for {subject} (Conditions or Replicates)",
    "repeat_not_confirmed": "{subject}: answer “same way?” above",
    "confirmed_on_missing": "{subject}: answer “same way?” again above",
    "unit_channels_missing": "the channels of {subject} are not tagged (Tag channels)",
    "unit_channel_untagged": "{subject} is not tagged (Tag channels)",
    "channel_undeclared": "the label {subject} is used but not declared (Stains)",
    "unit_image_type_unanswered": "the image type of {subject} is not answered (Image type)",
    "unit_channels_stale": (
        "the tags of {subject} were saved for another image: tag its channels again (Tag channels)"
    ),
    "unit_channel_count_unknown": (
        "the channels of {subject} are not known from its image: confirm the axis roles "
        "(Axes) and check that its first image can be read"
    ),
    "control_unresolved": "say what {subject} is compared against",
    "legacy_folder_missing": "{subject} is gone: confirm the removal",
}


def comparisons_html(sentences: Sequence[str]) -> str:
    """Return the comparison sentences as a list."""
    items = "".join(f"<li>{html.escape(s)}</li>" for s in sentences)
    return f"<b>What is compared</b><ul style='margin:4px 0 8px'>{items}</ul>"


def _channels(channels: Sequence[str | None], labels: Sequence[str]) -> str:
    return " ".join(
        (rgb_chip(c) if c in RGB_COLOURS else chip(c, labels)) if c else blank_chip("not tagged")
        for c in channels
    )


def member_html(
    member: MemberView, labels: Sequence[str], counts: Mapping[UnitKey, int] | None = None
) -> str:
    """Return one folder of a replicate: where, how many images, and its channels.

    ``counts`` are the channel counts of each unit's image now (as *Check and save* gates the
    save on them): tags saved for another count are not shown as the folder's channels, only
    said to be stale, as the to-do list says (``unit_channels_stale``).
    """
    name = html.escape(f"{member.condition}/{member.source_repeat}")
    lines = [f"<b>{name}</b> <span class='mb-help'>{plural(member.image_count, 'image')}</span>"]
    for unit in member.units:
        where = "" if unit.key[2] == "single" else f"{html.escape(unit.key[2])}: "
        now = None if counts is None else counts.get(unit.key)
        if unit.channels and now is not None and now != len(unit.channels):
            tags = (
                f"<span style='color:{ORANGE}'>{len(unit.channels)} tags saved for another "
                f"image (it has {now} channels now): tag its channels again</span>"
            )
        else:
            tags = _channels(unit.channels, labels) if unit.channels else blank_chip("not tagged")
        lines.append(f"<div class='mb-help'>{where}{tags}</div>")
    return "".join(lines)


def replicate_title(view: ReplicateView) -> str:
    """Return the heading of a replicate: its ID (or folder label) and shown-as name."""
    name = view.repeat_id or f"folder name {view.label}"
    shown = (
        f" <span class='mb-help'>shown as {html.escape(view.display_name)}</span>"
        if (view.display_name)
        else ""
    )
    return f"<b style='font-size:1.05em'>Replicate {html.escape(name)}</b>{shown}"


def missing_html(view: ReplicateView) -> str:
    """Say, in orange, which acquisitions lack a label others have."""
    lines = [
        f"{html.escape(unit_text(key))} has no {html.escape(label)}: an analysis of "
        f"{html.escape(label)} that includes it is refused (A_SCOPE_INCOMPLETE)."
        for key, label in view.missing
    ]
    return "".join(f"<div style='color:{ORANGE}'>{line}</div>" for line in lines)


def names_html(names: Sequence[tuple[str, Sequence[str]]]) -> str:
    """Return the short names, by kind, with the warning that they never change."""
    rows = "".join(
        f"<div><b>{html.escape(title)}:</b> {html.escape(', '.join(ids))}</div>"
        for title, ids in names
    )
    return (
        "<b>Short names</b> <span class='mb-help'>(these never change once saved; the "
        f"<i>shown as</i> names can)</span>{rows or '<div>none yet</div>'}"
    )


def todo_html(questions: Sequence[OpenQuestion], extra: Sequence[str]) -> str:
    """Return what is still to answer before saving, or nothing."""
    lines = [
        QUESTION_WORDS.get(q.code, "{subject}").format(
            subject=q.subject.replace("repeat ", "replicate ", 1).replace(" / single", "")
        )
        for q in questions
    ]
    lines += list(extra)
    if not lines:
        return ""
    return ui.status("warn", f"Still to answer before saving: {len(lines)}.", lines)


def _issue_lines(issues: Sequence[ValidationIssue]) -> list[str]:
    return [f"{html.escape(i.message)} ({html.escape(i.code)})" for i in issues]


def checks_html(check: CandidateCheck | None) -> str:
    """Return what the strict reader, the run's checks and the derived CSV say."""
    if check is None:
        return ""
    if check.refusals:
        return ui.status(
            "error", "The description would be refused:", _issue_lines(check.refusals), raw=True
        )
    parts: list[str] = []
    hetero = check.heterogeneous
    if hetero is not None:
        parts.append(
            ui.status(
                "warn",
                "It can be saved, but this version cannot run it.",
                [html.escape(hetero.message), html.escape(hetero.action)],
                raw=True,
            )
        )
    elif not check.physical_run:
        parts.append(
            ui.status(
                "info", "The images are checked against it once the axis roles are confirmed."
            )
        )
    elif check.run_refusals:
        parts.append(
            ui.status(
                "error",
                "The images contradict it: the run would stop.",
                _issue_lines(check.run_refusals),
                raw=True,
            )
        )
    else:
        parts.append(ui.status("ok", "The images agree with it (headers checked)."))
    changes = check.changes or ("no row changes",)
    details = (
        ui.status("ok", "The strict reader accepts the exact bytes that will be saved.")
        + "<b>experiment_design.csv</b> <span class='mb-help'>(written only when you save, from "
        "the description)</span><ul style='margin:2px 0'>"
        + "".join(f"<li>{html.escape(c)}</li>" for c in changes)
        + "</ul>"
    )
    parts.append(f"<details><summary class='mb-help'>Details</summary>{details}</details>")
    return "".join(parts)


def _folders(unit: UnitFolders) -> str:
    return unit_text((unit.condition, unit.source_repeat, unit.technical))


def changes_html(rec: Reconciliation) -> str:
    """Say in orange what changed in the folders since the description was saved."""
    lines = [f"new: {_folders(u.folders)} · {plural(u.image_count or 0, 'image')}" for u in rec.new]
    lines += [f"removed: {_folders(u.folders)}" for u in rec.removed]
    lines += [
        f"{c.repeat_id} / {c.condition_id}: {c.before} images saved, {c.after} now"
        for c in rec.counts
    ]
    if not lines:
        return ""
    return ui.status(
        "warn",
        "The folders changed since the description was saved.",
        lines,
    )


def removal_text(question: RemovalQuestion) -> str:
    """Return the orange question of one removal (a whole condition, or some of its folders)."""
    folders = ", ".join(_folders(u) for u in question.units)
    what = (
        f"Every folder of {question.condition_id} is gone ({folders}): remove the condition?"
        if question.last_member
        else f"{folders} of {question.condition_id} is gone: remove it from the experiment?"
    )
    refs = (
        f" First change what {', '.join(question.referenced_by)} is compared against."
        if question.last_member and question.referenced_by
        else ""
    )
    return f"<span style='color:{ORANGE}'>{html.escape(what + refs)}</span>"


__all__ = [
    "SAME_WAY",
    "SAME_WAY_HELP",
    "changes_html",
    "checks_html",
    "comparisons_html",
    "member_html",
    "missing_html",
    "names_html",
    "removal_text",
    "replicate_title",
    "todo_html",
]
