"""The units a folder walk found, as :func:`.description_reconcile.reconcile` takes them.

Counts only: how many images each unit has now. Whether the headers still fit the declared
channels is the header evidence of the physical checks; when a caller has it (channel count,
raw header names) it builds the :class:`~.description_reconcile.ObservedUnit` itself. Nothing
is read from an image here, and no unit is renamed or matched by similarity.
"""

from __future__ import annotations

from collections.abc import Collection

from .description_reconcile import ObservedUnit, UnitFolders
from .discovery import DiscoveryResult


def observe_folders(
    discovery: DiscoveryResult, excluded: Collection[str] = ()
) -> dict[UnitFolders, ObservedUnit]:
    """Return each unit of the walk with its image count, leaving out the images ``excluded``.

    A folder whose images are all excluded is not a unit (it is a removed one if published).
    The channel evidence of every unit is left unknown (None), never assumed.
    """
    found: dict[UnitFolders, int] = {}
    for leaf in discovery.leaves:
        count = sum(1 for image in leaf.images if image not in excluded)
        if count:
            key = UnitFolders(*leaf.labels)
            found[key] = found.get(key, 0) + count
    return {key: ObservedUnit(count) for key, count in sorted(found.items())}
