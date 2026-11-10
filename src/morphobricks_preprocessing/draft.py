"""The draft of an experiment description.

A draft is what discovery can say before the user has answered (experiment-v1 section 12): the
folders found, the IDs proposed for them, the pairing of biological repeats, and what is still
open. It is **not** a description and is never read as one: an ID is *proposed* until the user
confirms it, ``confirmed_on`` is a date the user gave, never a clock read,
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
from .discovery import DiscoveryResult
from .draft_ids import IdKind, id_problems, propose_id


class DraftError(ValueError):
    """Raised when a draft step is refused; ``codes`` are the reasons (Spec codes if any)."""

    def __init__(self, message: str, codes: tuple[str, ...] = ()) -> None:
        self.codes = codes
        super().__init__(message)


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
    ``repeats`` by the pairing label (the folder name for the default pairing).
    ``references`` maps a condition folder to the folder of the condition it is compared
    against. The draft is always derived from the folders; no file of the output folder
    (``experiment_design.csv`` included) is read to propose or prefill it (experiment-v1
    section 7).
    """

    conditions: Mapping[str, IdSlot]
    technical_repeats: Mapping[str, IdSlot]
    repeats: Mapping[str, DraftRepeat]
    references: Mapping[str, str] = field(default_factory=dict)
    used_ids: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

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

    Biological-repeat folders with the same name are one repeat (the default pairing, which the
    notebook's pairing card starts from); nothing is confirmed. IDs are proposed as the folder
    labels where allowed (Q6: the same rule for conditions, technical repeats and repeats), and
    ``used_ids`` (the IDs a published file of this lineage lists) are never proposed again.
    No image is opened, no folder renamed and no file written.
    """
    used = {kind: tuple(values) for kind, values in (used_ids or {}).items()}
    return _draft(_members(discovery, set(excluded)), {}, used)


__all__ = [
    "Draft",
    "DraftError",
    "DraftMember",
    "DraftRepeat",
    "DraftUnit",
    "IdSlot",
    "OpenQuestion",
    "UnitRow",
    "draft_from_discovery",
]
