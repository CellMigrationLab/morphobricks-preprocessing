"""What the *Check and save* screen says about the experiment, in plain sentences (pure).

Built from the draft with the IDs as the user sees them applied and from the answers: what each
condition is compared against (including "compared against nothing", said in words), each
replicate with its folders, image counts and the channel labels of each acquisition (a label some
acquisitions lack is named, since an analysis of it that includes them is refused,
``A_SCOPE_INCOMPLETE``), the short names that never change once saved, and how the derived
``experiment_design.csv`` changes (experiment-v1 section 11). Nothing here decides or confirms
anything: it restates the answers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields

from .design import DesignRow, ExperimentDesign
from .draft import Draft
from .draft_answers import ConditionAnswer, DraftAnswers, RepeatAnswer, UnitKey

SINGLE = "single"


@dataclass(frozen=True)
class UnitView:
    """One acquisition (imaging unit) of a member: its folder and its tagged channels."""

    key: UnitKey
    channels: tuple[str | None, ...] = ()


@dataclass(frozen=True)
class MemberView:
    """One folder of a replicate: condition, folder name, images and acquisitions."""

    condition: str
    condition_id: str | None
    source_repeat: str
    image_count: int
    units: tuple[UnitView, ...]


@dataclass(frozen=True)
class ReplicateView:
    """One replicate (biological repeat) as the screen shows it."""

    label: str
    repeat_id: str | None
    display_name: str | None
    members: tuple[MemberView, ...]
    channels: tuple[str, ...]
    missing: tuple[tuple[UnitKey, str], ...]
    confirmed_on: str | None


def _named(identifier: str | None, folder: str, shown: str | None) -> str:
    """Return ``ID (shown-as)``, the ID alone, or the folder when no ID is known yet."""
    name = identifier or f"folder {folder}"
    return f"{name} ({shown})" if shown else name


def comparisons(applied: Draft, answers: DraftAnswers) -> list[str]:
    """Return one sentence per condition: what it is compared against, or that it is not."""
    out = []
    for folder, slot in sorted(applied.conditions.items(), key=lambda i: i[1].confirmed or i[0]):
        answer = answers.conditions.get(folder, ConditionAnswer())
        name = _named(slot.confirmed, folder, answer.display_name)
        target = applied.references.get(folder)
        if target:
            other = answers.conditions.get(target, ConditionAnswer())
            against = _named(applied.conditions[target].confirmed, target, other.display_name)
            out.append(f"{name} is compared against {against}.")
        elif answer.reference == "":
            out.append(f"{name} is compared against nothing.")
        else:
            out.append(f"{name}: not answered yet (what is it compared against?).")
    return out


def _members(applied: Draft, label: str, answers: DraftAnswers) -> tuple[MemberView, ...]:
    """Return the folders of one replicate with their acquisitions and tagged channels."""
    out = []
    for member in applied.repeats[label].members:
        units = tuple(
            UnitView(
                key := (member.condition, member.source_repeat, unit.source_folder),
                tuple(answers.units[key].channels) if key in answers.units else (),
            )
            for unit in member.units
        )
        cid = applied.conditions[member.condition].confirmed
        out.append(
            MemberView(member.condition, cid, member.source_repeat, member.image_count, units)
        )
    return tuple(out)


def replicates(applied: Draft, answers: DraftAnswers) -> tuple[ReplicateView, ...]:
    """Return every replicate with its folders, their channels and the labels some lack."""
    every = sorted({c for unit in answers.units.values() for c in unit.channels if c is not None})
    out = []
    for label, repeat in sorted(applied.repeats.items()):
        members = _members(applied, label, answers)
        units = [u for m in members for u in m.units]
        present = sorted({c for u in units for c in u.channels if c is not None})
        missing = tuple(
            (u.key, label_id)
            for u in units
            if u.channels
            for label_id in every
            if label_id not in u.channels
        )
        answer = answers.repeats.get(label, RepeatAnswer())
        out.append(
            ReplicateView(
                label,
                repeat.id.confirmed,
                answer.display_name,
                members,
                tuple(present),
                missing,
                answer.confirmed_on,
            )
        )
    return tuple(out)


def short_names(applied: Draft, answers: DraftAnswers) -> list[tuple[str, tuple[str, ...]]]:
    """Return the IDs that never change once saved, by kind (unconfirmed ones left out)."""
    technical = {s.confirmed for s in applied.technical_repeats.values()} - {SINGLE, None}
    kinds = [
        ("Conditions", {s.confirmed for s in applied.conditions.values()} - {None}),
        ("Replicates", {r.id.confirmed for r in applied.repeats.values()} - {None}),
        ("Technical repeats", technical),
        ("Channels", set(answers.channels)),
        ("Masks", set(answers.masks)),
    ]
    return [(title, tuple(sorted(ids))) for title, ids in kinds if ids]


def unit_text(key: UnitKey) -> str:
    """Return ``condition/repeat/technical repeat`` of a unit, ``single`` left out."""
    return "/".join(part for part in key if part != SINGLE)


def _row_text(row: DesignRow) -> str:
    return f"{row.condition}/{row.biological_repeat}"


def _differences(old: DesignRow, new: DesignRow) -> list[str]:
    """Name each column of a row that changes, with its two values."""
    return [
        f"{f.name} {getattr(old, f.name)!s} → {getattr(new, f.name)!s}"
        for f in fields(DesignRow)
        if getattr(old, f.name) != getattr(new, f.name)
    ]


def design_changes(before: ExperimentDesign | None, after: ExperimentDesign) -> list[str]:
    """Return how ``experiment_design.csv`` changes, one line per row added, removed or changed.

    ``before`` is the file as it is now (None when there is none). An empty list means the file
    is written with the same rows.
    """
    new: Mapping[tuple[str, str], DesignRow] = {r.key: r for r in after.rows}
    if before is None:
        return [f"written for the first time ({len(new)} rows)"]
    old: Mapping[tuple[str, str], DesignRow] = {r.key: r for r in before.rows}
    out = [f"new row {_row_text(new[k])}" for k in sorted(set(new) - set(old))]
    out += [f"row removed {_row_text(old[k])}" for k in sorted(set(old) - set(new))]
    for key in sorted(set(old) & set(new)):
        changed = _differences(old[key], new[key])
        if changed:
            out.append(f"{_row_text(new[key])}: {', '.join(changed)}")
    return out


__all__ = [
    "MemberView",
    "ReplicateView",
    "UnitView",
    "comparisons",
    "design_changes",
    "replicates",
    "short_names",
    "unit_text",
]
