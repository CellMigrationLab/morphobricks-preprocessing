"""Channel-identity check: does each tagged channel look like itself? (``docs/contract.md`` §14.1).

After the run, every imaging unit's channels are compared with the same channels of the other
units, from the image summaries the intensity checks already made
(:func:`.intensity.check_intensities`): no image is read, nothing is written apart from the
limits in the manifest, and nothing is blocked.

**Signature.** Per imaging unit (condition, biological repeat, technical repeat) and channel ID,
the median over the unit's images of three log values: ``level`` = log(median + o), ``bright``
= log(high percentile + o) and ``spread`` = log(high - low percentile + o), the contrast. The
offset ``o`` is :data:`OFFSET_FRACTION` of the median high percentile of the pixel type's
images, so the log is scale-free: 8-bit, 16-bit and 0-1 float images give the same verdicts
for the same relative differences. The saturated fraction is kept beside the signature but is
not compared (``saturated_image`` covers it).

**Reference.** For a unit and a channel: the per-component median and scaled MAD (1.4826 x MAD,
at least ``mad_floor``) of that channel's signatures in the *other* units of the same condition
and pixel type, so a treatment effect is not read as a swap when the condition has at least
``min_reference`` other units. With fewer, the other units of the experiment (same pixel type)
are the reference and the finding says so; with fewer still the channel is not checked (named).
A swap present in every unit of a condition is not found: it is what that condition looks like.

**Findings** (warnings, never blocking). A unit's channel is reported when its distance (root
mean square of the robust z-scores) to its own reference is over ``outlier_limit`` and one
component differs by at least ``min_log_difference``. ``possible_channel_swap`` when it clearly
looks like another channel of the same unit (within ``swap_limit`` and ``swap_ratio`` times
closer than to its own reference) or the two channels look like each other (one warning);
otherwise ``channel_unlike_others``, naming the nearest channel, or saying that it looks like a
channel the unit does not have. Units of different pixel types are never compared, and nothing
is guessed or filled in.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import NamedTuple

import numpy as np
from morphobricks_normalization.summaries import ImageSummary

from .channel_identity_model import (
    CHANNEL_IDENTITY_CODES,
    CHANNEL_UNLIKE_OTHERS,
    COMPONENTS,
    KEY,
    POSSIBLE_CHANNEL_SWAP,
    ChannelFinding,
    ChannelIdentityReport,
    ChannelIdentitySettings,
    ChannelSignature,
    NotChecked,
    Unit,
    recorded_settings,
    settings_entry,
)
from .channel_identity_text import (
    channel_identity_summary,
    channel_identity_text,
    describe_settings,
    finding_issues,
    not_compared_text,
)
from .validation import plural

MAD_SCALE = 1.4826
"""Makes the MAD a standard deviation for normally distributed values."""
OFFSET_FRACTION = 1e-3
"""The offset added before the log, as a fraction of the pixel type's median high percentile."""
_Pools = Mapping[tuple[str, str], list[ChannelSignature]]
_Reference = tuple[np.ndarray, np.ndarray, bool]


def check_channel_identity(
    summaries: Iterable[ImageSummary], settings: ChannelIdentitySettings | None = None
) -> ChannelIdentityReport:
    """Compare every unit's channels with the same channels of the other units.

    Parameters
    ----------
    summaries
        The image summaries of the table (:attr:`.intensity.IntensityReport.summaries`).
    settings
        The limits; None uses the defaults of :class:`ChannelIdentitySettings`.

    Returns
    -------
    ChannelIdentityReport
        Signatures, findings (warnings, never blocking) and what was not checked.
    """
    settings = ChannelIdentitySettings() if settings is None else settings
    summaries = list(summaries)
    offsets = pixel_type_offsets(summaries)
    signatures, not_checked = unit_signatures(summaries, offsets)
    pools: dict[tuple[str, str], list[ChannelSignature]] = {}
    for signature in signatures:
        pools.setdefault((signature.dtype, signature.channel), []).append(signature)
    unit_channels: dict[Unit, set[str]] = {}
    for signature in signatures:
        unit_channels.setdefault(signature.unit, set()).add(signature.channel)
    places: dict[tuple[Unit, str], _Place] = {}
    for signature in signatures:
        own = _reference(pools, signature, signature.channel, settings)
        if own is None:
            reason = (
                f"fewer than {plural(settings.min_reference, 'other unit')} of its pixel type "
                "have this channel"
            )
            not_checked.append(NotChecked(signature.unit, signature.channel, reason))
            continue
        place = _place(pools, signature, own, settings, unit_channels[signature.unit])
        places[(signature.unit, signature.channel)] = place
    compared = len(places)
    findings = _findings(places, settings)
    types: dict[str, set[Unit]] = {}
    for signature in signatures:
        types.setdefault(signature.dtype, set()).add(signature.unit)
    return ChannelIdentityReport(
        signatures=tuple(signatures),
        findings=tuple(findings),
        not_checked=tuple(sorted(not_checked, key=lambda n: (n.unit, n.channel))),
        issues=tuple(finding_issues(findings, settings)),
        compared=compared,
        pixel_types=tuple((dtype, len(units)) for dtype, units in sorted(types.items())),
        offsets=tuple(sorted(offsets.items())),
        settings=settings,
    )


def pixel_type_offsets(summaries: Iterable[ImageSummary]) -> dict[str, float]:
    """Return the offset added before the log, per pixel type that has a positive intensity.

    :data:`OFFSET_FRACTION` of the median of the positive high percentiles of the pixel type's
    image channels: proportional to the data, so the signatures of images scaled by a factor
    differ only by its log and every verdict is the same.
    """
    highs: dict[str, list[float]] = {}
    for summary in summaries:
        found = highs.setdefault(str(summary.dtype), [])
        found += [h for h in summary.p_high if h is not None and h > 0]
    return {
        dtype: OFFSET_FRACTION * float(np.median(values))
        for dtype, values in highs.items()
        if values
    }


def unit_signatures(
    summaries: Iterable[ImageSummary], offsets: Mapping[str, float]
) -> tuple[list[ChannelSignature], list[NotChecked]]:
    """Return every unit's signature per channel, and the unit channels without one.

    An image channel without finite pixels has no values and is left out (the intensity
    checks name it). A unit whose images have different pixel types, a unit channel with no
    image left or with a negative intensity, and a pixel type without any positive intensity
    (no offset) have no signature: they are returned as not checked, never filled in.
    """
    units: dict[Unit, list[ImageSummary]] = {}
    for summary in summaries:
        unit = Unit(summary.condition, summary.biological_repeat, summary.technical_repeat)
        units.setdefault(unit, []).append(summary)
    signatures, not_checked = [], []
    for unit, images in sorted(units.items()):
        channels = sorted({c for image in images for c in image.channels})
        dtypes = sorted({str(image.dtype) for image in images})
        reason = None
        if len(dtypes) > 1:
            reason = f"its images have different pixel types ({', '.join(dtypes)})"
        elif dtypes[0] not in offsets:
            reason = f"every intensity of its pixel type ({dtypes[0]}) is zero"
        if reason is not None:
            not_checked += [NotChecked(unit, c, reason) for c in channels]
            continue
        for channel in channels:
            found = _signature(unit, channel, dtypes[0], images, offsets[dtypes[0]])
            (signatures if isinstance(found, ChannelSignature) else not_checked).append(found)
    return signatures, not_checked


def _signature(
    unit: Unit, channel: str, dtype: str, images: list[ImageSummary], offset: float
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
        rows.append(tuple(math.log(v + offset) for v in (median, high, high - low)))
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
    pools: _Pools, signature: ChannelSignature, channel: str, settings: ChannelIdentitySettings
) -> _Reference | None:
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


def _distance(values: np.ndarray, reference: _Reference) -> float:
    """Return the root mean square of the robust z-scores of ``values`` against ``reference``."""
    return float(np.sqrt(np.mean(np.square((values - reference[0]) / reference[1]))))


class _Place(NamedTuple):
    """One compared unit channel: its distances to its own and the other channels' references."""

    signature: ChannelSignature
    own: _Reference
    distance: float
    off: tuple[str, ...]
    others: dict[str, tuple[float, bool]]
    """Distance and "across conditions" to each other channel's reference."""
    channels: frozenset[str]
    """The channels the unit has."""


def _place(
    pools: _Pools,
    signature: ChannelSignature,
    own: _Reference,
    settings: ChannelIdentitySettings,
    channels: set[str],
) -> _Place:
    """Measure one unit channel against its own reference and every other channel's.

    ``off`` are the components over the limits: a z-score over ``outlier_limit`` and a log
    difference of at least ``min_log_difference``.
    """
    values, limit = signature.values, settings.outlier_limit
    differences = values - own[0]
    off = tuple(
        name
        for name, d, score in zip(COMPONENTS, differences, differences / own[1], strict=True)
        if abs(score) > limit and abs(d) >= settings.min_log_difference
    )
    others = {}
    for dtype, channel in sorted(pools):
        if dtype == signature.dtype and channel != signature.channel:
            other = _reference(pools, signature, channel, settings)
            if other is not None:
                others[channel] = (_distance(values, other), other[2])
    return _Place(signature, own, _distance(values, own), off, others, frozenset(channels))


def _finding(
    place: _Place, settings: ChannelIdentitySettings, *, force: bool = False
) -> ChannelFinding | None:
    """Return the unit channel's finding, unclassified, or None when it looks like itself.

    It is reported when its distance is over ``outlier_limit`` and some component is ``off``
    (``force`` returns it anyway, for the partner of a mutual swap). The nearest other channel
    is kept, and whether the channel clearly looks like it.
    """
    signature, own = place.signature, place.own
    if not force and (place.distance <= settings.outlier_limit or not place.off):
        return None
    found = ChannelFinding(
        CHANNEL_UNLIKE_OTHERS,
        signature.unit,
        signature.channel,
        place.distance,
        place.off,
        tuple(float(v) for v in own[0]),
        across_conditions=own[2],
    )
    if not place.others:
        return found
    near = min(place.others, key=lambda c: place.others[c][0])
    distance, across = place.others[near]
    clear = distance <= settings.swap_limit and distance * settings.swap_ratio < place.distance
    return replace(
        found,
        nearest=near,
        nearest_distance=distance,
        nearest_absent=near not in place.channels,
        clear=clear,
        across_conditions=own[2] or (clear and across),
    )


def _leans(place: _Place, channel: str, limit: float) -> bool:
    """Return True when the channel is nearer ``channel``'s reference than its own, in ``limit``."""
    found = place.others.get(channel)
    return found is not None and found[0] < place.distance and found[0] <= limit


def _findings(
    places: Mapping[tuple[Unit, str], _Place], settings: ChannelIdentitySettings
) -> list[ChannelFinding]:
    """Return the findings, with the swaps made, sorted by unit and channel.

    A reported channel whose nearest channel ``c'`` is one of its unit's makes a mutual swap
    when it is nearer ``c'`` than its own reference and the unit's ``c'`` is nearer this
    channel than its own (both within ``outlier_limit``), the unit's ``c'`` reported or not;
    otherwise a channel that clearly looks like another channel of its unit is a swap.
    """
    limit = settings.outlier_limit
    findings = {key: f for key, place in places.items() if (f := _finding(place, settings))}
    for key in list(findings):
        found = findings[key]
        other = (found.unit, found.nearest)
        partner = places.get(other)
        if found.mutual or found.nearest_absent or partner is None:
            continue
        if not (
            _leans(places[key], found.nearest, limit) and _leans(partner, found.channel, limit)
        ):
            continue
        pair = findings.get(other) or _finding(partner, settings, force=True)
        distance, across = partner.others[found.channel]
        pair = replace(pair, nearest=found.channel, nearest_distance=distance, nearest_absent=False)
        across = across or pair.across_conditions or found.across_conditions
        across = across or places[key].others[found.nearest][1]
        for place, item in ((key, found), (other, pair)):
            findings[place] = replace(
                item, code=POSSIBLE_CHANNEL_SWAP, clear=True, mutual=True, across_conditions=across
            )
    for key, found in findings.items():
        if found.clear and not found.nearest_absent and not found.mutual:
            findings[key] = replace(found, code=POSSIBLE_CHANNEL_SWAP)
    return [findings[key] for key in sorted(findings)]


def channel_centre(
    report: ChannelIdentityReport, channel: str, dtype: str, without: Unit | None = None
) -> tuple[float, float] | None:
    """Return the median ``(bright, spread)`` of ``channel`` over the units, for the plot.

    ``without`` leaves one unit out.
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


__all__ = [
    "CHANNEL_IDENTITY_CODES",
    "CHANNEL_UNLIKE_OTHERS",
    "COMPONENTS",
    "KEY",
    "MAD_SCALE",
    "OFFSET_FRACTION",
    "POSSIBLE_CHANNEL_SWAP",
    "ChannelFinding",
    "ChannelIdentityReport",
    "ChannelIdentitySettings",
    "ChannelSignature",
    "NotChecked",
    "Unit",
    "channel_centre",
    "channel_identity_summary",
    "channel_identity_text",
    "check_channel_identity",
    "describe_settings",
    "not_compared_text",
    "pixel_type_offsets",
    "recorded_settings",
    "settings_entry",
    "unit_signatures",
]
