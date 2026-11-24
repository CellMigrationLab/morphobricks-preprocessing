"""The ``removed`` and ``image_count_changed`` records of a publication (experiment-v1 4.6, 10).

Both are derived by comparing the candidate with the published file it replaces, plus the user's
facts that no comparison can supply: the date of each removal confirmation
(:class:`..description_removal.Removal`) and the date the image-count change is recorded
(``recorded_on``). Nothing here reads the clock, and a date that is needed and not given is an
error, never a default. Pure: no file is touched.

``removed`` (owner decision RR2). A published unit is removed when neither its source folders nor
its IDs (after the confirmed ID corrections) are in the candidate. Every removed unit is written
as a ``unit`` record naming all six keys. An entity whose ID the removal leaves held nowhere
(a condition, repeat or technical repeat) is written as its own record, before the unit records:
all entity records in the order condition, repeat, technical repeat (each by ID), then the unit
records by (condition ID, repeat ID, technical repeat ID). Labels are the published file's.

``image_count_changed`` (owner decisions RR1, RR3). Written for every member of a confirmed repeat
whose ``image_count`` differs, whether or not the publication changes anything else (a removal, a
new member, an ID correction, a reopening). Members are matched through the confirmed ID
corrections; a member the candidate no longer holds has no new count and is covered by its
``removed`` records. Lines carry the published IDs and labels, in (condition ID, repeat ID) order.

Folder and ID in one save (issue #475). A folder change (a removal, or a stated rename) and an ID
change of the same entity in one candidate are never paired: no ID correction is guessed from
folders that changed too. :func:`folder_id_changes` names them before Save and publication
refuses them (:data:`FOLDER_AND_ID_CHANGED`), so :func:`removal_records` never meets a unit that
no removal accounts for.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from ..description import Description, Member
from ..description_reconcile import PublishedUnit, UnitFolders, published_units
from ..description_removal import Removal
from ..description_rules import Refusal
from ..description_transition import DATE, Reconfirmation, _declared, transition_issues

FOLDER_AND_ID_CHANGED = "folder_and_id_changed"
"""Preprocessing's refusal of a candidate that changes the folder and the ID of one entity."""
REMOVAL_MISMATCH = "removal_mismatch"
"""Preprocessing's refusal of a dropped folder no removal confirms, or a removal of a kept one."""
_NOUNS = {"conditions": "condition", "repeats": "repeat", "technical_repeats": "technical repeat"}

_NONE = dict.fromkeys(
    (
        "condition_id",
        "condition_source_folder",
        "repeat_id",
        "source_repeat",
        "technical_repeat_id",
        "technical_repeat_source_folder",
    )
)


def _renames(confirmations: Iterable[Reconfirmation]) -> dict[str, dict[str, str]]:
    """Return the confirmed ID corrections (old to new) of each kind."""
    found: dict[str, dict[str, str]] = {"conditions": {}, "repeats": {}, "technical_repeats": {}}
    for item in confirmations:
        if item.new_id is not None and item.kind in found:
            found[item.kind][item.old_id] = item.new_id
    return found


def _paths(description: Description) -> set[tuple[str, str, str]]:
    return {
        (repeat_id, condition_id, tid)
        for repeat_id, repeat in description.repeats.items()
        for condition_id, member in repeat.members.items()
        for tid in member.technical_repeats
    }


_PATH = ("repeats", "conditions", "technical_repeats")
"""The kinds in the order of a unit's ID path (:func:`_paths`)."""


def _ids(unit: PublishedUnit) -> dict[str, str]:
    return {
        "conditions": unit.condition_id,
        "repeats": unit.repeat_id,
        "technical_repeats": unit.technical_id,
    }


def _fixed_ids(unit: PublishedUnit, fix: dict[str, dict[str, str]]) -> dict[str, str]:
    """Return a unit's IDs after the confirmed ID corrections ``fix`` (:func:`_renames`)."""
    return {kind: fix[kind].get(i, i) for kind, i in _ids(unit).items()}


def _path(ids: dict[str, str]) -> tuple[str, str, str]:
    """Return the ID path (repeat, condition, technical repeat) of a unit's IDs by kind."""
    return ids["repeats"], ids["conditions"], ids["technical_repeats"]


def _removed_units(
    old: Description, new: Description, confirmations: Iterable[Reconfirmation]
) -> dict[UnitFolders, PublishedUnit]:
    """Return the published units the candidate no longer holds, by source folder labels."""
    fix = _renames(confirmations)
    kept_keys, kept_paths = set(published_units(new)), _paths(new)
    gone = {}
    for key, unit in published_units(old).items():
        if key not in kept_keys and _path(_fixed_ids(unit, fix)) not in kept_paths:
            gone[key] = unit
    return gone


def _unanswered(
    old: Description,
    new: Description,
    removals: Iterable[Removal],
    confirmations: Iterable[Reconfirmation],
) -> tuple[dict[UnitFolders, PublishedUnit], list[UnitFolders], list[UnitFolders]]:
    """Return the dropped units, those no removal confirms, and removals of units not dropped.

    The two checks :func:`removal_records` asserts and :func:`folder_id_changes` reports.
    """
    gone = _removed_units(old, new, confirmations)
    asked = {key for removal in removals for key in removal.units}
    return gone, sorted(set(gone) - asked), sorted(asked - set(gone))


@dataclass(frozen=True, order=True)
class FolderIdChange:
    """One entity whose folder and ID change in the same save, or a folder no answer accounts for.

    ``kind`` is ``conditions``, ``repeats`` or ``technical_repeats`` and ``old_id`` the published
    ID; both are empty when no ID of the folders changed (:data:`REMOVAL_MISMATCH`). The
    published units concerned are split by what the candidate does with them: ``kept`` are
    still there by their folders, now under ``new_ids`` (read from the candidate, never
    guessed); ``lost`` are there neither by their folders nor by their IDs and no removal
    confirms them; ``removed`` are confirmed removed while their IDs went to ``given_to``.
    """

    kind: str
    old_id: str
    kept: tuple[UnitFolders, ...] = ()
    new_ids: tuple[str, ...] = ()
    lost: tuple[UnitFolders, ...] = ()
    removed: tuple[UnitFolders, ...] = ()
    given_to: tuple[UnitFolders, ...] = ()

    @property
    def code(self) -> str:
        """Return the refusal's code."""
        return FOLDER_AND_ID_CHANGED if self.kind else REMOVAL_MISMATCH

    @property
    def message(self) -> str:
        """Return what the candidate does, naming the entity, its IDs and its folders."""
        if not self.kind:
            said = []
            if self.lost:
                said.append(
                    f"The folder {_labels(self.lost)} is not in the description and no removal "
                    "confirms it."
                )
            if self.removed:
                said.append(
                    f"A removal names the folder {_labels(self.removed)}, which the description "
                    "does not remove (it keeps it or never had it)."
                )
            return " ".join(said)
        entity = f"{_NOUNS[self.kind]} {self.old_id}"
        said = [f"This changes both the folder and the ID of {entity} in one save:"]
        if self.kept:
            now = " and ".join(self.new_ids)
            said.append(
                f"{_labels(self.kept)} is now under {now} while the folders of {self.old_id} "
                f"changed too, so the ID change {self.old_id} -> {now} cannot be paired with "
                "them."
            )
        if self.lost:
            said.append(
                f"{_labels(self.lost)} is in the description neither under its saved folder nor "
                f"under its saved ID {self.old_id}, and no removal confirms it."
            )
        if self.removed:
            said.append(
                f"{_labels(self.removed)} is removed and the ID {self.old_id} is given to "
                f"{_labels(self.given_to)}."
            )
        return " ".join(said)

    @property
    def action(self) -> str:
        """Return how the user proceeds."""
        if not self.kind:
            return "Confirm the removal of a folder that is gone (Yes, remove), or undo the change."
        if self.removed and not (self.kept or self.lost):
            return (
                "Undo the ID edit: the new folder keeps an ID of its own. A removed ID stays "
                "used and is not given to another folder (experiment-v1 4.9)."
            )
        return f"Undo the ID edit: put {self.old_id} back."


def _labels(keys: Iterable[UnitFolders]) -> str:
    return ", ".join(_label(k) for k in keys)


def _label(key: UnitFolders) -> str:
    return f"{key.condition}/{key.source_repeat}/{key.technical}"


_ATTR = {"conditions": "condition_id", "repeats": "repeat_id", "technical_repeats": "technical_id"}


def _parts(key: UnitFolders) -> tuple[str, str, str]:
    """Return a unit's folder labels in :data:`_PATH` order (a member is its source repeat)."""
    return key.source_repeat, key.condition, key.technical


def _moved(key: UnitFolders, other: UnitFolders) -> list[str]:
    """Return the kinds whose folder differs between two units."""
    return [k for k, a, b in zip(_PATH, _parts(key), _parts(other), strict=True) if a != b]


def folder_id_changes(
    old: Description,
    new: Description,
    removals: Iterable[Removal] = (),
    confirmations: Iterable[Reconfirmation] = (),
) -> tuple[FolderIdChange, ...]:
    """Return the entities whose folder and ID change together, and folders nothing accounts for.

    Three cases, each found from the two files and the user's answers only:

    * an ID of a published unit the candidate still holds by its folders is gone, the unit now
      under a new ID that the transition checks did not pair (the entity's folders changed);
    * a unit the candidate holds neither by its folders nor by its IDs (after the confirmed
      corrections) that no removal confirms, with an ID no longer declared;
    * a unit a removal confirms whose IDs the candidate still holds, under other folders.

    A unit dropped with no removal and no ID gone, or a removal of a unit the candidate keeps by
    its folders, is reported with an empty ``kind``. An empty result means
    :func:`removal_records` accepts the candidate and the removals. Pure.
    """
    confirmations = tuple(confirmations)
    gone, loose, extra = _unanswered(old, new, removals, confirmations)
    fix = _renames(confirmations)
    declared, before = _declared(new), _declared(old)
    paired = {
        i.change
        for i in transition_issues(old, new)
        if i.code == Refusal.R_CONFIRMED_ID_CHANGED and i.new_id is not None
    }
    paired_old = {(kind, old_id) for kind, old_id, _ in paired}
    units, held = published_units(old), published_units(new)
    by_path = {_path(_ids(u)): key for key, u in held.items()}
    found: dict[tuple[str, str], dict[str, set]] = {}

    def add(kind: str, key: UnitFolders, part: str, *also: tuple[str, object]) -> None:
        """Note ``key`` as ``part`` of the entity of ``kind`` that holds it ('' for none)."""
        old_id = getattr(units[key], _ATTR[kind]) if kind else ""
        entry = found.setdefault((kind, old_id), {})
        entry.setdefault(part, set()).add(key)
        for name, value in also:
            entry.setdefault(name, set()).add(value)

    def retired(unit: PublishedUnit) -> list[str]:  # a paired ID change is refused on its own
        ids, fixed = _ids(unit), _fixed_ids(unit, fix)
        return [k for k in ids if fixed[k] not in declared[k] and (k, ids[k]) not in paired_old]

    for key, unit in units.items():
        if key in held:  # the same folders: an ID gone and the folders now under a new ID
            for kind in retired(unit):
                new_id = _ids(held[key])[kind]
                if new_id not in before[kind]:
                    add(kind, key, "kept", ("new_ids", new_id))
    for key in loose:  # dropped, and no removal confirms it
        for kind in retired(gone[key]) or [""]:
            add(kind, key, "lost")
    for key in extra:  # a removal of a unit the candidate does not drop
        if key in units and key not in held:  # removed, but its IDs are held by other folders
            other = by_path[_path(_fixed_ids(units[key], fix))]
            for kind in _moved(key, other):
                add(kind, key, "removed", ("given_to", other))
        else:  # a removal of a folder the candidate keeps or the published file does not have
            add("", key, "removed")
    return tuple(
        sorted(
            FolderIdChange(kind, old_id, **{part: tuple(sorted(v)) for part, v in entry.items()})
            for (kind, old_id), entry in found.items()
        )
    )


def removal_records(
    old: Description,
    new: Description,
    removals: tuple[Removal, ...],
    confirmations: tuple[Reconfirmation, ...] = (),
) -> tuple[dict, ...]:
    """Return the ``removed`` records of the units and entities the candidate drops.

    Each takes the date of the first removal answer that names one of its units.

    Raises
    ------
    ValueError
        An internal assertion: when a unit is dropped that no removal confirms, or a removal
        names a unit the candidate keeps or the published file does not have. Publication and
        the pre-save check refuse these first, with a code (:func:`folder_id_changes`).
    """
    gone, loose, extra = _unanswered(old, new, removals, confirmations)
    if loose:
        raise ValueError(f"a unit is removed and no removal confirms it: {_labels(loose)}")
    if extra:
        raise ValueError(f"a removal names a unit the candidate does not remove: {_labels(extra)}")

    def day(keys: Iterable[UnitFolders]) -> str:
        held = set(keys)
        return next(r.confirmed_on for r in removals if held & set(r.units))

    fix = _renames(confirmations)
    kept_tids = {tid for _, _, tid in _paths(new)}
    entities: dict[tuple[int, str], dict] = {}
    conditions = {u.condition_id: u for u in gone.values()}
    repeats = {u.repeat_id for u in gone.values()}
    tids = {u.technical_id: u for u in gone.values()}
    for cid in conditions:
        if fix["conditions"].get(cid, cid) not in new.conditions:
            keys = [k for k, u in gone.items() if u.condition_id == cid]
            entities[(0, cid)] = _row(
                "condition",
                day(keys),
                condition_id=cid,
                condition_source_folder=old.conditions[cid].source_folder,
            )
    for rid in repeats:
        if fix["repeats"].get(rid, rid) not in new.repeats:
            keys = [k for k, u in gone.items() if u.repeat_id == rid]
            entities[(1, rid)] = _row("repeat", day(keys), repeat_id=rid)
    for tid, unit in tids.items():
        if fix["technical_repeats"].get(tid, tid) not in kept_tids:
            keys = [k for k, u in gone.items() if u.technical_id == tid]
            entities[(2, tid)] = _row(
                "technical_repeat",
                day(keys),
                technical_repeat_id=tid,
                technical_repeat_source_folder=unit.unit.source_folder,
            )
    rows = [entities[key] for key in sorted(entities)]
    for key, unit in sorted(
        gone.items(), key=lambda kv: (kv[1].condition_id, kv[1].repeat_id, kv[1].technical_id)
    ):
        rows.append(
            _row(
                "unit",
                day([key]),
                condition_id=unit.condition_id,
                condition_source_folder=key.condition,
                repeat_id=unit.repeat_id,
                source_repeat=key.source_repeat,
                technical_repeat_id=unit.technical_id,
                technical_repeat_source_folder=key.technical,
            )
        )
    return tuple(rows)


def _row(entity: str, confirmed_on: str, **named: str) -> dict:
    """Return a ``removed`` line: every key present, ``null`` where the entity names none."""
    return {"type": "removed", "entity": entity, **_NONE, **named, "confirmed_on": confirmed_on}


def image_count_records(
    old: Description,
    new: Description,
    recorded_on: str | None,
    confirmations: Iterable[Reconfirmation] = (),
) -> tuple[dict, ...]:
    """Return the ``image_count_changed`` record of every confirmed member whose count changed.

    Raises
    ------
    ValueError
        When a record is due and ``recorded_on`` is not a ``YYYY-MM-DD`` date (no default).
    """
    fix = _renames(confirmations)
    changed = []
    for rid, repeat in old.repeats.items():
        if not repeat.confirmed:
            continue
        kept = new.repeats.get(fix["repeats"].get(rid, rid))
        for cid, member in repeat.members.items():
            held = kept.members.get(fix["conditions"].get(cid, cid)) if kept else None
            if held is not None and held.image_count != member.image_count:
                changed.append((cid, rid, member, held.image_count))
    if not changed:
        return ()
    try:
        if recorded_on is None or not DATE.fullmatch(recorded_on):
            raise ValueError
        date.fromisoformat(recorded_on)
    except ValueError:
        raise ValueError(
            f"an image count changed: recorded_on is a real YYYY-MM-DD date, not {recorded_on!r}"
        ) from None
    return tuple(_count_row(old, *item, recorded_on) for item in sorted(changed, key=_by_ids))


def _by_ids(item: tuple) -> tuple[str, str]:
    return item[0], item[1]


def _count_row(
    old: Description, cid: str, rid: str, member: Member, now: int, recorded_on: str
) -> dict:
    """Return the ``image_count_changed`` line of one member (4.6)."""
    return {
        "type": "image_count_changed",
        "condition_id": cid,
        "condition_source_folder": old.conditions[cid].source_folder,
        "repeat_id": rid,
        "source_repeat": member.source_repeat,
        "technical_repeats": [
            {"id": tid, "source_folder": unit.source_folder}
            for tid, unit in sorted(member.technical_repeats.items())
        ],
        "old_image_count": member.image_count,
        "new_image_count": now,
        "recorded_on": recorded_on,
    }
