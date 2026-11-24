"""The channel-identity check's limits and results (``docs/contract.md`` §14.1).

:class:`ChannelIdentitySettings` (recorded in the run manifest beside ``intensity_checks``) and
the frozen results :func:`.channel_identity.check_channel_identity` returns. Pure: nothing here
computes a verdict or words a message.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, NamedTuple

import numpy as np

from .validation import ValidationIssue

CHANNEL_UNLIKE_OTHERS = "channel_unlike_others"
POSSIBLE_CHANNEL_SWAP = "possible_channel_swap"
AMBIGUOUS_CHANNEL_SWAP = "ambiguous_channel_swap"
CHANNEL_IDENTITY_CODES = (POSSIBLE_CHANNEL_SWAP, AMBIGUOUS_CHANNEL_SWAP, CHANNEL_UNLIKE_OTHERS)
PAIR_TOLERANCE = 0.5
"""Two mutual pairs sharing a channel whose strengths (the larger of a pair's two cross
distances) differ by at most this much cannot be told apart: the channels are an ambiguous
swap, never one pair picked (#395). Half a robust deviation (root mean square), below the
spread of the references themselves (``mad_floor``, ``swap_limit``)."""
KEY = "channel_identity"
"""The manifest key of the limits, beside ``intensity_checks``."""
COMPONENTS = ("level", "bright", "spread")
_FLOATS = ("outlier_limit", "mad_floor", "min_log_difference", "swap_limit", "swap_ratio")
_FLOATS += ("partner_ratio",)
_KEYS = ("outlier_limit", "min_reference", "mad_floor", "min_log_difference")
_KEYS += ("swap_limit", "swap_ratio", "partner_ratio")


@dataclass(frozen=True)
class ChannelIdentitySettings:
    """Limits of the channel-identity check (contract §14.1).

    The defaults were chosen with a simulation of experiments without and with swaps, with
    between-unit variation of 10 % and 20 % per unit and channel (the pull request records
    the table). Distances are root mean squares of robust z-scores over the three components.

    Attributes
    ----------
    outlier_limit
        Distance from its own reference above which a unit's channel may be reported;
        default 3.5.
    min_reference
        Fewest other units a reference needs (within the condition, else across the
        experiment); default 3.
    mad_floor
        Smallest robust spread of a component, in natural-log units: about the usual
        between-unit variation of staining and exposure (default 0.2, about 22 %), so a reference of
        a few very similar units does not turn ordinary differences into large z-scores.
    min_log_difference
        Smallest difference from the reference, in natural-log units, that one component must
        have before a channel is reported; default 0.6 (a factor of about 1.8).
    swap_limit
        Largest distance to another channel's reference for the channel to be said to look
        like it, when the two channels do not look like each other; default 1.0.
    swap_ratio
        How many times closer to the other channel than to its own reference the channel must
        be (unless the two channels of the unit look like each other); default 3.0.
    partner_ratio
        For a mutual swap, how many times nearer the other channel's reference than its own
        each of the two channels must be, so a normal channel close to a failed one is not
        named half of a swap; default 2.0.

    Raises
    ------
    ValueError
        For a limit outside its range.
    """

    outlier_limit: float = 3.5
    min_reference: int = 3
    mad_floor: float = 0.2
    min_log_difference: float = 0.6
    swap_limit: float = 1.0
    swap_ratio: float = 3.0
    partner_ratio: float = 2.0

    def __post_init__(self) -> None:
        for name in _FLOATS:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float, np.number)):
                raise ValueError(f"{name} must be a number; got {value!r}.")
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f"{name} must be a positive finite number; got {value!r}.")
            object.__setattr__(self, name, float(value))
        for name in ("swap_ratio", "partner_ratio"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1; got {getattr(self, name)!r}.")
        count = self.min_reference
        if isinstance(count, bool) or not isinstance(count, (int, np.integer)) or count < 1:
            raise ValueError(f"min_reference must be a whole number of at least 1; got {count!r}.")
        object.__setattr__(self, "min_reference", int(count))

    def to_dict(self) -> dict[str, Any]:
        """Return the limits as JSON-compatible data, for the manifest."""
        return {name: getattr(self, name) for name in _KEYS}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ChannelIdentitySettings:
        """Read limits written by :meth:`to_dict`; every key is required, none is defaulted.

        Raises
        ------
        ValueError
            For a missing or unknown key, or a value outside its range.
        """
        if not isinstance(data, Mapping) or set(data) != set(_KEYS):
            found = sorted(data) if isinstance(data, Mapping) else data
            raise ValueError(f"Expected the channel identity limits {list(_KEYS)}; got {found!r}.")
        return cls(**{name: data[name] for name in _KEYS})


def settings_entry(settings: ChannelIdentitySettings | None) -> dict[str, Any] | None:
    """Return the manifest's ``channel_identity`` value: the limits, or None."""
    return None if settings is None else settings.to_dict()


def recorded_settings(data: object) -> ChannelIdentitySettings | None:
    """Return the limits a manifest records, or None when it records none (older manifests)."""
    return None if data is None else ChannelIdentitySettings.from_dict(data)


class Unit(NamedTuple):
    """An imaging unit, by its folder labels."""

    condition: str
    biological_repeat: str
    technical_repeat: str

    @property
    def label(self) -> str:
        """``condition/biological repeat/technical repeat``, as the warnings name it."""
        return "/".join(self)


@dataclass(frozen=True)
class ChannelSignature:
    """One unit's signature in one channel: medians over its images of three log values.

    Attributes
    ----------
    unit, channel, dtype
        The unit, the channel ID and the pixel type of the unit's images.
    level, bright, spread
        ``log(x + offset)`` of the median, of the high percentile, and of the high minus the
        low percentile, with the channel's offset (:attr:`ChannelIdentityReport.offsets`).
    saturated_fraction
        Median over the images that have one; None when none has (not compared).
    images
        The images the signature is the median of.
    offset
        The offset added before the log (:func:`.channel_identity.channel_offsets`).
    raw
        Per image, ``(median, high, high - low)`` as summarised, so the signature can be taken
        again with another channel's offset; not compared.
    """

    unit: Unit
    channel: str
    dtype: str
    level: float
    bright: float
    spread: float
    saturated_fraction: float | None
    images: int
    offset: float = 0.0
    raw: tuple[tuple[float, float, float], ...] = field(default=(), compare=False, repr=False)

    @property
    def values(self) -> np.ndarray:
        """``(level, bright, spread)`` as an array."""
        return np.array([self.level, self.bright, self.spread])


@dataclass(frozen=True)
class ChannelFinding:
    """A unit's channel that does not look like itself.

    Attributes
    ----------
    code
        ``possible_channel_swap``, ``ambiguous_channel_swap`` or ``channel_unlike_others``.
    unit, channel
        Where.
    distance
        Root mean square of the robust z-scores against the channel's own reference.
    off
        The components over the limits (z-score and log difference), in :data:`COMPONENTS` order.
    reference
        The centre ``(level, bright, spread)`` of the channel's own reference for this unit.
    nearest, nearest_distance
        The other channel whose reference is nearest, and the distance to it; None without one.
    nearest_absent
        True when ``nearest`` is a channel this unit does not have.
    clear
        True when the channel clearly looks like ``nearest`` (within ``swap_limit`` and
        ``swap_ratio`` times closer than to its own reference, or a mutual pair).
    mutual
        True when the unit's ``nearest`` channel also looks like ``channel``: they look swapped.
    across_conditions
        True when a reference used was taken across conditions (too few units in the
        condition).
    alternatives
        For an ``ambiguous_channel_swap``: ``(channel, distance)`` for each other channel of
        the unit it could be swapped with, the distance being to that channel's reference,
        sorted by channel; empty otherwise.
    """

    code: str
    unit: Unit
    channel: str
    distance: float
    off: tuple[str, ...]
    reference: tuple[float, float, float]
    nearest: str | None = None
    nearest_distance: float | None = None
    nearest_absent: bool = False
    clear: bool = False
    mutual: bool = False
    across_conditions: bool = False
    alternatives: tuple[tuple[str, float], ...] = ()

    @property
    def resembles(self) -> str | None:
        """The channel this one clearly looks like, or None."""
        return self.nearest if self.clear else None


@dataclass(frozen=True)
class NotChecked:
    """A unit's channel the check could not compare, and why (information, not a warning)."""

    unit: Unit
    channel: str
    reason: str


@dataclass(frozen=True)
class ChannelIdentityReport:
    """The channel-identity check of one histogram table.

    Attributes
    ----------
    signatures
        One per unit and channel, sorted by unit and channel.
    findings
        One per unit and channel that does not look like itself, sorted the same way.
    not_checked
        The unit channels without a signature or a reference, with the reason.
    issues
        The findings as warnings; a mutual swap is one warning naming its unit once.
    compared
        How many unit channels were compared with a reference.
    pixel_types
        ``(dtype, units)`` per pixel type, sorted; units of different types are not compared.
    offsets
        ``(dtype, channel, offset)``: the offset added before the log, per pixel type and
        channel (:func:`.channel_identity.channel_offsets`).
    settings
        The limits used.
    """

    signatures: tuple[ChannelSignature, ...] = ()
    findings: tuple[ChannelFinding, ...] = ()
    not_checked: tuple[NotChecked, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()
    compared: int = 0
    pixel_types: tuple[tuple[str, int], ...] = ()
    offsets: tuple[tuple[str, str, float], ...] = ()
    settings: ChannelIdentitySettings = field(default_factory=ChannelIdentitySettings)

    @property
    def units(self) -> tuple[Unit, ...]:
        """Every unit with a signature, sorted."""
        return tuple(sorted({s.unit for s in self.signatures}))

    def finding(self, unit: Unit, channel: str) -> ChannelFinding | None:
        """Return the finding about ``channel`` of ``unit``, or None."""
        return next((f for f in self.findings if f.unit == unit and f.channel == channel), None)

    def validation_issues(self) -> list[ValidationIssue]:
        """Return the findings as warnings (:attr:`issues`)."""
        return list(self.issues)
