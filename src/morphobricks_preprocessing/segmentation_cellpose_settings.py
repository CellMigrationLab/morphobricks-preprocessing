"""The Cellpose inference settings and what Cellpose derives from them (no Cellpose import).

Split from :mod:`.segmentation_cellpose` to keep each file small; that module re-exports all of
it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .segmentation_record import SegmentationError

FLOW_ITERATIONS = 200
"""Cellpose's flow iterations at the native scale; it divides it by the rescale factor."""
NORMALIZE_REFUSAL = (
    "Images are always normalised by Cellpose's own per-image normalisation (each channel to "
    "its 1st to 99th percentile); they are never sent to Cellpose unnormalised, so turning "
    "it off is refused."
)
"""Why :class:`Settings` refuses ``normalize`` other than True."""


@dataclass(frozen=True)
class Settings:
    """The inference settings the user chooses.

    Attributes
    ----------
    diameter
        Cell diameter in **pixels**, or None for the model's native scale (Cellpose 4.2.1.1
        does not estimate a diameter). Never 0.
    flow_threshold
        Flow error threshold, 0 or more (Cellpose's default 0.4).
    cellprob_threshold
        Cell probability threshold (Cellpose's default 0).
    normalize
        Always True: Cellpose's own per-image normalisation, each channel to its 1st to 99th
        percentile (owner, 23 November 2026). Images are never sent to Cellpose unnormalised,
        so anything else is refused (:data:`NORMALIZE_REFUSAL`).
    batch_size
        Tiles run together on the device, 1 or more.
    """

    diameter: float | None = None
    flow_threshold: float = 0.4
    cellprob_threshold: float = 0.0
    normalize: bool = True
    batch_size: int = 8

    def __post_init__(self) -> None:
        """Refuse a value Cellpose would accept but this brick will not record as it is."""
        if self.diameter is not None and not _positive(self.diameter):
            raise SegmentationError(
                f"The diameter must be a positive number of pixels, or blank for the model's "
                f"native scale; got {self.diameter!r}. Zero is refused: it would silently mean "
                "the native scale."
            )
        if flow_iterations(self) < 1:
            raise SegmentationError(
                f"A diameter of {self.diameter!r} pixels gives Cellpose less than one flow "
                f"iteration (int({FLOW_ITERATIONS} / rescale) is 0 at a rescale of "
                f"{rescale_factor(self):.4g}); Cellpose would run on a rescale it cannot use. "
                "Give the diameter of the cells in pixels, or leave it blank."
            )
        if not _finite(self.flow_threshold) or self.flow_threshold < 0:
            raise SegmentationError(
                f"The flow threshold must be 0 or more, not {self.flow_threshold!r}."
            )
        if not _finite(self.cellprob_threshold):
            raise SegmentationError(
                f"The cell probability threshold must be a number, not {self.cellprob_threshold!r}."
            )
        if self.normalize is not True:
            raise SegmentationError(f"{NORMALIZE_REFUSAL} Got normalize={self.normalize!r}.")
        if (
            isinstance(self.batch_size, bool)
            or not isinstance(self.batch_size, int)
            or self.batch_size < 1
        ):
            raise SegmentationError(
                f"The batch size must be a whole number, 1 or more, not {self.batch_size!r}."
            )


def _finite(value: object) -> bool:
    """Return True for a real number that is neither NaN nor infinite (a bool is not one)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _positive(value: object) -> bool:
    """Return True for a finite number above 0."""
    return _finite(value) and value > 0  # type: ignore[operator]


def diameter_from_text(text: str) -> float | None:
    """Return the diameter typed in a text field: blank is None (native scale), else pixels.

    Raises
    ------
    SegmentationError
        For text that is not a positive number (0 included).
    """
    if not text.strip():
        return None
    try:
        value = float(text)
    except ValueError:
        raise SegmentationError(
            f"The diameter {text!r} is not a number. Type pixels (for example 30) or leave it "
            "blank."
        ) from None
    return Settings(diameter=value).diameter


def rescale_factor(settings: Settings) -> float:
    """Return the factor Cellpose derives from the diameter: 30 / diameter, or 1 (native scale)."""
    return 1.0 if settings.diameter is None else 30.0 / settings.diameter


def flow_iterations(settings: Settings) -> int:
    """Return the flow iterations Cellpose 4.2.1.1 uses when none are given: 200 / rescale.

    Cellpose computes ``int(200 / rescale)`` (resampling is on); it is passed explicitly and
    recorded, so what ran is not left to Cellpose's derivation. It is never raised to 1: a
    diameter that gives 0 is refused by :class:`Settings`.
    """
    try:
        return int(FLOW_ITERATIONS / rescale_factor(settings))
    except (OverflowError, ZeroDivisionError):  # a diameter so large its rescale rounds to 0
        return 0


CELL_ROLE = "cell"
"""The role of the cell (cytoplasm or cell-border) channel."""
NUCLEAR_ROLE = "nuclear"
"""The role of the nuclear channel."""
CELL_OUTPUT = "cells"
"""Output: a cell mask, from the cell channel and, if given, the nuclear channel after it."""
NUCLEI_OUTPUT = "nuclei"
"""Output: a nuclei mask, from the nuclear channel only."""
OUTPUT_MASK_TYPES = {CELL_OUTPUT: "Cells", NUCLEI_OUTPUT: "Nuclei"}
"""The mask type each output proposes (the user may change it)."""
BOTH_OUTPUT = "both"
"""Output: a cell mask and a nuclei mask, each segmented on its own (#440); never paired here."""
NO_CHANNEL = "Choose a cell channel, a nuclear channel or both, by channel ID; neither is chosen."
"""The refusal when neither channel is chosen."""
BOTH_NEEDS = (
    "The output Both needs a cell channel and a nuclear channel: the cells are segmented from "
    "the cell channel and the nuclear channel, the nuclei from the nuclear channel alone. "
    "Choose both channels, or choose the output Cell mask or Nuclei mask."
)
"""The refusal of the output Both without a cell channel or without a nuclear channel."""


def input_channels(output: str, cell: str, nuclear: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return Cellpose's input channel IDs and their roles for ``output`` (owner, 23 Nov 2026).

    A cell mask reads the cell channel, then the nuclear channel if one is given; a nuclei
    mask reads the nuclear channel only. ``""`` is a channel not chosen. Channels are only
    named here; they are resolved by ID by :func:`.segmentation_handoff.resolve_segmentation`.

    Raises
    ------
    SegmentationError
        When neither channel is chosen, for a cell mask without a cell channel, a nuclei mask
        without a nuclear channel, or an unknown output.
    """
    if not cell and not nuclear:
        raise SegmentationError(NO_CHANNEL)
    if output == CELL_OUTPUT:
        if not cell:
            raise SegmentationError(
                "A cell mask needs a cell channel; only a nuclear channel is chosen. Choose the "
                "cell channel, or choose the output Nuclei mask."
            )
        return ((cell, nuclear), (CELL_ROLE, NUCLEAR_ROLE)) if nuclear else ((cell,), (CELL_ROLE,))
    if output == NUCLEI_OUTPUT:
        if not nuclear:
            raise SegmentationError(
                "A nuclei mask needs a nuclear channel; only a cell channel is chosen. Choose "
                "the nuclear channel, or choose the output Cell mask."
            )
        return (nuclear,), (NUCLEAR_ROLE,)
    raise SegmentationError(f"The output must be {CELL_OUTPUT} or {NUCLEI_OUTPUT}, not {output!r}.")


def output_inputs(
    output: str, cell: str, nuclear: str
) -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]:
    """Return each mask an output makes: ``(mask output, channel IDs, roles)``, cells first.

    A cell mask or a nuclei mask is one entry (:func:`input_channels`). **Both** (#440) is two
    segmentations of each image, each with its own input: the cells from the cell channel and
    the nuclear channel, stacked as (cell, nuclear), the nuclei from the nuclear channel alone.
    It needs both channels. The two masks are never paired by label number.

    Raises
    ------
    SegmentationError
        As :func:`input_channels`, and for Both without a cell or a nuclear channel.
    """
    if output != BOTH_OUTPUT:
        return ((output, *input_channels(output, cell, nuclear)),)
    if not cell and not nuclear:
        raise SegmentationError(NO_CHANNEL)
    if not (cell and nuclear):
        raise SegmentationError(BOTH_NEEDS)
    return tuple((o, *input_channels(o, cell, nuclear)) for o in (CELL_OUTPUT, NUCLEI_OUTPUT))


def channel_roles_entry(
    channel_ids: tuple[str, ...], roles: tuple[str, ...], output: str | None = None
) -> list[dict[str, str]] | None:
    """Return the record's ``channel_roles``: each channel ID with its role, in Cellpose's order.

    The roles only label the channels chosen by ID: for a cell mask the cell channel, then the
    nuclear channel if any; for a nuclei mask the nuclear channel alone. No roles (``()``, and
    no output) are recorded as null, never guessed.

    Raises
    ------
    SegmentationError
        For roles without an output or the reverse, or roles that are not those of the output.
    """
    if not roles and output is None:
        return None
    allowed = {
        CELL_OUTPUT: ((CELL_ROLE,), (CELL_ROLE, NUCLEAR_ROLE)),
        NUCLEI_OUTPUT: ((NUCLEAR_ROLE,),),
    }.get(output, ())
    if len(roles) != len(channel_ids) or roles not in allowed:
        raise SegmentationError(
            f"The channel roles {list(roles)} for {list(channel_ids)} do not fit the output "
            f"{output!r}: a cell mask reads the cell channel, then the nuclear one if any; a "
            "nuclei mask reads the nuclear channel only."
        )
    return [{"channel_id": c, "role": r} for c, r in zip(channel_ids, roles, strict=True)]
