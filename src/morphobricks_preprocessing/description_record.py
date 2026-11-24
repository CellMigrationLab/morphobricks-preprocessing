"""The ``experiment_description`` entry of the run manifest (experiment-v1 sections 4.5a, 4.6, 9.3).

A run with a published description records, in its manifest, the file's identity (the SHA-256
of the exact bytes the run read: provenance, never a cache key), the section digests consumers
key their products on, which units' image types were checked against a header, the status of
every declared mask file, and the records of publication (re-confirmations) copied unchanged
from ``morphobricks.records.jsonl``. Every run has a published description (Spec ADR 0002 AR10).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .channel_plan import channel_coverage
from .description import Description, Unit
from .description_unnamed import FIELD, checked_items, unnamed_items
from .description_write import SECTIONS, section_digests
from .fast_layout import HEADER, STATEMENT
from .physical_fast import fov_key
from .physical_unit import UnitKey
from .rgb_override import HEADER_RGB, NO_INTERPRETATION, RGB, UNUSED, USED, OverrideUse

KEY = "experiment_description"
"""The manifest key of the entry."""
SHA256 = re.compile(r"[0-9a-f]{64}")
OVERRIDDEN = "image_type_header_overridden"
PHOTOMETRIC = "header_photometric"
"""The unit record's keys of an accepted RGB header override (experiment-v1 4.4b, RH1)."""
DECLARED = "rgb_header_override"
"""The unit record's key of a unit declared ``rgb``: its declared ``rgb_header_override``."""
OVERRIDE_IMAGES = "rgb_header_override_images"
"""The unit record's key of a unit declaring the override: what it did for each image."""
CHANNELS_FROM = "channels_from"
FAST_IMAGES = "fast_layout_images"
"""A fast unit's per-image verification status (ADR 0006): path, FOV key, header_verified and,
per index of the list, the channel, the header name as read and its evidence."""
DIGESTS_MALFORMED = "manifest_section_digests_malformed"
"""The code of a ``section_digests`` record that is not the four sections' SHA-256 (#490)."""
UNIT_MALFORMED = "manifest_unit_record_malformed"
"""The code of a unit record that is not an object with its fields and their types (#491)."""
UNIT_MISMATCH = "manifest_unit_record_mismatch"
"""The code of a well-formed unit record that is not the published description's unit (#491)."""
_IDENTITY = ("repeat", "condition", "technical_repeat")
_UNIT_KEYS = (*_IDENTITY, "channels", "image_type_header_verified")
"""The fields :func:`unit_records` writes for every unit."""
_UNIT_OPTIONAL = frozenset(
    {DECLARED, OVERRIDDEN, PHOTOMETRIC, OVERRIDE_IMAGES, FIELD, CHANNELS_FROM, FAST_IMAGES}
)
"""The fields :func:`unit_records` writes for some units only."""
_USE_KEYS = frozenset({"image", "override", "header_photometric"})
_FIELDS = (
    "sha256",
    "schema_version",
    "section_digests",
    "units",
    "masks",
    "records",
    "channel_coverage",
)


def unit_records(
    description: Description,
    verified: Mapping[tuple[str, str, str], bool],
    unnamed: Mapping[tuple[str, str, str], Mapping[str, tuple[int, ...]]] | None = None,
    overridden: Mapping[tuple[str, str, str], str] | None = None,
    override_images: Mapping[tuple[str, str, str], Mapping[str, OverrideUse]] | None = None,
    fast_images: Mapping[tuple[str, str, str], Sequence[dict]] | None = None,
) -> list[dict[str, object]]:
    """Return every declared unit with whether its image type was checked against a header.

    ``verified`` maps ``(condition folder, repeat folder, technical repeat folder)`` to the
    check's outcome; a unit it does not name is not verified. It is False for a unit whose
    format states no colour interpretation (OME-Zarr: the declaration stands on the user's
    confirmation) and for a unit with no readable image.

    ``unnamed`` maps the same keys to the images whose header names not every channel and the
    positions without a name (:mod:`.description_unnamed`); a unit with such images gets the
    list under ``images_without_header_channel_names``, any other unit has no such key.

    ``overridden`` maps the same keys to the colour interpretation of a unit declared ``rgb``
    whose contradicting header was accepted by ``rgb_header_override`` (experiment-v1 4.4b,
    RH1; :attr:`.physical_check.PhysicalReport.image_type_overridden`): such a unit is recorded
    as declared RGB with the header overridden, ``image_type_header_overridden`` true and
    ``header_photometric`` naming the header's interpretation (for example ``minisblack``); it
    is not header-verified. Any other unit has neither key.

    Declared and effective are recorded separately (4.4b, owner answers of 14 November 2026).
    Every unit declared ``rgb`` records its declared key, ``rgb_header_override`` (``true`` or
    ``false``; absent in the file is ``false``); other units' records do not have it.
    ``override_images`` maps a unit declaring the override to what it did for each image
    (:attr:`.physical_check.PhysicalReport.override_images`), recorded under
    ``rgb_header_override_images`` (:func:`override_items`).
    """
    out = []
    for repeat_id, repeat in sorted(description.repeats.items()):
        for condition_id, member in sorted(repeat.members.items()):
            for unit_id, unit in sorted(member.technical_repeats.items()):
                key = (
                    description.conditions[condition_id].source_folder,
                    member.source_repeat,
                    unit.source_folder,
                )
                record: dict[str, object] = {
                    "repeat": repeat_id,
                    "condition": condition_id,
                    "technical_repeat": unit_id,
                    "channels": list(unit.channels),
                    "image_type_header_verified": verified.get(key, False),
                }
                if unit.image_type == RGB:
                    record[DECLARED] = unit.rgb_header_override is True
                if key in (overridden or {}):
                    record[OVERRIDDEN] = True
                    record[PHOTOMETRIC] = overridden[key]
                if (override_images or {}).get(key):
                    record[OVERRIDE_IMAGES] = override_items(override_images[key])
                if (unnamed or {}).get(key):
                    record[FIELD] = unnamed_items(unnamed[key], len(unit.channels))
                if unit.channels_from is not None:
                    record[CHANNELS_FROM] = unit.channels_from
                if unit.follows_experiment:
                    record[FAST_IMAGES] = [dict(item) for item in (fast_images or {}).get(key, ())]
                out.append(record)
    return out


def override_items(uses: Mapping[str, OverrideUse]) -> list[dict[str, object]]:
    """Return a unit's per-image override record, sorted by image (relative path).

    Each item has ``image``, ``override`` (``used``: the override lifted a header that does not
    say RGB; ``unused``: it did not) and ``header_photometric``, the image's own header colour
    interpretation kept as evidence (null for a format that states none). An unused item adds
    ``unused_reason``: ``header_already_rgb`` (header-verified) or ``no_colour_interpretation``
    (for example OME-Zarr; not header-verified).
    """
    out = []
    for image, use in sorted(uses.items()):
        item: dict[str, object] = {
            "image": image,
            "override": use.use,
            "header_photometric": use.photometric,
        }
        if use.reason is not None:
            item["unused_reason"] = use.reason
        out.append(item)
    return out


def _checked_override_items(value: object, where: str) -> None:
    """Refuse a malformed ``rgb_header_override_images`` list of one unit record."""
    if not isinstance(value, list) or not value:
        raise ValueError(f"{where}.{OVERRIDE_IMAGES} must be a non-empty list")
    for item in value:
        unused = isinstance(item, dict) and item.get("override") == UNUSED
        keys = _USE_KEYS | {"unused_reason"} if unused else _USE_KEYS
        if not isinstance(item, dict) or set(item) != keys:
            raise ValueError(f"{where}.{OVERRIDE_IMAGES} items must hold {', '.join(sorted(keys))}")
        photometric = item["header_photometric"]
        reason = item.get("unused_reason")
        if (
            not isinstance(item["image"], str)
            or not item["image"]
            or item["override"] not in (USED, UNUSED)
            or reason not in (None, HEADER_RGB, NO_INTERPRETATION)
            or (photometric is None) != (reason == NO_INTERPRETATION)
            or not isinstance(photometric, str | None)
            or (item["override"] == USED and photometric == RGB)
            or (reason == HEADER_RGB and photometric != RGB)
        ):
            raise ValueError(f"{where}.{OVERRIDE_IMAGES} holds a malformed item")
    paths = [item["image"] for item in value]
    if paths != sorted(set(paths)):
        raise ValueError(f"{where}.{OVERRIDE_IMAGES} images must be sorted and distinct")


def description_record(
    description: Description,
    sha256: str,
    units: list[dict[str, object]],
    masks: dict[str, dict],
    records: tuple[dict, ...],
) -> dict[str, object]:
    """Return the manifest entry for a description read from the bytes with this SHA-256.

    ``channel_coverage`` classifies every biological repeat for every declared channel ID as
    ``full``, ``partial`` or ``unavailable``, with the units that lack it
    (:func:`.channel_plan.channel_coverage`; Stage 3, S3.3); each unit record lists its
    channels in source order.
    """
    return {
        "sha256": sha256,
        "schema_version": description.schema_version,
        "section_digests": section_digests(description),
        "units": units,
        "masks": masks,
        "records": list(records),
        "channel_coverage": channel_coverage(description),
    }


def recorded_description(value: object) -> dict | None:
    """Return the manifest's entry as read (None when absent), refusing a malformed one.

    Raises
    ------
    ValueError
        When the entry is not an object with the recorded fields and types.
    SectionDigestsError
        ``manifest_section_digests_malformed``: ``section_digests`` does not hold exactly the
        four sections, each a lowercase SHA-256 (#490).
    """
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != set(_FIELDS):
        raise ValueError(f"{KEY} must hold exactly {', '.join(_FIELDS)}")
    sha = value["sha256"]
    if not isinstance(sha, str) or not SHA256.fullmatch(sha):
        raise ValueError(f"{KEY}.sha256 is not a SHA-256")
    if not isinstance(value["schema_version"], int) or isinstance(value["schema_version"], bool):
        raise ValueError(f"{KEY}.schema_version is not a whole number")
    for name, kind in (
        ("section_digests", dict),
        ("masks", dict),
        ("units", list),
        ("records", list),
        ("channel_coverage", dict),
    ):
        if not isinstance(value[name], kind):
            raise ValueError(f"{KEY}.{name} is not a {kind.__name__}")
    _checked_section_digests(value["section_digests"])
    for index, unit in enumerate(value["units"]):
        where = f"{KEY}.units[{index}]"
        _checked_unit_fields(unit, where)
        if FIELD in unit:
            checked_items(unit[FIELD], where)
        if DECLARED in unit and not isinstance(unit[DECLARED], bool):
            raise ValueError(f"{where}.{DECLARED} is not true or false")
        if OVERRIDE_IMAGES in unit:
            _checked_override_items(unit[OVERRIDE_IMAGES], where)
        _checked_override_agreement(unit, where)
        if FAST_IMAGES in unit:
            checked_fast_images(unit, where)
    return value


class UnitRecordError(ValueError):
    """A unit record that is not an object with its fields and their types (#491)."""

    code = UNIT_MALFORMED


class UnitRecordMismatch(ValueError):
    """A well-formed unit record that is not the published description's unit (#491)."""

    code = UNIT_MISMATCH


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _checked_unit_fields(unit: object, where: str) -> None:
    """Refuse a unit record without its fields, with another field, or of the wrong types.

    The identity (``repeat``, ``condition``, ``technical_repeat``) is non-empty text,
    ``channels`` a list of non-empty text, ``image_type_header_verified`` true or false and
    ``channels_from``, when present, non-empty text; nothing missing is filled in (#491).
    """
    if not isinstance(unit, dict):
        raise UnitRecordError(f"{UNIT_MALFORMED}: {where} is not an object")
    named = [f"{where}.{name} is missing" for name in _UNIT_KEYS if name not in unit]
    named += [
        f"{where}.{name} is not a unit record field"
        for name in sorted(str(key) for key in unit)
        if name not in _UNIT_KEYS and name not in _UNIT_OPTIONAL
    ]
    if not named:
        named = [
            f"{where}.{name} is not non-empty text" for name in _IDENTITY if not _text(unit[name])
        ]
        channels = unit["channels"]
        if not isinstance(channels, list) or not all(_text(item) for item in channels):
            named.append(f"{where}.channels is not a list of channel IDs")
        if not isinstance(unit["image_type_header_verified"], bool):
            named.append(f"{where}.image_type_header_verified is not true or false")
        if CHANNELS_FROM in unit and not _text(unit[CHANNELS_FROM]):
            named.append(f"{where}.{CHANNELS_FROM} is not non-empty text")
    if named:
        raise UnitRecordError(f"{UNIT_MALFORMED}: {'; '.join(named)}")


class SectionDigestsError(ValueError):
    """A ``section_digests`` record that is not the four sections' SHA-256 (#490)."""

    code = DIGESTS_MALFORMED


def _checked_section_digests(digests: dict) -> None:
    """Refuse a ``section_digests`` record other than the four sections' lowercase SHA-256.

    A missing or extra section, or a value that is not 64 lowercase hexadecimal digits, is
    refused, naming the field; nothing is filled in or repaired. Whether a well-formed digest is
    the published description's is checked when a run is opened
    (:func:`.run_inputs_semantic.check_section_digests`).
    """
    where = f"{KEY}.section_digests"
    missing = [name for name in SECTIONS if name not in digests]
    extra = sorted(str(name) for name in digests if name not in SECTIONS)
    if missing or extra:
        named = [f"{where}.{name} is missing" for name in missing]
        named += [f"{where}.{name} is not a section" for name in extra]
        raise SectionDigestsError(
            f"{DIGESTS_MALFORMED}: {'; '.join(named)} (it holds exactly {', '.join(SECTIONS)})"
        )
    for name in SECTIONS:
        digest = digests[name]
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise SectionDigestsError(
                f"{DIGESTS_MALFORMED}: {where}.{name} is not a lowercase hexadecimal SHA-256"
            )


_FAST_KEYS = frozenset({"path", "fov_key", "header_verified", "channels"})
_EVIDENCE_KEYS = frozenset({"c", "channel", "header_name", "evidence"})
_FOV_KEY = frozenset(fov_key(UnitKey("c", "r", "t"), "p.tif"))
"""The four fields of a FOV key (experiment-v1 3.4), each non-empty text: the record's own."""


def checked_fast_images(unit: dict, where: str) -> None:
    """Refuse a malformed ``fast_layout_images`` of one unit record (ADR 0006).

    The unit says ``channels_from = "experiment"``; the items are sorted by distinct path; each
    lists one entry per channel of the unit, in C order, with ``evidence`` ``header`` (only with
    a non-empty ``header_name``) or ``statement``; ``header_verified`` is true exactly when every
    entry is ``header``; ``fov_key`` is the four non-empty fields of experiment-v1 3.4.
    """
    items = unit[FAST_IMAGES]
    if unit.get(CHANNELS_FROM) != "experiment" or not isinstance(items, list):
        raise ValueError(f"{where}.{FAST_IMAGES} needs {CHANNELS_FROM} = experiment and a list")
    for item in items:
        if not isinstance(item, dict) or set(item) != _FAST_KEYS:
            raise ValueError(
                f"{where}.{FAST_IMAGES} items must hold {', '.join(sorted(_FAST_KEYS))}"
            )
        fov = item["fov_key"]
        if (
            not isinstance(fov, dict)
            or set(fov) != _FOV_KEY
            or not all(isinstance(v, str) and v for v in fov.values())
        ):
            raise ValueError(
                f"{where}.{FAST_IMAGES} fov_key must hold {', '.join(sorted(_FOV_KEY))}"
            )
        entries = item["channels"]
        if not isinstance(entries, list) or [
            e.get("channel") if isinstance(e, dict) else None for e in entries
        ] != list(unit.get("channels", ())):
            raise ValueError(f"{where}.{FAST_IMAGES} channels must follow the unit's list")
        for index, entry in enumerate(entries):
            if (
                set(entry) != _EVIDENCE_KEYS
                or entry["c"] != index
                or entry["evidence"] not in (HEADER, STATEMENT)
                or not isinstance(entry["header_name"], str | None)
                or (entry["evidence"] == HEADER and not entry["header_name"])
            ):
                raise ValueError(f"{where}.{FAST_IMAGES} holds a malformed channel entry")
        verified = all(entry["evidence"] == HEADER for entry in entries)
        if item["header_verified"] is not verified:
            raise ValueError(f"{where}.{FAST_IMAGES}: header_verified disagrees with the evidence")
    paths = [item["path"] for item in items]
    if not all(isinstance(path, str) and path for path in paths) or paths != sorted(set(paths)):
        raise ValueError(f"{where}.{FAST_IMAGES} paths must be sorted and distinct")


def _checked_override_agreement(unit: dict, where: str) -> None:
    """Refuse override fields of one unit record that contradict each other (review K, N1).

    Per-image records and the effective ``image_type_header_overridden`` need the declared key
    ``true``; the effective flag is present exactly when an image used the override, and its
    ``header_photometric`` names the used images' interpretations as the writer joins them.
    """
    images = unit.get(OVERRIDE_IMAGES)
    if (images is not None or OVERRIDDEN in unit) and unit.get(DECLARED) is not True:
        raise ValueError(f"{where} records an override use but does not declare {DECLARED} = true")
    if OVERRIDDEN in unit and (
        unit[OVERRIDDEN] is not True or not isinstance(unit.get(PHOTOMETRIC), str)
    ):
        raise ValueError(f"{where}.{OVERRIDDEN} must be true with the header's {PHOTOMETRIC}")
    if images is None:
        return
    used = sorted({item["header_photometric"] for item in images if item["override"] == USED})
    if (OVERRIDDEN in unit) != bool(used) or (used and unit.get(PHOTOMETRIC) != ", ".join(used)):
        raise ValueError(
            f"{where}: {OVERRIDDEN} and {PHOTOMETRIC} disagree with the images' override use"
        )


def check_declared_overrides(recorded: dict, description: Description) -> None:
    """Refuse recorded unit records that are not the published description's units (#491).

    ``recorded`` is :func:`recorded_description`'s entry (its unit records well-formed) and
    ``description`` the published file the run read (its SHA-256 already checked). Each record
    names a declared unit, once, and every declared unit has a record; its ``channels`` and
    ``channels_from`` are the unit's; the declared ``rgb_header_override`` is recorded exactly
    for a unit declared ``rgb`` and equals the description's (review K, N1).

    Raises
    ------
    UnitRecordMismatch
        ``manifest_unit_record_mismatch``, naming the unit record and the field.
    """
    declared = {
        (repeat_id, condition_id, unit_id)
        for repeat_id, repeat in description.repeats.items()
        for condition_id, member in repeat.members.items()
        for unit_id in member.technical_repeats
    }
    seen: set[tuple[str, str, str]] = set()
    for index, record in enumerate(recorded["units"]):
        where = f"{KEY}.units[{index}]"
        identity = tuple(record[name] for name in _IDENTITY)
        unit = _declared_unit(record, description, where)
        if identity in seen:
            raise UnitRecordMismatch(
                f"{UNIT_MISMATCH}: {where} records unit {'/'.join(identity)} more than once"
            )
        seen.add(identity)
        _check_unit_record(record, unit, where)
    missing = ", ".join("/".join(identity) for identity in sorted(declared - seen))
    if missing:
        raise UnitRecordMismatch(
            f"{UNIT_MISMATCH}: the published description's unit {missing} has no record in "
            f"{KEY}.units"
        )


def _declared_unit(record: dict, description: Description, where: str) -> Unit:
    """Return the published unit a record names, refusing the first field naming none."""
    repeat = description.repeats.get(record["repeat"])
    if repeat is None:
        raise UnitRecordMismatch(
            f"{UNIT_MISMATCH}: {where}.repeat {record['repeat']!r} is not a biological repeat "
            "of the published description"
        )
    member = repeat.members.get(record["condition"])
    if member is None:
        raise UnitRecordMismatch(
            f"{UNIT_MISMATCH}: {where}.condition {record['condition']!r} is not a condition of "
            f"biological repeat {record['repeat']!r} in the published description"
        )
    unit = member.technical_repeats.get(record["technical_repeat"])
    if unit is None:
        raise UnitRecordMismatch(
            f"{UNIT_MISMATCH}: {where}.technical_repeat {record['technical_repeat']!r} is not a "
            f"technical repeat of {record['repeat']!r}/{record['condition']!r} in the published "
            "description"
        )
    return unit


def _check_unit_record(record: dict, unit: Unit, where: str) -> None:
    """Refuse a record whose channels or declared override differ from its published unit."""
    if record["channels"] != list(unit.channels):
        raise UnitRecordMismatch(
            f"{UNIT_MISMATCH}: {where}.channels differs from the published description"
        )
    if record.get(CHANNELS_FROM) != unit.channels_from:
        raise UnitRecordMismatch(
            f"{UNIT_MISMATCH}: {where}.{CHANNELS_FROM} differs from the published description"
        )
    rgb = unit.image_type == RGB
    if (DECLARED in record) != rgb:
        state = "records" if DECLARED in record else "does not record"
        kind = "not declared rgb" if DECLARED in record else "declared rgb"
        raise UnitRecordMismatch(
            f"{UNIT_MISMATCH}: {where} {state} {DECLARED}, but the published description's "
            f"unit is {kind}"
        )
    if rgb and record[DECLARED] != (unit.rgb_header_override is True):
        raise UnitRecordMismatch(
            f"{UNIT_MISMATCH}: {where}.{DECLARED} differs from the published description"
        )
