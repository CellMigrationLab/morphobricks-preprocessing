"""The ``experiment_description`` entry of the run manifest (experiment-v1 sections 4.5a, 4.6, 9.3).

A run with a published description records, in its manifest, the file's identity (the SHA-256
of the exact bytes the run read: provenance, never a cache key), the section digests consumers
key their products on, which units' image types were checked against a header, the status of
every declared mask file, and the records of publication (re-confirmations) copied unchanged
from ``morphobricks.records.jsonl``. Every run has a published description (Spec ADR 0002 AR10).
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from .channel_plan import channel_coverage
from .description import Description
from .description_unnamed import FIELD, checked_items, unnamed_items
from .description_write import section_digests
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
    for index, unit in enumerate(value["units"]):
        if isinstance(unit, dict) and FIELD in unit:
            checked_items(unit[FIELD], f"{KEY}.units[{index}]")
        if isinstance(unit, dict) and DECLARED in unit and not isinstance(unit[DECLARED], bool):
            raise ValueError(f"{KEY}.units[{index}].{DECLARED} is not true or false")
        if isinstance(unit, dict) and OVERRIDE_IMAGES in unit:
            _checked_override_items(unit[OVERRIDE_IMAGES], f"{KEY}.units[{index}]")
        if isinstance(unit, dict):
            _checked_override_agreement(unit, f"{KEY}.units[{index}]")
    return value


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
    """Refuse a recorded unit whose declared override differs from the published description.

    ``recorded`` is :func:`recorded_description`'s entry and ``description`` the published file
    the run read (its SHA-256 already checked): the manifest's ``rgb_header_override`` of an RGB
    unit must equal the description's.
    """
    for index, unit in enumerate(recorded["units"]):
        if not isinstance(unit, dict) or DECLARED not in unit:
            continue
        member = description.repeats[unit["repeat"]].members[unit["condition"]]
        declared = member.technical_repeats[unit["technical_repeat"]].rgb_header_override is True
        if unit[DECLARED] != declared:
            raise ValueError(
                f"{KEY}.units[{index}].{DECLARED} differs from the published description"
            )
