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
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from ..description import Description, Member
from ..description_reconcile import PublishedUnit, UnitFolders, published_units
from ..description_removal import Removal
from ..description_transition import DATE, Reconfirmation

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


def _removed_units(
    old: Description, new: Description, confirmations: Iterable[Reconfirmation]
) -> dict[UnitFolders, PublishedUnit]:
    """Return the published units the candidate no longer holds, by source folder labels."""
    fix = _renames(confirmations)
    kept_keys, kept_paths = set(published_units(new)), _paths(new)
    gone = {}
    for key, unit in published_units(old).items():
        path = (
            fix["repeats"].get(unit.repeat_id, unit.repeat_id),
            fix["conditions"].get(unit.condition_id, unit.condition_id),
            fix["technical_repeats"].get(unit.technical_id, unit.technical_id),
        )
        if key not in kept_keys and path not in kept_paths:
            gone[key] = unit
    return gone


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
        When a unit is dropped that no removal confirms, or a removal names a unit the
        candidate keeps or the published file does not have.
    """
    gone = _removed_units(old, new, confirmations)
    asked = {key for removal in removals for key in removal.units}
    loose = sorted(set(gone) - asked)
    if loose:
        names = ", ".join(f"{k.condition}/{k.source_repeat}/{k.technical}" for k in loose)
        raise ValueError(f"a unit is removed and no removal confirms it: {names}")
    extra = sorted(asked - set(gone))
    if extra:
        names = ", ".join(f"{k.condition}/{k.source_repeat}/{k.technical}" for k in extra)
        raise ValueError(f"a removal names a unit the candidate does not remove: {names}")

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
