"""Channel-identity check: does each tagged channel look like itself? (``docs/contract.md`` §14.1).

After the run, every imaging unit's channels are compared with the same channels of the other
units, from the image summaries the intensity checks already made
(:func:`.intensity.check_intensities`): no image is read, nothing is written apart from the
limits in the manifest, and nothing is blocked.

**Signature.** Per imaging unit (condition, biological repeat, technical repeat) and channel ID,
the median over the unit's images of three log values: ``level`` = log1p(median), ``bright`` =
log1p(high percentile) and ``spread`` = log1p(high - low percentile), the contrast. The
saturated fraction is kept beside it but is not compared (``saturated_image`` covers it).

**Reference.** For a unit and a channel: the per-component median and scaled MAD (1.4826 x MAD,
at least ``mad_floor``) of that channel's signatures in the *other* units of the same condition
and pixel type, so a treatment effect is never read as a swap. With fewer than
``min_reference`` such units, the other units of the experiment (same pixel type) are the
reference and the finding says so; with fewer still the channel is not checked (named).

**Findings** (warnings, never blocking). ``channel_unlike_others``: the root mean square of the
robust z-scores of the unit's channel against its own reference is over ``outlier_limit``.
``possible_channel_swap``: the same, and the channel is closer to the reference of another
channel, within the limit; a pair whose channels each look like the other is one warning.
Units of different pixel types are never compared, and nothing is guessed or filled in.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, NamedTuple

import numpy as np
from morphobricks_normalization.summaries import ImageSummary

from .validation import ValidationIssue, plural, warning

CHANNEL_UNLIKE_OTHERS = "channel_unlike_others"
POSSIBLE_CHANNEL_SWAP = "possible_channel_swap"
CHANNEL_IDENTITY_CODES = (POSSIBLE_CHANNEL_SWAP, CHANNEL_UNLIKE_OTHERS)
KEY = "channel_identity"
"""The manifest key of the limits, beside ``intensity_checks``."""
COMPONENTS = ("level", "bright", "spread")
COMPONENT_WORDS = {"level": "level", "bright": "brightness", "spread": "contrast"}
MAD_SCALE = 1.4826
"""Makes the MAD a standard deviation for normally distributed values."""
_KEYS = ("outlier_limit", "min_reference", "mad_floor")
_ACTIONS = {
    POSSIBLE_CHANNEL_SWAP: (
        "Check the channel tags of this unit (its channel order) against how its images were "
        "acquired. If they are swapped, correct the unit's channels in the experiment "
        "description and run again. Nothing was changed or blocked."
    ),
    CHANNEL_UNLIKE_OTHERS: (
        "Look at this unit's images in this channel: check its channel tag, and whether the "
        "stain, exposure or acquisition differed from the other units. Nothing was changed or "
        "blocked."
    ),
}
TITLES = {
    POSSIBLE_CHANNEL_SWAP: "Channels that look like another channel (possible swaps)",
    CHANNEL_UNLIKE_OTHERS: "Channels unlike the same channel of the other units",
}


@dataclass(frozen=True)
class ChannelIdentitySettings:
    """Limits of the channel-identity check (contract §14.1).

    Attributes
    ----------
    outlier_limit
        Root mean square of the robust z-scores above which a unit's channel is reported;
        default 3.5.
    min_reference
        Fewest other units a reference needs (within the condition, else across the
        experiment); default 3.
    mad_floor
        Smallest robust spread of a component, in log units, so very consistent units do not
        turn tiny differences into large z-scores; default 0.05.

    Raises
    ------
    ValueError
        For a limit outside its range.
    """

    outlier_limit: float = 3.5
    min_reference: int = 3
    mad_floor: float = 0.05

    def __post_init__(self) -> None:
        for name in ("outlier_limit", "mad_floor"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float, np.number)):
                raise ValueError(f"{name} must be a number; got {value!r}.")
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f"{name} must be a positive finite number; got {value!r}.")
            object.__setattr__(self, name, float(value))
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
        log1p of the median, of the high percentile, and of the high minus the low percentile.
    saturated_fraction
        Median over the images that have one; None when none has (not compared).
    images
        The images the signature is the median of.
    """

    unit: Unit
    channel: str
    dtype: str
    level: float
    bright: float
    spread: float
    saturated_fraction: float | None
    images: int

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
        ``possible_channel_swap`` or ``channel_unlike_others``.
    unit, channel
        Where.
    distance
        Root mean square of the robust z-scores against the channel's own reference.
    off
        The components whose z-score is over the limit, in :data:`COMPONENTS` order.
    resembles, resembles_distance
        For a possible swap, the channel it looks like and the distance to its reference.
    mutual
        True when the unit's ``resembles`` channel also looks like ``channel``.
    across_conditions
        True when a reference used was taken across conditions (too few units in the
        condition).
    """

    code: str
    unit: Unit
    channel: str
    distance: float
    off: tuple[str, ...]
    resembles: str | None = None
    resembles_distance: float | None = None
    mutual: bool = False
    across_conditions: bool = False


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
    pixel_types
        ``(dtype, units)`` per pixel type, sorted; units of different types are not compared.
    settings
        The limits used.
    """

    signatures: tuple[ChannelSignature, ...] = ()
    findings: tuple[ChannelFinding, ...] = ()
    not_checked: tuple[NotChecked, ...] = ()
    pixel_types: tuple[tuple[str, int], ...] = ()
    settings: ChannelIdentitySettings = field(default_factory=ChannelIdentitySettings)

    @property
    def units(self) -> tuple[Unit, ...]:
        """Every unit with a signature, sorted."""
        return tuple(sorted({s.unit for s in self.signatures}))

    def finding(self, unit: Unit, channel: str) -> ChannelFinding | None:
        """Return the finding about ``channel`` of ``unit``, or None."""
        return next((f for f in self.findings if f.unit == unit and f.channel == channel), None)

    def validation_issues(self) -> list[ValidationIssue]:
        """Return the findings as warnings; a mutual swap is one warning, naming its unit once."""
        issues, done = [], set()
        for found in self.findings:
            if (found.unit, found.channel) in done:
                continue
            if found.mutual:
                done.add((found.unit, found.resembles))
            message = _message(found, self.settings)
            issues.append(warning(found.code, message, (found.unit.label,), _ACTIONS[found.code]))
        return issues


def check_channel_identity(
    summaries: Iterable[ImageSummary], settings: ChannelIdentitySettings | None = None
) -> ChannelIdentityReport:
    """Compare every unit's channels with the same channels of the other units.

    Parameters
    ----------
    summaries
        The image summaries of the table (:attr:`.intensity.IntensityReport.summaries`).
    settings
        The limits; None uses the defaults (3.5, 3, 0.05).

    Returns
    -------
    ChannelIdentityReport
        Signatures, findings (warnings, never blocking) and what was not checked.
    """
    settings = ChannelIdentitySettings() if settings is None else settings
    signatures, not_checked = unit_signatures(summaries)
    pools: dict[tuple[str, str], list[ChannelSignature]] = {}
    for signature in signatures:
        pools.setdefault((signature.dtype, signature.channel), []).append(signature)
    findings = []
    for signature in signatures:
        own = _reference(pools, signature, signature.channel, settings)
        if own is None:
            reason = (
                f"fewer than {plural(settings.min_reference, 'other unit')} of its pixel type "
                "have this channel"
            )
            not_checked.append(NotChecked(signature.unit, signature.channel, reason))
            continue
        found = _finding(pools, signature, own, settings)
        if found is not None:
            findings.append(found)
    findings = _mark_mutual(findings)
    types: dict[str, set[Unit]] = {}
    for signature in signatures:
        types.setdefault(signature.dtype, set()).add(signature.unit)
    return ChannelIdentityReport(
        signatures=tuple(signatures),
        findings=tuple(findings),
        not_checked=tuple(sorted(not_checked, key=lambda n: (n.unit, n.channel))),
        pixel_types=tuple((dtype, len(units)) for dtype, units in sorted(types.items())),
        settings=settings,
    )


def unit_signatures(
    summaries: Iterable[ImageSummary],
) -> tuple[list[ChannelSignature], list[NotChecked]]:
    """Return every unit's signature per channel, and the unit channels without one.

    An image channel without finite pixels has no values and is left out (the intensity
    checks name it). A unit whose images have different pixel types, a unit channel with no
    image left, and one with a negative intensity (which log1p would not compare on the same
    scale) have no signature: they are returned as not checked, never filled in.
    """
    units: dict[Unit, list[ImageSummary]] = {}
    for summary in summaries:
        unit = Unit(summary.condition, summary.biological_repeat, summary.technical_repeat)
        units.setdefault(unit, []).append(summary)
    signatures, not_checked = [], []
    for unit, images in sorted(units.items()):
        channels = sorted({c for image in images for c in image.channels})
        dtypes = sorted({str(image.dtype) for image in images})
        if len(dtypes) > 1:
            reason = f"its images have different pixel types ({', '.join(dtypes)})"
            not_checked += [NotChecked(unit, c, reason) for c in channels]
            continue
        for channel in channels:
            found = _signature(unit, channel, dtypes[0], images)
            (signatures if isinstance(found, ChannelSignature) else not_checked).append(found)
    return signatures, not_checked


def _signature(
    unit: Unit, channel: str, dtype: str, images: list[ImageSummary]
) -> ChannelSignature | NotChecked:
    """Return the unit's signature in ``channel``, or why it has none."""
    rows, saturated = [], []
    for image in images:
        if channel not in image.channels:
            continue
        k = image.channels.index(channel)
        median, low, high = image.median[k], image.p_low[k], image.p_high[k]
        if median is None or low is None or high is None:
            continue  # no finite pixels: named by the intensity checks
        if low < 0:
            return NotChecked(unit, channel, "it has negative intensities")
        rows.append((math.log1p(median), math.log1p(high), math.log1p(high - low)))
        if image.saturated_fraction[k] is not None:
            saturated.append(image.saturated_fraction[k])
    if not rows:
        return NotChecked(unit, channel, "no image of the unit has finite pixels in it")
    level, bright, spread = (float(v) for v in np.median(np.array(rows), axis=0))
    fraction = float(np.median(saturated)) if saturated else None
    return ChannelSignature(unit, channel, dtype, level, bright, spread, fraction, len(rows))


def _others(pool: list[ChannelSignature], unit: Unit) -> list[ChannelSignature]:
    """Return the signatures of ``pool`` of units other than ``unit``: never itself."""
    return [s for s in pool if s.unit != unit]


def _reference(
    pools: Mapping[tuple[str, str], list[ChannelSignature]],
    signature: ChannelSignature,
    channel: str,
    settings: ChannelIdentitySettings,
) -> tuple[np.ndarray, np.ndarray, bool] | None:
    """Return the centre, the robust spread and "across conditions" of ``channel``'s reference.

    The reference is ``channel`` in the other units of the same condition and pixel type, or,
    with fewer than ``min_reference`` of them, in the other units of that pixel type; None when
    there are fewer still.
    """
    unit = signature.unit
    others = _others(pools.get((signature.dtype, channel), []), unit)
    within = [s for s in others if s.unit.condition == unit.condition]
    for members, across in ((within, False), (others, True)):
        if len(members) >= settings.min_reference:
            values = np.array([s.values for s in members])
            centre = np.median(values, axis=0)
            spread = MAD_SCALE * np.median(np.abs(values - centre), axis=0)
            return centre, np.maximum(spread, settings.mad_floor), across
    return None


def _finding(
    pools: Mapping[tuple[str, str], list[ChannelSignature]],
    signature: ChannelSignature,
    own: tuple[np.ndarray, np.ndarray, bool],
    settings: ChannelIdentitySettings,
) -> ChannelFinding | None:
    """Return the finding about one unit channel against its reference ``own``, or None."""
    limit, where = settings.outlier_limit, (signature.unit, signature.channel)
    z = (signature.values - own[0]) / own[1]
    distance = _rms(z)
    if distance <= limit:
        return None
    off = tuple(name for name, value in zip(COMPONENTS, z, strict=True) if abs(value) > limit)
    best: tuple[float, str, bool] | None = None
    for dtype, channel in sorted(pools):
        if dtype != signature.dtype or channel == signature.channel:
            continue
        other = _reference(pools, signature, channel, settings)
        if other is not None:
            d = _rms((signature.values - other[0]) / other[1])
            if best is None or d < best[0]:
                best = (d, channel, other[2])
    if best is not None and best[0] < distance and best[0] <= limit:
        resembles, across = {"resembles": best[1], "resembles_distance": best[0]}, own[2] or best[2]
        return ChannelFinding(
            POSSIBLE_CHANNEL_SWAP, *where, distance, off, **resembles, across_conditions=across
        )
    return ChannelFinding(CHANNEL_UNLIKE_OTHERS, *where, distance, off, across_conditions=own[2])


def _rms(values: np.ndarray) -> float:
    """Return the root mean square of ``values``."""
    return float(np.sqrt(np.mean(np.square(values))))


def _mark_mutual(findings: list[ChannelFinding]) -> list[ChannelFinding]:
    """Mark the swaps whose two channels each look like the other, in one unit."""
    swaps = {(f.unit, f.channel): f.resembles for f in findings if f.code == POSSIBLE_CHANNEL_SWAP}
    marked = []
    for found in findings:
        if (
            found.resembles is not None
            and swaps.get((found.unit, found.resembles)) == found.channel
        ):
            found = replace(found, mutual=True)
        marked.append(found)
    return marked


def _message(found: ChannelFinding, settings: ChannelIdentitySettings) -> str:
    """Word one finding: the unit, the channel(s), the distances and the components off."""
    unit, channel, limit = found.unit.label, found.channel, settings.outlier_limit
    others = "the other units of its condition"
    if found.across_conditions:
        others = "the other units of the experiment (compared across conditions)"
    if found.mutual:
        first, second = sorted((channel, found.resembles))
        text = (
            f"In unit {unit}, channels {first} and {second} look swapped: each looks like the "
            f"other channel in {others}."
        )
    elif found.resembles is not None:
        text = (
            f"In unit {unit}, channel {channel} does not look like {channel} in {others} but "
            f"like {found.resembles} (distance {found.distance:.1f} to {channel}, "
            f"{found.resembles_distance:.1f} to {found.resembles}; limit {limit:g})."
        )
    else:
        words = " and ".join(COMPONENT_WORDS[name] for name in found.off)
        text = (
            f"In unit {unit}, channel {channel} does not look like {channel} in {others}: its "
            f"{words} differ (distance {found.distance:.1f}, limit {limit:g})."
        )
    return text


def describe_settings(settings: ChannelIdentitySettings) -> str:
    """Describe the limits in plain words, for the command line and the notebook."""
    return (
        f"distance over {settings.outlier_limit:g} robust deviations, references of at least "
        f"{plural(settings.min_reference, 'other unit')}, robust spread at least "
        f"{settings.mad_floor:g} log units"
    )


def channel_identity_summary(report: ChannelIdentityReport) -> str:
    """Describe the check in one line, e.g. ``"8 units checked: 1 channel warning."``."""
    units = plural(len(report.units), "unit")
    issues = report.validation_issues()
    if not issues:
        return f"{units} checked: every channel looks like itself."
    return f"{units} checked: {plural(len(issues), 'channel warning')}."


def not_compared_text(report: ChannelIdentityReport) -> str | None:
    """Name the pixel types whose units were not compared with each other, or None."""
    if len(report.pixel_types) < 2:
        return None
    types = ", ".join(f"{dtype} ({plural(n, 'unit')})" for dtype, n in report.pixel_types)
    return f"Units of different pixel types were not compared with each other: {types}."


def channel_identity_text(report: ChannelIdentityReport) -> str:
    """Describe the check as plain ASCII text: summary, limits, warnings by kind, not checked."""
    lines = [
        f"Channel identity: {channel_identity_summary(report)}",
        f"  Limits: {describe_settings(report.settings)}.",
    ]
    issues = report.validation_issues()
    for code in CHANNEL_IDENTITY_CODES:
        found = [issue for issue in issues if issue.code == code]
        if found:
            lines += ["", f"{TITLES[code]} ({code}, {len(found)}):"]
            lines += [f"  - {issue.message}" for issue in found]
            lines.append(f"  What to do: {found[0].action}")
    if report.not_checked:
        lines += ["", f"Not checked ({len(report.not_checked)}):"]
        lines += [
            f"  - {n.unit.label}, channel {n.channel}: {n.reason}" for n in report.not_checked
        ]
    note = not_compared_text(report)
    if note:
        lines += ["", note]
    return "\n".join(lines)


def channel_centre(
    report: ChannelIdentityReport, channel: str, dtype: str, without: Unit | None = None
) -> tuple[float, float] | None:
    """Return the median ``(bright, spread)`` of ``channel`` over the units, for the plot.

    ``without`` leaves one unit out (the flagged one, whose arrow points at the others).
    """
    values = [
        (s.bright, s.spread)
        for s in report.signatures
        if s.channel == channel and s.dtype == dtype and s.unit != without
    ]
    if not values:
        return None
    bright, spread = np.median(np.array(values), axis=0)
    return float(bright), float(spread)
