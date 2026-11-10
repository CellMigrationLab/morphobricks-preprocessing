"""The ``experiment_design.csv`` derived from a published description (experiment-v1 section 11).

``derive_design`` maps the description to one :class:`.design.DesignRow` per member, and
``write_derived_design`` writes it with today's writer (:func:`.design.write_experiment_design`),
so the delimiter, quoting, final LF, absence of a byte-order mark and the sort order are the
writer's, and the bytes equal the Spec's golden files. The file carries the source folder labels
exactly as before and no condition, technical-repeat or channel ID: renaming an ID never changes
it. Nothing is guessed, defaulted or repaired: every value is the description's.

Columns: ``condition`` is the condition's ``source_folder``; ``biological_repeat`` the member's
``source_repeat``; ``repeat_group`` the repeat ID; ``control`` is true when another condition
(or none other: never itself, 4.7) names the condition as its ``reference``; ``image_count``
the member's; ``technical_repeat_count`` is 0 for the single unit ``single`` and else the number
of units; ``confirmed`` is true (the reader refuses a published repeat that is not);
``confirmed_on`` the repeat's date.
"""

from __future__ import annotations

import os
from pathlib import Path

from morphobricks_normalization.records import SINGLE

from .description import Description
from .design import DesignRow, DesignTable, write_experiment_design


def derive_design(description: Description) -> DesignTable:
    """Return the design the description derives, one row per member (section 11).

    Raises
    ------
    DesignFormatError
        From :func:`.design.write_experiment_design` when written, if a repeat ID is not a name
        the legacy readers accept (it is never repaired).
    """
    referenced = {c.reference for c in description.conditions.values() if c.reference is not None}
    rows = []
    for repeat_id, repeat in description.repeats.items():
        for condition_id, member in repeat.members.items():
            units = member.technical_repeats
            single = len(units) == 1 and next(iter(units.values())).source_folder == SINGLE
            rows.append(
                DesignRow(
                    description.conditions[condition_id].source_folder,
                    member.source_repeat,
                    repeat_id,
                    control=condition_id in referenced,
                    image_count=member.image_count,
                    technical_repeat_count=0 if single else len(units),
                    confirmed=repeat.confirmed,
                    confirmed_on=repeat.confirmed_on,
                )
            )
    return DesignTable(tuple(rows))


def write_derived_design(description: Description, path: str | os.PathLike) -> Path:
    """Write the derived ``experiment_design.csv`` atomically and return its path."""
    return write_experiment_design(derive_design(description), path)


__all__ = ["derive_design", "write_derived_design"]
