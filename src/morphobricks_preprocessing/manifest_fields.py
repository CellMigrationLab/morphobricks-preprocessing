"""Checks of the fields of one manifest file entry, as written and as read (contract §9).

A file entry's ``position_id`` is not free text: it is the image's name without its
extension (:func:`.image_names.position_id`, MorphoBricks D02 and D85), never parsed or
edited. Manifest version 2 records it for every image, so a version 2 entry without it, or
with another one, is refused rather than read as an empty or a different position (round-2
audit A-m5: ``RunInputs`` turned a missing one into ``""``). Also the check of an entry's
axis roles, and of its flags, optional texts and masks entry, moved from :mod:`.manifest`.
Pure: no I/O.
"""

from __future__ import annotations

from .axes import AxisMapping
from .image_names import position_id


def recorded_position_id(value: object, relative_path: str, *, strict: bool = True) -> str | None:
    """Check the ``position_id`` of a manifest file entry against its image's name.

    Parameters
    ----------
    value
        The recorded ``position_id``.
    relative_path
        The entry's ``relative_path``, whose name gives the position identifier.
    strict
        True for manifest version 2, which records it for every image. False for version 1
        (read for reporting only): any value is kept as text, and None when absent.

    Returns
    -------
    str or None
        The position identifier; None only when not ``strict`` and none is recorded.

    Raises
    ------
    ValueError
        When ``strict`` and the value is missing, null or not text, or is not
        ``position_id(relative_path)``, or the path's name is only an extension; the
        message names the entry and the identifier its name gives.
    """
    if not strict:
        return None if value is None else str(value)
    expected = position_id(relative_path)
    if not isinstance(value, str):
        raise ValueError(
            f"the entry of {relative_path!r} has no position_id ({value!r}); manifest version 2 "
            f"records one for every image: {expected!r}, its name without the extension"
        )
    if value != expected:
        raise ValueError(
            f"the entry of {relative_path!r} records the position_id {value!r}, but its name "
            f"gives {expected!r}: a position is named by its image's name without the "
            "extension, never edited"
        )
    return value


def recorded_roles(value: object, axes: str) -> tuple[str | None, ...] | None:
    """Check a file's recorded axis roles: one role or null per axis (absent in older manifests).

    Parameters
    ----------
    value
        The recorded ``roles``: a list with one role or null per axis, or None.
    axes
        The file's axis letters.

    Returns
    -------
    tuple or None
        The roles; None when none are recorded.

    Raises
    ------
    ValueError
        If they are not one role or null per axis, or a role is unknown.
    """
    if value is None:
        return None
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
