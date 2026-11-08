"""The draft of an experiment description, and the migration of a legacy ``experiment_design.csv``.

A draft is what discovery can say before the user has answered (experiment-v1 section 12): the
folders found, the IDs proposed for them, the pairing of biological repeats, and what is still
open. It is **not** a description and is never read as one: an ID is *proposed* until the user
confirms it, ``confirmed_on`` is a date the user (or a legacy file) gave, never a clock read,
and a draft with an open question cannot be published. Nothing here writes a file, reads an
image or reads the clock.

Source folders keep their labels in every key (section 3.4): a draft names conditions and
technical repeats by their source folder, the IDs are slots beside them, and ``unit_rows`` gives
the added ``condition_id``, ``repeat_group`` and ``technical_repeat_id`` next to the unchanged
folder labels.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from morphobricks_normalization.records import SINGLE

from .description_identity import nfc
from .description_read import PHASES
from .description_report import DescriptionIssue
from .description_rules import SCHEMA_ID
from .design import ExperimentDesign, design_issues, parse_design_bytes
from .discovery import DiscoveryResult
from .draft_ids import IdKind, id_problems, propose_id

PLACEHOLDER_DATE = "2000-01-01"
"""Stands in for a missing date in the rule-check document only; it never leaves that function."""


class DraftError(ValueError):
    """Raised when a draft step is refused; ``codes`` are the reasons (Spec codes if any)."""

    def __init__(self, message: str, codes: tuple[str, ...] = ()) -> None:
        self.codes = codes
        super().__init__(message)


class LegacyMigrationRefused(DraftError):
    """Raised when a legacy design violates experiment-v1: refused, never repaired (section 7).

    ``issues`` are the reader's issues (code, table path, value) of the first failing phase;
    the paths use stand-in condition IDs where the folder is not an allowed ID.
    """

    def __init__(self, message: str, issues: tuple[DescriptionIssue, ...] = ()) -> None:
        self.issues = issues
        codes = tuple(dict.fromkeys(issue.code.value for issue in issues)) or (
            "repeat_group_conflict",
        )
        super().__init__(message, codes)


@dataclass(frozen=True)
class IdSlot:
    """An ID that may be proposed and is adopted only when the user confirms one.

    ``proposed`` is the folder label when it is allowed (None: no proposal, the user names
    it). ``confirmed`` is None until the user confirmed an ID, the proposed one or another.
    """

    proposed: str | None = None
    confirmed: str | None = None

    @property
    def pending(self) -> bool:
        """True while no ID is confirmed."""
        return self.confirmed is None


@dataclass(frozen=True)
class DraftUnit:
    """One imaging unit (a technical-repeat folder, or ``single``) of a member."""

    source_folder: str
    image_count: int


@dataclass(frozen=True)
class DraftMember:
    """One biological-repeat folder of one condition, with its units."""

    condition: str
    source_repeat: str
    image_count: int
    units: tuple[DraftUnit, ...]

    @property
    def key(self) -> tuple[str, str]:
        """``(condition folder, source repeat)``."""
        return (self.condition, self.source_repeat)


@dataclass(frozen=True)
class DraftRepeat:
    """One biological repeat: its ID slot, its member folders and the user's confirmation.

    ``confirmed`` is the N35 answer, ``confirmed_on`` its date (``""`` while none is known).
    """

    id: IdSlot
    members: tuple[DraftMember, ...]
    confirmed: bool = False
    confirmed_on: str = ""


@dataclass(frozen=True)
class MigrationNote:
    """The original per-folder dates of a legacy group that had several (AQ12)."""

    repeat_group: str
    dates: tuple[tuple[str, str, str], ...]
    """``(condition folder, source repeat, date)``, ``""`` for a folder with no date."""
    kept: str
    """The date the draft carries: the latest of them, never the clock."""


@dataclass(frozen=True)
class OpenQuestion:
    """Something the user must answer before the draft can be published."""

    code: str
    subject: str


@dataclass(frozen=True)
class UnitRow:
    """The key of one unit with the canonical IDs added beside it (section 3.4).

    ``condition``, ``biological_repeat`` and ``technical_repeat`` stay the source folder
    labels; ``condition_id``, ``repeat_group`` and ``technical_repeat_id`` are the confirmed IDs,
    None while not confirmed.
    """

    condition: str
    biological_repeat: str
    technical_repeat: str
    condition_id: str | None
    repeat_group: str | None
    technical_repeat_id: str | None
    image_count: int


@dataclass(frozen=True)
class Draft:
    """A draft description: IDs beside the source folders, and the open questions.

    ``conditions`` and ``technical_repeats`` are keyed by source folder (one folder, one ID);
    ``repeats`` by the pairing label (the folder name for the default pairing, the legacy
    ``repeat_group`` for a migration). ``references`` maps a condition folder to the folder of
    the condition it is compared against; ``unresolved_controls`` lists the folders a legacy
    design marked as controls when there were two or more (no name-stem guessing), and
    ``legacy_missing`` the legacy folders no longer found. ``origin`` is ``"discovery"`` or
    ``"legacy"``.
    """

    conditions: Mapping[str, IdSlot]
    technical_repeats: Mapping[str, IdSlot]
    repeats: Mapping[str, DraftRepeat]
    references: Mapping[str, str] = field(default_factory=dict)
    unresolved_controls: tuple[str, ...] = ()
    legacy_missing: tuple[tuple[str, str], ...] = ()
    notes: tuple[MigrationNote, ...] = ()
    used_ids: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    origin: str = "discovery"

    def open_questions(self) -> tuple[OpenQuestion, ...]:
        """Return every question still open; a draft with any cannot be published."""
        out = [
            OpenQuestion("id_pending", f"condition {folder}")
            for folder, slot in sorted(self.conditions.items())
            if slot.pending
        ]
        out += [
            OpenQuestion("id_pending", f"technical repeat {folder}")
            for folder, slot in sorted(self.technical_repeats.items())
            if slot.pending
        ]
        for group, repeat in sorted(self.repeats.items()):
            if repeat.id.pending:
                out.append(OpenQuestion("id_pending", f"repeat {group}"))
            if not repeat.confirmed:
                out.append(OpenQuestion("repeat_not_confirmed", f"repeat {group}"))
            elif not repeat.confirmed_on:
                out.append(OpenQuestion("confirmed_on_missing", f"repeat {group}"))
        out += [OpenQuestion("control_unresolved", folder) for folder in self.unresolved_controls]
        out += [OpenQuestion("legacy_folder_missing", f"{c} / {r}") for c, r in self.legacy_missing]
        return tuple(out)

    @property
    def publishable(self) -> bool:
        """True when no question is open. Publication still validates and asks the user."""
        return not self.open_questions()

    def confirm_id(self, kind: IdKind, key: str, value: str) -> Draft:
        """Return the draft with the user's ID for a condition, technical-repeat folder or repeat.

        ``value`` is what the user accepted or typed, adopted exactly as given. It is refused
        with the codes of the reader's rules when the reader would refuse it as an ID, or when
        it is already used by another entity of this draft or by ``used_ids``.

        Raises
        ------
        DraftError
            With the codes, or ``unknown_key`` when the draft has no such folder or repeat.
        """
        table = {
            "conditions": self.conditions,
            "technical_repeats": self.technical_repeats,
            "repeats": {group: repeat.id for group, repeat in self.repeats.items()},
        }.get(kind)
        if table is None or key not in table:
            raise DraftError(f"the draft has no {kind} entry {key!r}", ("unknown_key",))
        codes = list(id_problems(kind, value, self.used_ids.get(kind, ())))
        for name, slot in table.items():  # another entry of this draft already has the ID
            if name != key and slot.confirmed is not None:
                if slot.confirmed == value:
                    codes.append("id_in_use_in_draft")
                elif nfc(slot.confirmed).casefold() == nfc(value).casefold():
                    codes.append("E_ID_CASE_COLLISION")
        if kind in ("conditions", "technical_repeats") and (value == SINGLE) != (key == SINGLE):
            codes.append("E_SINGLE_MISUSE")
        if codes:
            raise DraftError(
                f"{value!r} cannot be the ID of {kind} {key!r}: {', '.join(codes)}",
                tuple(dict.fromkeys(codes)),
            )
        if kind == "repeats":
            repeats = dict(self.repeats)
            repeats[key] = replace(repeats[key], id=replace(repeats[key].id, confirmed=value))
            return replace(self, repeats=MappingProxyType(repeats))
        slots = dict(table)
        slots[key] = replace(slots[key], confirmed=value)
        return replace(self, **{kind: MappingProxyType(slots)})

    def paired(self, pairing: Mapping[tuple[str, str], str]) -> Draft:
        """Return the draft with folders moved into the repeats the user paired them with.

        ``pairing`` maps ``(condition folder, source repeat folder)`` to the pairing label of
        the repeat the folder joins (the folder name of the other condition's folder, or a
        label the user chose); a folder not in it stays in its repeat. Repeat IDs are proposed
        again for the new repeats. Pairing is an answer that comes first: it is refused once
        any repeat ID or confirmation exists, because those answers belong to the old repeats.

        Raises
        ------
        DraftError
            ``unknown_key`` for a folder the draft does not have, ``pairing_label_empty`` for
            an empty label, ``pairing_after_confirmation`` when a repeat is already answered.
        """
        if not pairing:
            return self
        current = {m.key: group for group, r in self.repeats.items() for m in r.members}
        unknown = sorted(set(pairing) - set(current))
        if unknown:
            raise DraftError(
                "the draft has no folder " + ", ".join("/".join(key) for key in unknown),
                ("unknown_key",),
            )
        if any(not label or label != label.strip() for label in pairing.values()):
            raise DraftError(
                "a repeat is paired under a name, not an empty one", ("pairing_label_empty",)
            )
        if any(r.confirmed or not r.id.pending for r in self.repeats.values()):
            raise DraftError(
                "the pairing is applied before any repeat is named or confirmed",
                ("pairing_after_confirmation",),
            )
        grouped: dict[str, list[DraftMember]] = {}
        for key, group in sorted(current.items()):
            member = next(m for m in self.repeats[group].members if m.key == key)
            grouped.setdefault(pairing.get(key, group), []).append(member)
        slots = _slots("repeats", grouped, self.used_ids.get("repeats", ()))
        repeats = {g: DraftRepeat(slots[g], tuple(found)) for g, found in sorted(grouped.items())}
        return replace(self, repeats=MappingProxyType(repeats))

    def unit_rows(self) -> tuple[UnitRow, ...]:
        """Return one row per unit: the source labels and the confirmed IDs beside them."""
        rows = [
            UnitRow(
                member.condition,
                member.source_repeat,
                unit.source_folder,
                self.conditions[member.condition].confirmed,
                repeat.id.confirmed,
                self.technical_repeats[unit.source_folder].confirmed,
                unit.image_count,
            )
            for repeat in self.repeats.values()
            for member in repeat.members
            for unit in member.units
        ]
        return tuple(
            sorted(rows, key=lambda r: (r.condition, r.biological_repeat, r.technical_repeat))
        )


def _slots(kind: IdKind, labels: Iterable[str], used: Collection[str]) -> dict[str, IdSlot]:
    """Propose each label as its own ID where allowed, never twice for one case-folded name."""
    slots: dict[str, IdSlot] = {}
    taken = list(used)
    for label in sorted(set(labels)):
        proposal = propose_id(kind, label, taken)
        if proposal is not None:
            taken.append(proposal)
        slots[label] = IdSlot(proposal)
    return slots


def _members(discovery: DiscoveryResult, excluded: Collection[str]) -> list[DraftMember]:
    """Return the biological-repeat folders with images to analyse, with their units."""
    folders: dict[tuple[str, str], dict[str, int]] = {}
    for leaf in discovery.leaves:
        included = sum(image not in excluded for image in leaf.images)
        if included:  # a folder whose images are all excluded is not analysed
            units = folders.setdefault((leaf.condition, leaf.biological_repeat), {})
            units[leaf.technical_repeat] = units.get(leaf.technical_repeat, 0) + included
    return [
        DraftMember(
            condition,
            repeat,
            sum(units.values()),
            tuple(DraftUnit(folder, count) for folder, count in sorted(units.items())),
        )
        for (condition, repeat), units in sorted(folders.items())
    ]


def _draft(
    members: list[DraftMember],
    groups: Mapping[tuple[str, str], str],
    used: Mapping[str, tuple[str, ...]],
) -> Draft:
    """Build a draft from members and the repeat group of each (default: the folder name)."""
    paired: dict[str, list[DraftMember]] = {}
    for member in members:
        paired.setdefault(groups.get(member.key, member.source_repeat), []).append(member)
    repeat_slots = _slots("repeats", paired, used.get("repeats", ()))
    return Draft(
        MappingProxyType(
            _slots("conditions", (m.condition for m in members), used.get("conditions", ()))
        ),
        MappingProxyType(
            _slots(
                "technical_repeats",
                (u.source_folder for m in members for u in m.units),
                used.get("technical_repeats", ()),
            )
        ),
        MappingProxyType(
            {
                group: DraftRepeat(repeat_slots[group], tuple(found))
                for group, found in sorted(paired.items())
            }
        ),
        used_ids=MappingProxyType(dict(used)),
    )


def draft_from_discovery(
    discovery: DiscoveryResult,
    excluded: Iterable[str] = (),
    *,
    used_ids: Mapping[str, Iterable[str]] | None = None,
) -> Draft:
    """Build the draft of a folder before any answer: proposed IDs, units, counts, pairing.

    Biological-repeat folders with the same name are one repeat, as in the default design
    (:func:`.design.default_design`); nothing is confirmed. IDs are proposed as the folder
    labels where allowed (Q6: the same rule for conditions, technical repeats and repeats), and
    ``used_ids`` (the IDs a published file of this lineage lists) are never proposed again.
    No image is opened, no folder renamed and no file written.
    """
    used = {kind: tuple(values) for kind, values in (used_ids or {}).items()}
    return _draft(_members(discovery, set(excluded)), {}, used)


def _legacy_groups(legacy: ExperimentDesign) -> dict[tuple[str, str], str]:
    issues = [issue for issue in design_issues(legacy) if issue.code == "repeat_group_conflict"]
    if issues:
        raise LegacyMigrationRefused(issues[0].message)
    return legacy.repeat_groups()


def _probe(draft: Draft) -> dict[str, object]:
    """Return the draft as a document for the reader's rules only; stand-ins fill missing IDs.

    Condition, technical-repeat and repeat folders that are not allowed IDs get a stand-in ID
    (their question stays open, section 4.6a), and one channel stands in for the layouts the
    draft does not know. Nothing of it is stored.
    """
    stand_in = {}
    repeat_slots = {group: repeat.id for group, repeat in draft.repeats.items()}
    for kind, slots in (
        ("conditions", draft.conditions),
        ("technical", draft.technical_repeats),
        ("repeats", repeat_slots),
    ):
        for folder, slot in slots.items():
            name = folder if folder == SINGLE and kind != "repeats" else slot.proposed
            number = len(stand_in) + 1
            while name is None or (kind, name) in stand_in.values():
                name = f"{kind} {number}"
                number += 1
            stand_in[(kind, folder)] = (kind, name)
    cid = {folder: name for (kind, folder), (_, name) in stand_in.items() if kind == "conditions"}
    tid = {folder: name for (kind, folder), (_, name) in stand_in.items() if kind == "technical"}
    rid = {folder: name for (kind, folder), (_, name) in stand_in.items() if kind == "repeats"}
    return {
        "schema": SCHEMA_ID,
        "schema_version": 1,
        "conditions": {cid[f]: {"source_folder": f} for f in draft.conditions},
        "channels": {"Probe": {}},
        "repeats": {
            rid[group]: {
                "confirmed": True,
                "confirmed_on": repeat.confirmed_on or PLACEHOLDER_DATE,
                "members": {
                    cid[m.condition]: {
                        "source_repeat": m.source_repeat,
                        "image_count": m.image_count,
                        "technical_repeats": {
                            tid[u.source_folder]: {
                                "source_folder": u.source_folder,
                                "channels": ["Probe"],
                            }
                            for u in m.units
                        },
                    }
                    for m in repeat.members
                },
            }
            for group, repeat in draft.repeats.items()
        },
        "used_ids": {
            "conditions": sorted(cid.values()),
            "channels": ["Probe"],
            "repeats": sorted(rid.values()),
            "technical_repeats": sorted(tid.values()),
        },
    }


def _check_legacy_rules(draft: Draft) -> None:
    """Refuse a migrated draft that experiment-v1 would refuse, with the reader's codes."""
    doc = _probe(draft)
    for _, check in PHASES:
        issues = check(doc)
        if issues:
            codes = ", ".join(sorted({i.code.value for i in issues.items}))
            raise LegacyMigrationRefused(
                f"the legacy design breaks the rules of the experiment description ({codes}); "
                "it is not repaired: change the repeat groups, or start a new design",
                tuple(issues.items),
            )


def _migrated_repeat(
    group: str, repeat: DraftRepeat, legacy: ExperimentDesign
) -> tuple[DraftRepeat, MigrationNote | None]:
    """Carry a legacy group's confirmation: only if every folder is confirmed, latest date."""
    rows = [legacy.row(member.key) for member in repeat.members]
    known = [row for row in rows if row is not None]
    dates = sorted({row.confirmed_on for row in known if row.confirmed_on})
    confirmed = len(known) == len(rows) and all(row.confirmed for row in known)
    kept = dates[-1] if confirmed and dates else ""
    note = None
    if len(dates) > 1:
        listed = tuple(
            (m.condition, m.source_repeat, row.confirmed_on if row else "")
            for m, row in zip(repeat.members, rows, strict=True)
        )
        note = MigrationNote(group, listed, kept)
    return replace(repeat, confirmed=confirmed, confirmed_on=kept), note


def migrate_legacy_design(
    data: bytes,
    discovery: DiscoveryResult,
    excluded: Iterable[str] = (),
    *,
    name: str = "experiment_design.csv",
    used_ids: Mapping[str, Iterable[str]] | None = None,
) -> Draft:
    """Migrate the bytes of a legacy ``experiment_design.csv`` to a draft (experiment-v1 section 7).

    The file is read strictly (:func:`.design.parse_design_bytes`: ``yes``, ``no`` and empty
    booleans are refused, not coerced). Its ``repeat_group`` becomes the proposed repeat ID,
    ``biological_repeat`` the source repeat, and the ``condition`` label the condition's source
    folder, whose ID is proposed as that label when allowed. Technical repeats and counts come
    from ``discovery``; channels are not part of a legacy file. A group is confirmed only if
    every one of its folders is; with several dates it takes the latest and a
    :class:`MigrationNote` lists each folder's date; the clock is never read, and a confirmed
    group without any date stays open (``confirmed_on_missing``). A ``repeat_group`` that is not
    an allowed repeat ID (for example ``z``) is not refused: it migrates with no proposed ID and
    an open question, so the user names the repeat (section 4.6a). Controls: none marked, no
    reference; one marked, every other condition references it; two or more, an unresolved
    question and no reference. A folder found now but not in the file is a new pending member
    of the group named after it, which therefore is not confirmed. A legacy folder no longer
    found is listed in ``legacy_missing`` and blocks publication. IDs stay proposals: the user
    confirms them.

    Raises
    ------
    DesignFormatError
        If the bytes are not a design this brick writes (:func:`.design.parse_design_bytes`).
    LegacyMigrationRefused
        If a repeat group holds two folders of one condition, or the migrated design breaks a
        rule of the experiment description (for example ``E_REPEAT_ID_SHADOWS_SOURCE``): it is
        refused with the code, never repaired.
    """
    legacy = parse_design_bytes(data, name)
    used = {kind: tuple(values) for kind, values in (used_ids or {}).items()}
    members = _members(discovery, set(excluded))
    draft = _draft(members, _legacy_groups(legacy), used)
    notes, repeats = [], {}
    for group, repeat in draft.repeats.items():
        migrated, note = _migrated_repeat(group, repeat, legacy)
        proposal = group if not id_problems("repeats", group, used.get("repeats", ())) else None
        repeats[group] = replace(migrated, id=IdSlot(proposal))
        notes += [note] if note else []
    present = {m.condition for m in members}
    marked = sorted(c for c in legacy.controls if c in present)
    references = (
        {c: marked[0] for c in sorted(present) if c != marked[0]} if len(marked) == 1 else {}
    )
    found = {m.key for m in members}
    draft = replace(
        draft,
        repeats=MappingProxyType(repeats),
        references=MappingProxyType(references),
        unresolved_controls=tuple(marked) if len(marked) > 1 else (),
        legacy_missing=tuple(sorted(row.key for row in legacy.rows if row.key not in found)),
        notes=tuple(notes),
        origin="legacy",
    )
    _check_legacy_rules(draft)
    return draft


__all__ = [
    "Draft",
    "DraftError",
    "DraftMember",
    "DraftRepeat",
    "DraftUnit",
    "IdSlot",
    "LegacyMigrationRefused",
    "MigrationNote",
    "OpenQuestion",
    "UnitRow",
    "draft_from_discovery",
    "migrate_legacy_design",
]
