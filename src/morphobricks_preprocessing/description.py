"""The experiment description as a value: the model of ``morphobricks.toml`` (experiment-v1).

The model is built only from a document the strict reader accepted (``description_read``);
nothing here validates, repairs, defaults or converts. Arrays whose order is not semantic
(``header_names``, ``source_channels``, ``used_ids``) are held sorted in code point order, so
two models are equal exactly when the files mean the same (section 9.2); a unit's ``channels``
keep their declared order. An optional field is ``None`` when the file did not write it, and
an optional array keeps the difference between absent and ``[]``.

The model is immutable all the way down (#489): every table (``conditions``, ``channels``,
``repeats``, ``used_ids``, ``masks``, a repeat's ``members``, a member's ``technical_repeats``)
is a read-only ``MappingProxyType`` over a copy the model alone holds, made when the model is
built, and every array is a tuple. One parsed model is shared by every reader of the same bytes
(:mod:`.description_consume`), so no consumer can change what another sees or what a later
digest is taken of. A model pickles (and ``copy.deepcopy`` copies) through its tables' plain
dicts and is rebuilt read-only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import Any

from .description_read import read_description_bytes
from .description_report import DescriptionIssue
from .description_rules import CHANNEL_LAYOUT, CHANNELS_FROM, SCHEMA_ID, USED_IDS

Table = Mapping[str, Any]
USED_KINDS = USED_IDS.fields
"""The kinds of ``used_ids``, in the order the file writes them."""


def _frozen(table: Mapping) -> Mapping:
    """Return a read-only view of a copy of ``table``: no one else holds the copy (#489)."""
    return MappingProxyType(dict(table))


def _freeze(model: object, *names: str) -> None:
    """Replace the named tables of a frozen dataclass instance with read-only copies."""
    for name in names:
        object.__setattr__(model, name, _frozen(getattr(model, name)))


def _reduce(model: object) -> tuple:
    """Return the pickle recipe of a model holding read-only tables (each as a plain dict).

    ``MappingProxyType`` does not pickle; the model is rebuilt by its own constructor, which
    makes the tables read-only again.
    """
    values = (getattr(model, f.name) for f in fields(model))
    return (type(model), tuple(dict(v) if isinstance(v, MappingProxyType) else v for v in values))


class DescriptionError(Exception):
    """Raised when bytes are not a valid description; ``issues`` are the first failing phase's."""

    def __init__(self, issues: tuple[DescriptionIssue, ...], phase: int) -> None:
        self.issues = issues
        self.phase = phase
        shown = "; ".join(f"{i.code.value} at {i.path}" for i in issues[:5])
        super().__init__(f"not a valid experiment description (phase {phase}): {shown}")


@dataclass(frozen=True)
class Condition:
    """One experimental condition: its source folder and descriptive or reference fields."""

    source_folder: str
    display_name: str | None = None
    reference: str | None = None
    target: str | None = None
    treatment_key: str | None = None


@dataclass(frozen=True)
class Channel:
    """One declared channel; ``header_names`` is None when the file wrote none."""

    display_name: str | None = None
    header_names: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Unit:
    """One imaging unit (a technical repeat of a member); ``channels`` is C0, C1, ... in order."""

    source_folder: str
    channels: tuple[str, ...]
    display_name: str | None = None
    image_type: str | None = None
    rgb_header_override: bool | None = None
    channels_from: str | None = None
    """``experiment`` or ``unit`` in a fast-mode file, None in a strict file (ADR 0006)."""

    @property
    def follows_experiment(self) -> bool:
        """True if the unit follows the fast-mode experiment list (``channels_from``)."""
        return self.channels_from == "experiment"


@dataclass(frozen=True)
class ChannelLayout:
    """The fast-mode statement (section 4.3a, ADR 0006): one ordered list for the experiment.

    Present only in a file that has the ``channel_layout`` table; no reader ever supplies it.
    ``confirmed_on`` is the date of the user's statement, a user fact (section 4.4a).
    """

    channels: tuple[str, ...]
    confirmed_on: str
    image_type: str | None = None


@dataclass(frozen=True)
class Member:
    """One source biological-repeat folder of one condition, with its imaging units."""

    source_repeat: str
    image_count: int
    technical_repeats: Mapping[str, Unit]

    def __post_init__(self) -> None:
        _freeze(self, "technical_repeats")

    def __reduce__(self) -> tuple:
        return _reduce(self)


@dataclass(frozen=True)
class Repeat:
    """One canonical biological repeat: the user's confirmation and its members by condition."""

    confirmed: bool
    confirmed_on: str
    members: Mapping[str, Member]
    display_name: str | None = None

    def __post_init__(self) -> None:
        _freeze(self, "members")

    def __reduce__(self) -> tuple:
        return _reduce(self)


@dataclass(frozen=True)
class Mask:
    """One declared mask: its source type and the representation the user confirmed."""

    source_type: str
    representation: str
    source_channels: tuple[str, ...] | None = None
    display_name: str | None = None


@dataclass(frozen=True)
class Description:
    """A whole experiment description. ``masks`` is empty when the file has none.

    ``channel_layout`` is None for a strict per-unit file, the only meaning of its absence.
    Every table is a read-only copy (``MappingProxyType``) and every ``used_ids`` value a tuple,
    whatever mappings and sequences were given (#489).
    """

    conditions: Mapping[str, Condition]
    channels: Mapping[str, Channel]
    repeats: Mapping[str, Repeat]
    used_ids: Mapping[str, tuple[str, ...]]
    masks: Mapping[str, Mask] = field(default_factory=dict)
    schema_version: int = 1
    schema: str = SCHEMA_ID
    channel_layout: ChannelLayout | None = None

    def __post_init__(self) -> None:
        _freeze(self, "conditions", "channels", "repeats", "masks")
        used = {kind: tuple(ids) for kind, ids in self.used_ids.items()}
        object.__setattr__(self, "used_ids", MappingProxyType(used))

    def __reduce__(self) -> tuple:
        return _reduce(self)

    @property
    def fast_mode(self) -> bool:
        """True if the file is a fast-mode file (it has ``channel_layout``, ADR 0006)."""
        return self.channel_layout is not None


def _sorted(table: Table, key: str) -> tuple[str, ...] | None:
    return tuple(sorted(table[key])) if key in table else None


def _unit(table: Table) -> Unit:
    return Unit(
        table["source_folder"],
        tuple(table["channels"]),
        table.get("display_name"),
        table.get("image_type"),
        table.get("rgb_header_override"),
        table.get(CHANNELS_FROM),
    )


def _member(table: Table) -> Member:
    units = {key: _unit(value) for key, value in table["technical_repeats"].items()}
    return Member(table["source_repeat"], table["image_count"], units)


def _repeat(table: Table) -> Repeat:
    members = {key: _member(value) for key, value in table["members"].items()}
    return Repeat(
        table["confirmed"],
        table["confirmed_on"],
        members,
        table.get("display_name"),
    )


def from_document(doc: Table) -> Description:
    """Return the model of a document that passed phases 1 to 5 of the reader.

    A document the reader refused is not a description: calling this on one may raise
    ``KeyError`` or ``TypeError``, or build a model that means nothing. Use
    ``description_from_bytes`` to read and refuse in one step.
    """
    conditions = {
        key: Condition(
            value["source_folder"],
            value.get("display_name"),
            value.get("reference"),
            value.get("target"),
            value.get("treatment_key"),
        )
        for key, value in doc["conditions"].items()
    }
    channels = {
        key: Channel(value.get("display_name"), _sorted(value, "header_names"))
        for key, value in doc["channels"].items()
    }
    masks = {
        key: Mask(
            value["source_type"],
            value["representation"],
            _sorted(value, "source_channels"),
            value.get("display_name"),
        )
        for key, value in doc.get("masks", {}).items()
    }
    layout = doc.get(CHANNEL_LAYOUT)
    return Description(
        conditions,
        channels,
        {key: _repeat(value) for key, value in doc["repeats"].items()},
        {kind: tuple(sorted(doc["used_ids"][kind])) for kind in USED_KINDS},
        masks,
        doc["schema_version"],
        doc["schema"],
        None
        if layout is None
        else ChannelLayout(
            tuple(layout["channels"]), layout["confirmed_on"], layout.get("image_type")
        ),
    )


def description_from_bytes(data: bytes) -> Description:
    """Read the raw bytes of a file with the strict reader and return its model.

    Raises
    ------
    DescriptionError
        With every error of the first failing phase, when the bytes are not valid.
    """
    report = read_description_bytes(data)
    if not report.ok:
        raise DescriptionError(report.errors, report.phase)
    return from_document(report.document)
