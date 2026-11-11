"""Checks of the fields of one manifest file entry, as written and as read (contract §9).

A file entry's ``position_id`` is not free text: it is the image's name without its
extension (:func:`.image_names.position_id`, MorphoBricks D02 and D85), never parsed or
edited. Every manifest this package reads (version 4) records it for every image, so an entry
without it, or with another one, is refused rather than read as an empty or a different position
(round-2 audit A-m5: ``RunInputs`` turned a missing one into ``""``). Nothing is read leniently
for an older manifest: older versions are refused whole (:mod:`.manifest_versions`). Also the
check of an entry's axis roles, and of its flags, optional texts and masks entry, moved from
:mod:`.manifest`.
Pure: no I/O.
"""

from __future__ import annotations

from morphobricks_normalization.channels import check_channel_ids

from .axes import AxisMapping
from .image_names import position_id

RAW_NAMES_KEY = "raw_channel_names"
"""The file entry's key for the channel names as the header gave them (written when not empty)."""


CHANNEL_IDS_KEY = "channel_ids"
"""The key of an image's canonical channel IDs in a manifest file entry (Stage 3, S3.3)."""


def recorded_channel_ids(value: object, relative_path: str) -> tuple[str, ...]:
    """Check an image's recorded canonical channel IDs, as written and as read.

    One ID per source channel, in source order: the confirmed ``channels`` list of the image's
    imaging unit (experiment-v1 section 4.4). Required, non-empty, distinct channel IDs; never
    filled in or guessed.

    Raises
    ------
    ValueError
        When the value is not a non-empty list of distinct channel IDs.
    """
    if not isinstance(value, list | tuple) or not value:
        raise ValueError(f"the entry of {relative_path!r} records no channel_ids")
    try:
        return check_channel_ids(value, f"The channel_ids of {relative_path!r}")
    except (TypeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc


def recorded_raw_names(value: object, relative_path: str) -> tuple[str | None, ...]:
    """Read a recorded ``raw_channel_names`` (a list of text or null), or refuse it.

    Absent (a manifest written before it was recorded, or an image whose header names no
    channel) reads as empty. A name is kept exactly as written, never trimmed.
    """
    if value is None:
        return ()
    if not isinstance(value, list) or any(n is not None and not isinstance(n, str) for n in value):
        raise ValueError(f"the raw channel names of {relative_path!r} are not text or null")
    return tuple(value)


def recorded_position_id(value: object, relative_path: str) -> str:
    """Check the ``position_id`` of a manifest file entry against its image's name.

    Parameters
    ----------
    value
        The recorded ``position_id``.
    relative_path
        The entry's ``relative_path``, whose name gives the position identifier.

    Returns
    -------
    str
        The position identifier.

    Raises
    ------
    ValueError
        When the value is missing, null or not text, or is not ``position_id(relative_path)``,
        or the path's name is only an extension; the message names the entry and the
        identifier its name gives.
    """
    expected = position_id(relative_path)
    if not isinstance(value, str):
        raise ValueError(
            f"the entry of {relative_path!r} has no position_id ({value!r}); a manifest records "
            f"one for every image: {expected!r}, its name without the extension"
        )
    if value != expected:
        raise ValueError(
            f"the entry of {relative_path!r} records the position_id {value!r}, but its name "
            f"gives {expected!r}: a position is named by its image's name without the "
            "extension, never edited"
        )
    return value


def recorded_roles(value: object, axes: str) -> tuple[str | None, ...]:
    """Check a file's recorded axis roles: one role or null per axis, always recorded.

    Parameters
    ----------
    value
        The recorded ``roles``: a list with one role or null per axis.
    axes
        The file's axis letters.

    Returns
    -------
    tuple
        The roles.

    Raises
    ------
    ValueError
        If they are absent, not one role or null per axis, or a role is unknown.
    """
    if not isinstance(value, list) or len(value) != len(axes):
        raise ValueError(f"the roles {value!r} do not fit the axes {axes!r}")
    roles = tuple(None if r is None else str(r) for r in value)
    AxisMapping(axes, roles)  # raises ValueError for an unknown role
    return roles


def recorded_flag(value: object, key: str | None = None) -> bool:
    """Return a manifest flag; anything but a boolean (``"false"``, 0, null) is malformed.

    Used both when reading and when writing a manifest, so a manifest is never written
    with a flag its reader would refuse (or that ``bool()`` would turn around). Moved from
    :mod:`.manifest`.

    Parameters
    ----------
    value
        The recorded value.
    key
        Its key, for the message.

    Returns
    -------
    bool

    Raises
    ------
    ValueError
        If ``value`` is not ``True`` or ``False``.
    """
    if not isinstance(value, bool):
        where = f" for {key}" if key else ""
        raise ValueError(f"expected true or false{where}, found {value!r}")
    return value


def optional_text(value: object) -> str | None:
    """Return an optional manifest value as text, or None when it is None.

    Parameters
    ----------
    value
        The recorded value.

    Returns
    -------
    str or None
    """
    return None if value is None else str(value)


def recorded_masks(data: object) -> dict | None:
    """Check the optional ``masks`` entry of a manifest (absent in older manifests).

    Parameters
    ----------
    data
        The recorded entry, or None.

    Returns
    -------
    dict or None

    Raises
    ------
    ValueError
        If it is not a mapping with a ``files`` list.
    """
    if data is None:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise ValueError("its masks entry is not a mask list")
    return data
