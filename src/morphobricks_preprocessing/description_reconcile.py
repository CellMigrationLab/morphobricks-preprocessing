"""Reconcile the published description with the folder as it is now (experiment-v1 section 10).

Fresh discovery is compared with the published file: each imaging unit is **unchanged**, **new**
or **removed**, matched only by the exact source folder labels, never by an ID, a similar name
or a position. The result is a proposal for the user: it never rewrites confirmed semantics.

- A unit that is new beside a confirmed member, a removed unit, a unit whose channel evidence
  (count, header names) no longer fits its declared channel list, and a folder the user
  explicitly said was renamed, reopen the N35 confirmation of that repeat only
  (``confirmed = false`` in the draft). ``confirmed_on`` is copied from the published file, never
  from the clock; only the user's new confirmation sets a new date (:func:`confirm_repeat`).
- A change of the number of images in a unit alone (new FOVs) does not reopen anything: it is
  reported (:class:`CountChange`) and the member's ``image_count`` follows the folder.
- A renamed folder is a removed folder plus a new pending one, never paired automatically. Only a
  :class:`FolderRename` statement of the user ties the new folder to the old entity, and only when
  the old folder is gone and the new one is there.
- A new folder is pending: it joins no repeat, condition or technical repeat without the user,
  and the only proposal is an existing repeat ID equal to the folder label (section 10).

Pure: it compares two models and a plain description of the folders.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum

from .description import Description, Unit
from .description_transition import DATE, repeat_layout


class Status(StrEnum):
    """How a unit stands against the published file."""

    UNCHANGED = "unchanged"
    NEW = "new"
    REMOVED = "removed"


NEW_UNIT = "new_unit"
REMOVED_UNIT = "removed_unit"
CHANNELS_CHANGED = "channels_changed"
RENAMED_FOLDER = "renamed_folder"
REOPENING_REASONS = (NEW_UNIT, REMOVED_UNIT, CHANNELS_CHANGED, RENAMED_FOLDER)

CONDITION, MEMBER, TECHNICAL = "condition", "member", "technical"
RENAME_KINDS = (CONDITION, MEMBER, TECHNICAL)


@dataclass(frozen=True, order=True)
class UnitFolders:
    """An imaging unit by its source folder labels (``single`` for a missing level)."""

    condition: str
    source_repeat: str
    technical: str


@dataclass(frozen=True)
class ObservedUnit:
    """What the folder shows of one unit now.

    ``channel_count`` and ``header_names`` (raw names by C index, None where there is none) are
    None when the headers were not read or do not state them: that is not evidence of a change.
    """

    image_count: int
    channel_count: int | None = None
    header_names: tuple[str | None, ...] | None = None

    def __post_init__(self) -> None:
        if self.image_count < 1:
            raise ValueError("an observed unit has at least one image")


@dataclass(frozen=True)
class FolderRename:
    """The user's explicit statement that a folder was renamed, with the date they made it.

    ``kind`` is ``condition`` (``old``/``new`` are condition folders), ``member`` (source repeat
    folders under the published condition folder ``condition``) or ``technical`` (technical-repeat
    folders). ``confirmed_on`` is a user fact and has no default.
    """

    kind: str
    old: str
    new: str
    confirmed_on: str
    condition: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in RENAME_KINDS:
            raise ValueError(f"a rename is of one of {RENAME_KINDS}, not {self.kind!r}")
        if (self.kind == MEMBER) == (self.condition is None):
            raise ValueError("a member rename names its condition folder, no other kind does")
        if self.old == self.new:
            raise ValueError(f"the folder {self.old!r} is not renamed")
        if not DATE.fullmatch(self.confirmed_on):
            raise ValueError(f"confirmed_on is YYYY-MM-DD, not {self.confirmed_on!r}")
        date.fromisoformat(self.confirmed_on)


class RenameRefused(ValueError):
    """Raised when a rename statement does not match the published file and the folder."""


@dataclass(frozen=True)
class UnitChange:
    """One unit: its folders now (published ones for a removed unit) and what is known of it.

    ``repeat_id``, ``condition_id`` and ``technical_id`` are the published IDs of an unchanged or
    removed unit, None for a new one, which is pending. ``proposed_repeat`` is the only proposal
    for a new unit: the published repeat ID equal to its source repeat label.
    """

    folders: UnitFolders
    status: Status
    repeat_id: str | None = None
    condition_id: str | None = None
    technical_id: str | None = None
    proposed_repeat: str | None = None
    reasons: tuple[str, ...] = ()
    image_count: int | None = None


@dataclass(frozen=True)
class CountChange:
    """A member whose images in the folder differ from the published ``image_count``."""

    repeat_id: str
    condition_id: str
    before: int
    after: int


@dataclass(frozen=True)
class Reconciliation:
    """The classification of every unit, the repeats to reopen and the reported count changes."""

    units: tuple[UnitChange, ...]
    reopened: Mapping[str, tuple[str, ...]]
    counts: tuple[CountChange, ...]
    renames: tuple[FolderRename, ...] = ()

    def with_status(self, status: Status) -> tuple[UnitChange, ...]:
        """Return the units with this status, in folder order."""
        return tuple(unit for unit in self.units if unit.status == status)

    @property
    def unchanged(self) -> tuple[UnitChange, ...]:
        """The units found as published."""
        return self.with_status(Status.UNCHANGED)

    @property
    def new(self) -> tuple[UnitChange, ...]:
        """The units in the folder that the file does not have: pending."""
        return self.with_status(Status.NEW)

    @property
    def removed(self) -> tuple[UnitChange, ...]:
        """The published units with no images in the folder: removed only on confirmation."""
        return self.with_status(Status.REMOVED)


@dataclass(frozen=True)
class PublishedUnit:
    """A published unit with the IDs that hold it."""

    folders: UnitFolders
    repeat_id: str
    condition_id: str
    technical_id: str
    unit: Unit


def published_units(description: Description) -> dict[UnitFolders, PublishedUnit]:
    """Return the published units by source folder labels."""
    found = {}
    for repeat_id, repeat in description.repeats.items():
        for condition_id, member in repeat.members.items():
            folder = description.conditions[condition_id].source_folder
            for tid, unit in member.technical_repeats.items():
                key = UnitFolders(folder, member.source_repeat, unit.source_folder)
                found[key] = PublishedUnit(key, repeat_id, condition_id, tid, unit)
    return found


def renamed_key(key: UnitFolders, rename: FolderRename) -> UnitFolders:
    """Return the unit's folders after one rename statement."""
    if rename.kind == CONDITION and key.condition == rename.old:
        return replace(key, condition=rename.new)
    if rename.kind == TECHNICAL and key.technical == rename.old:
        return replace(key, technical=rename.new)
    if rename.kind == MEMBER and (key.condition, key.source_repeat) == (
        rename.condition,
        rename.old,
    ):
        return replace(key, source_repeat=rename.new)
    return key


def _check_rename(
    rename: FolderRename, published: Iterable[UnitFolders], observed: Iterable[UnitFolders]
) -> None:
    """Refuse a rename whose old folder is not published or still exists, or new one is absent."""
    seen, held = list(observed), list(published)
    moved = {key for key in held if renamed_key(key, rename) != key}
    after = {renamed_key(key, rename) for key in moved}
    if not moved:
        raise RenameRefused(f"no published folder {rename.old!r} to rename ({rename.kind})")
    if after & (set(held) - moved):
        raise RenameRefused(f"{rename.new!r} is already a folder of the published file")
    if any(renamed_key(key, rename) != key for key in seen):
        raise RenameRefused(f"the folder {rename.old!r} is still in the experiment")
    if not any(key in after for key in seen):
        raise RenameRefused(f"the folder {rename.new!r} is not in the experiment")


def _channels_differ(
    published: PublishedUnit, seen: ObservedUnit, description: Description
) -> bool:
    """Return whether the observed channel evidence no longer fits the declared channel list."""
    unit = published.unit
    if seen.channel_count is not None and seen.channel_count != len(unit.channels):
        return True
    if seen.header_names is None or unit.image_type == "rgb":
        return False
    for channel, name in zip(unit.channels, seen.header_names, strict=False):
        known = {_nfc(entry) for entry in description.channels[channel].header_names or ()}
        if name and _nfc(name) not in known:
            return True
    return False


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _member_counts(
    description: Description,
    units: Mapping[UnitFolders, PublishedUnit],
    now: Mapping[UnitFolders, UnitFolders],
    observed: Mapping[UnitFolders, ObservedUnit],
) -> tuple[CountChange, ...]:
    """Return the members whose units are all still in the folder and whose image count differs."""
    out = []
    for repeat_id, repeat in description.repeats.items():
        for condition_id, member in repeat.members.items():
            held = [
                u
                for u in units.values()
                if (u.repeat_id, u.condition_id) == (repeat_id, condition_id)
            ]
            seen = [observed.get(now[u.folders]) for u in held]
            if None in seen:
                continue  # a unit is missing: the member's count is not known until it is settled
            total = sum(item.image_count for item in seen if item is not None)
            if total != member.image_count:
                out.append(CountChange(repeat_id, condition_id, member.image_count, total))
    return tuple(out)


def reconcile(
    published: Description,
    observed: Mapping[UnitFolders, ObservedUnit],
    renames: Iterable[FolderRename] = (),
) -> Reconciliation:
    """Compare the published description with the folder and classify every unit.

    Parameters
    ----------
    published
        The confirmed description on disk.
    observed
        The units found in the folder now, by their exact source folder labels.
    renames
        The user's explicit statements that a folder was renamed; without one a renamed folder is
        a removed unit plus a new one.

    Returns
    -------
    Reconciliation
        Every unit, the repeats whose N35 confirmation reopens and why, and the count changes.

    Raises
    ------
    RenameRefused
        When a statement names a folder that is not published, is still there, or is not found
        under its new name.
    """
    units = published_units(published)
    statements = tuple(renames)
    now = {key: key for key in units}
    for rename in statements:
        _check_rename(rename, now.values(), observed)
        now = {key: renamed_key(current, rename) for key, current in now.items()}
    changes, reasons = [], {key: set() for key in units}
    renamed = {key for key in units if now[key] != key}
    for key, unit in units.items():
        current = now[key]
        seen = observed.get(current)
        if seen is None:
            changes.append(_change(unit, Status.REMOVED, (REMOVED_UNIT,)))
            reasons[key].add(REMOVED_UNIT)
            continue
        found = [RENAMED_FOLDER] * (key in renamed)
        found += [CHANNELS_CHANGED] * _channels_differ(unit, seen, published)
        reasons[key].update(found)
        changes.append(_change(unit, Status.UNCHANGED, tuple(found), current, seen))
    known = set(now.values())
    pending = [key for key in observed if key not in known]
    changes += [_new(key, observed[key], published, now, reasons, units) for key in sorted(pending)]
    reopened = _reopened(units, reasons)
    counts = _member_counts(published, units, now, observed)
    return Reconciliation(
        tuple(sorted(changes, key=lambda c: c.folders)), reopened, counts, statements
    )


def _change(
    unit: PublishedUnit,
    status: Status,
    reasons: tuple[str, ...],
    folders: UnitFolders | None = None,
    seen: ObservedUnit | None = None,
) -> UnitChange:
    return UnitChange(
        folders or unit.folders,
        status,
        unit.repeat_id,
        unit.condition_id,
        unit.technical_id,
        None,
        tuple(sorted(reasons)),
        seen.image_count if seen else None,
    )


def _new(
    key: UnitFolders,
    seen: ObservedUnit,
    published: Description,
    now: Mapping[UnitFolders, UnitFolders],
    reasons: dict[UnitFolders, set[str]],
    units: Mapping[UnitFolders, PublishedUnit],
) -> UnitChange:
    """Return a pending new unit; it reopens the repeat of an existing member it joins."""
    siblings = [
        units[old]
        for old, current in now.items()
        if (current.condition, current.source_repeat) == (key.condition, key.source_repeat)
    ]
    if siblings:  # a new technical-repeat folder inside a confirmed member
        for sibling in siblings:
            reasons[sibling.folders].add(NEW_UNIT)
    proposed = key.source_repeat if key.source_repeat in published.repeats else None
    return UnitChange(key, Status.NEW, proposed_repeat=proposed, image_count=seen.image_count)


def _reopened(
    units: Mapping[UnitFolders, PublishedUnit], reasons: Mapping[UnitFolders, set[str]]
) -> dict[str, tuple[str, ...]]:
    """Return, per repeat ID, the sorted reasons of its units that reopen the confirmation."""
    out: dict[str, set[str]] = {}
    for key, found in reasons.items():
        if found:
            out.setdefault(units[key].repeat_id, set()).update(found)
    return {repeat: tuple(sorted(found)) for repeat, found in sorted(out.items())}


def layout_changed(old: Description, new: Description) -> tuple[str, ...]:
    """Return the repeat IDs in both files whose layout differs (section 10), sorted.

    A candidate that adds, removes or reassigns a member, unit, folder, channel list or image
    type under an existing repeat reopens it, whether the change came from the folder or from
    the user's assignment of a pending unit. The image count is not part of the layout.
    """
    return tuple(
        sorted(
            key
            for key in old.repeats.keys() & new.repeats.keys()
            if repeat_layout(old, key) != repeat_layout(new, key)
        )
    )


def confirm_repeat(description: Description, repeat_id: str, confirmed_on: str) -> Description:
    """Return the description with an open repeat confirmed by the user on ``confirmed_on``.

    The date is the calendar day of the user's action and has no default.

    Raises
    ------
    ValueError
        When the repeat is unknown, is not open (``confirmed`` is already true), or the date is
        not a real ``YYYY-MM-DD`` day.
    """
    if repeat_id not in description.repeats:
        raise ValueError(f"no repeat {repeat_id!r}")
    repeat = description.repeats[repeat_id]
    if repeat.confirmed:
        raise ValueError(f"repeat {repeat_id!r} is not open: it is confirmed already")
    if not DATE.fullmatch(confirmed_on):
        raise ValueError(f"confirmed_on is YYYY-MM-DD, not {confirmed_on!r}")
    date.fromisoformat(confirmed_on)
    repeats = {
        **description.repeats,
        repeat_id: replace(repeat, confirmed=True, confirmed_on=confirmed_on),
    }
    return replace(description, repeats=repeats)


__all__ = [
    "CHANNELS_CHANGED",
    "NEW_UNIT",
    "REMOVED_UNIT",
    "RENAMED_FOLDER",
    "CountChange",
    "FolderRename",
    "ObservedUnit",
    "PublishedUnit",
    "Reconciliation",
    "RenameRefused",
    "Status",
    "UnitChange",
    "UnitFolders",
    "confirm_repeat",
    "layout_changed",
    "published_units",
    "reconcile",
    "renamed_key",
]
