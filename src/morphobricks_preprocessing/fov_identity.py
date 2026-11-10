"""A position's canonical identity from the description (experiment-v1 section 3.4).

The FOV key keeps the source folder labels. The canonical IDs are added beside it, each a
function of the key: the repeat ID (``repeat_group``) of the member folder, the condition ID of
the condition folder, the technical repeat ID of the unit folder, and the unit's channel IDs
by C index. Nothing is looked up by the label alone: a label that names no declared member, or
two, is refused. The unit's declared image type rides along (``image_type``: ``rgb``, or
``channels`` for a unit that declares none, experiment-v1 section 4.4b): the one source of
``Fov.image_type`` and of the request resolver's ``FovSelection.image_type``
(:mod:`.run_inputs_resolve`). It is never read from a header.
"""

from __future__ import annotations

from dataclasses import dataclass

from .description import Description, Unit
from .run_inputs_request import CHANNELS, RGB


@dataclass(frozen=True)
class FovIdentity:
    """The canonical identity of one position."""

    repeat_group: str
    condition_id: str
    technical_repeat_id: str
    channel_ids: tuple[str, ...]
    image_type: str
    """The unit's declared image type: ``rgb``, or ``channels`` for an ordinary channel image."""


def declared_image_type(unit: Unit) -> str:
    """Return a unit's declared image type: ``rgb``, or ``channels`` when it declares none.

    The description's only value is ``rgb``; an absent ``image_type`` is an ordinary channel
    image (experiment-v1 section 4.4b). Nothing is inferred from the images.
    """
    return RGB if unit.image_type == RGB else CHANNELS


def fov_identity(description: Description, labels: tuple[str, str, str]) -> FovIdentity:
    """Return the identity of the position whose source folder labels are ``labels``.

    Parameters
    ----------
    description
        The published description.
    labels
        ``(condition folder, biological repeat folder, technical repeat folder)``.

    Raises
    ------
    LookupError
        When the labels name no declared unit, or more than one.
    """
    condition, repeat, technical = labels
    found = [
        FovIdentity(repeat_id, condition_id, unit_id, unit.channels, declared_image_type(unit))
        for repeat_id, entry in description.repeats.items()
        for condition_id, member in entry.members.items()
        if description.conditions[condition_id].source_folder == condition
        and member.source_repeat == repeat
        for unit_id, unit in member.technical_repeats.items()
        if unit.source_folder == technical
    ]
    if len(found) != 1:
        raise LookupError(
            f"the folders {condition!r} / {repeat!r} / {technical!r} name "
            f"{len(found)} units of the description, not one"
        )
    return found[0]
