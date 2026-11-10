"""Which planes show the axis roles being edited: the example of the notebook's axes step.

The owner's test of v0.5.0 (10 November 2026, item (e)): the role boxes only listed lengths.
The step now shows an example of one image *as the chosen roles read it*, redrawn whenever a
role changes: one tile per channel, a few time points, a few depth slices, or the single plane.
A wrong role then looks wrong (a length-3 axis chosen as Y gives a strip three pixels high).

This module only chooses the planes and labels them; it reads nothing and guesses nothing:
the roles are the ones the user is editing, and the example is display only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

CHANNEL_TILES = 6
"""The most channels shown side by side (evenly spread over the channel axis beyond that)."""
STEP_TILES = 4
"""The most time points, or depth slices, shown side by side."""
ROW_NAMES = {"C": "Channels (C)", "T": "Time points (T)", "Z": "Depth slices (Z)"}


@dataclass(frozen=True)
class Tile:
    """One plane of the example: its label and the coordinates it is read at."""

    label: str
    coords: tuple[tuple[str, int], ...]

    def at(self) -> dict[str, int]:
        """Return the coordinates as ``{"z": ..., "c": ..., "t": ...}`` (only the axes there)."""
        return dict(self.coords)


@dataclass(frozen=True)
class ExampleRow:
    """One row of tiles along one role (``""`` for the single plane of an image without one)."""

    role: str
    title: str
    tiles: tuple[Tile, ...]


def spread(length: int, count: int) -> list[int]:
    """Return at most ``count`` positions of ``range(length)``, evenly spread, ends included."""
    if length <= count:
        return list(range(length))
    if count == 1:
        return [0]
    return sorted({round(i * (length - 1) / (count - 1)) for i in range(count)})


def example_rows(lengths: Mapping[str, int], at: Mapping[str, int]) -> list[ExampleRow]:
    """Return the rows of the example for an image with these role lengths.

    Parameters
    ----------
    lengths
        ``{role: length}`` of the image's Z, C and T axes under the roles being edited; a role
        the image has no axis for is left out.
    at
        The position along each of those roles at which the *other* rows are read (the
        preview's sliders), e.g. the time point of the row of channels.

    Returns
    -------
    list of ExampleRow
        A row of channels (C), then a row of time points (T), then one of depth slices (Z),
        each only for an axis longer than one; a single row with one tile when there is none.
    """
    base = {role.lower(): min(at.get(role, 0), length - 1) for role, length in lengths.items()}
    rows = []
    for role, limit in (("C", CHANNEL_TILES), ("T", STEP_TILES), ("Z", STEP_TILES)):
        length = lengths.get(role, 1)
        if length <= 1:
            continue
        shown = spread(length, limit)
        name = role.lower()
        tiles = tuple(Tile(f"{name}={i}", tuple(sorted({**base, name: i}.items()))) for i in shown)
        title = ROW_NAMES[role]
        if len(shown) < length:
            title += f": {len(shown)} of {length}"
        rows.append(ExampleRow(role, title, tiles))
    if not rows:
        rows.append(ExampleRow("", "The plane", (Tile("", tuple(sorted(base.items()))),)))
    return rows


__all__ = [
    "CHANNEL_TILES",
    "ROW_NAMES",
    "STEP_TILES",
    "ExampleRow",
    "Tile",
    "example_rows",
    "spread",
]
