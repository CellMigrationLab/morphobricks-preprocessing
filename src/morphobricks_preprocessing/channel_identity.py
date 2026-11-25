"""Channel-identity check: does each tagged channel look like itself? (``docs/contract.md`` §14.1).

After the run, every imaging unit's channels are compared with the same channels of the other
units, from the image summaries the intensity checks already made
(:func:`.intensity.check_intensities`): no image is read, nothing is written apart from the
limits in the manifest, and nothing is blocked.

**Signature.** Per imaging unit (condition, biological repeat, technical repeat) and channel ID,
the median over the unit's images of three log values: ``level`` = log(median + o), ``bright``
= log(high percentile + o) and ``spread`` = log(high - low percentile + o), the contrast. The
offset ``o`` is :data:`OFFSET_FRACTION` of the channel's own median high percentile in its pixel
type (at least one count for integer types, below which nothing is measured), so the log is
scale-free: 16-bit and 0-1 float images give the same verdicts for the same relative
differences (while the integer floor is not in effect, that is when the channel's median high
percentile is above about 333 counts), and a bright channel does not change a dim channel's
verdict. A unit's channel measured against another channel's reference is summarised again
with that channel's offset, so the two are on one scale. **Limit:** float images converted
from integer data whose medians are near zero get no floor and can give false "unlike others"
warnings. The saturated fraction is kept beside the signature but is not compared
(``saturated_image`` covers it).

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
closer than to its own reference) or the two channels look like each other (one warning;
mutual pairs are chosen by strength, a channel in one pair at most, and pairs sharing a channel
that cannot be told apart are one ``ambiguous_channel_swap`` naming every channel);
otherwise ``channel_unlike_others``, naming the nearest channel, or saying that it looks like a
channel the unit does not have. Units of different pixel types are never compared, and nothing
is guessed or filled in.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import NamedTuple

import numpy as np
from morphobricks_normalization.normalization import NORMAL_CONSISTENCY
from morphobricks_normalization.summaries import ImageSummary

from .channel_identity_model import (
    AMBIGUOUS_CHANNEL_SWAP,
    CHANNEL_IDENTITY_CODES,
    CHANNEL_UNLIKE_OTHERS,
    COMPONENTS,
    KEY,
    PAIR_TOLERANCE,
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

MAD_SCALE = NORMAL_CONSISTENCY
"""Makes the MAD a standard deviation for normally distributed values (the normalization
brick's constant, reused so both bricks scale the MAD alike)."""
OFFSET_FRACTION = 0.003
"""The offset added before the log, as a fraction of the channel's median high percentile."""
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
    offsets = channel_offsets(single_type_summaries(summaries))
    signatures, not_checked = unit_signatures(summaries, offsets)
    pools: dict[tuple[str, str], list[ChannelSignature]] = {}
    for signature in signatures:
        pools.setdefault((signature.dtype, signature.channel), []).append(signature)
    unit_channels = unit_presence(summaries)
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
        offsets=tuple((dtype, channel, o) for (dtype, channel), o in sorted(offsets.items())),
        settings=settings,
    )


def _by_unit(summaries: Iterable[ImageSummary]) -> dict[Unit, list[ImageSummary]]:
    """Group the image summaries by imaging unit."""
    units: dict[Unit, list[ImageSummary]] = {}
    for summary in summaries:
        unit = Unit(summary.condition, summary.biological_repeat, summary.technical_repeat)
        units.setdefault(unit, []).append(summary)
    return units


def unit_presence(summaries: Iterable[ImageSummary]) -> dict[Unit, set[str]]:
    """Return the channels each unit has, from its images, whether or not they can be checked.

    A channel that is present but has no signature (negative intensities, mixed pixel types)
    is still the unit's: it must not be called absent.
    """
    return {
        unit: {c for image in images for c in image.channels}
        for unit, images in _by_unit(summaries).items()
    }


def single_type_summaries(summaries: Iterable[ImageSummary]) -> list[ImageSummary]:
    """Return the summaries of the units whose images share one pixel type.

    A unit with several pixel types has no signature, so it must not shape the offsets either.
    """
    return [
        image
        for images in _by_unit(summaries).values()
        if len({str(i.dtype) for i in images}) == 1
        for image in images
    ]


def channel_offsets(summaries: Iterable[ImageSummary]) -> dict[tuple[str, str], float]:
    """Return the offset added before the log, per pixel type and channel.

    :data:`OFFSET_FRACTION` of the median of the channel's positive high percentiles among the
    images of that pixel type: proportional to the channel's own data, so images scaled by a
    factor differ only by its log and a bright channel leaves a dim one's offset alone. For
    integer pixel types the offset is at least one count, the smallest step measured, so a
    median of 0 or 1 count is not read as a large ratio. A channel without any positive
    high percentile has no offset.
    """
    highs: dict[tuple[str, str], list[float]] = {}
    for summary in summaries:
        for channel, high in zip(summary.channels, summary.p_high, strict=True):
            found = highs.setdefault((str(summary.dtype), channel), [])
            if high is not None and high > 0:
                found.append(high)
    offsets = {}
    for (dtype, channel), values in highs.items():
        if values:
            offset = OFFSET_FRACTION * float(np.median(values))
            integer = np.dtype(dtype).kind in "iub"
            offsets[(dtype, channel)] = max(offset, 1.0) if integer else offset
    return offsets


def unit_signatures(
    summaries: Iterable[ImageSummary], offsets: Mapping[tuple[str, str], float]
) -> tuple[list[ChannelSignature], list[NotChecked]]:
    """Return every unit's signature per channel, and the unit channels without one.

    An image channel without finite pixels has no values and is left out (the intensity
    checks name it). A unit whose images have different pixel types, a unit channel with no
    image left or with a negative intensity, and a channel without any positive intensity in
    its pixel type (no offset) have no signature: they are returned as not checked, never
    filled in.
    """
    signatures, not_checked = [], []
    for unit, images in sorted(_by_unit(summaries).items()):
        channels = sorted({c for image in images for c in image.channels})
        dtypes = sorted({str(image.dtype) for image in images})
        if len(dtypes) > 1:
            reason = f"its images have different pixel types ({', '.join(dtypes)})"
            not_checked += [NotChecked(unit, c, reason) for c in channels]
            continue
        for channel in channels:
            offset = offsets.get((dtypes[0], channel))
            if offset is None:
                reason = f"every intensity of this channel is zero in {dtypes[0]} images"
                not_checked.append(NotChecked(unit, channel, reason))
                continue
            found = _signature(unit, channel, dtypes[0], images, offset)
            (signatures if isinstance(found, ChannelSignature) else not_checked).append(found)
    return signatures, not_checked


def _signature(
    unit: Unit, channel: str, dtype: str, images: list[ImageSummary], offset: float
) -> ChannelSignature | NotChecked:
    """Return the unit's signature in ``channel``, or why it has none."""
    rows, saturated = [], []
    if any(channel not in image.channels for image in images):
        # Images of one unit that list different channels: never a partial signature.
        return NotChecked(unit, channel, "not every image of the unit has this channel")
    for image in images:
        k = image.channels.index(channel)
        median, low, high = image.median[k], image.p_low[k], image.p_high[k]
        if median is None or low is None or high is None:
            continue  # no finite pixels: named by the intensity checks
        if low < 0:
            return NotChecked(unit, channel, "it has negative intensities")
        rows.append((median, high, high - low))
        if image.saturated_fraction[k] is not None:
            saturated.append(image.saturated_fraction[k])
    if not rows:
        return NotChecked(unit, channel, "no image of the unit has finite pixels in it")
    raw = tuple(rows)
    level, bright, spread = (float(v) for v in log_values(raw, offset))
    fraction = float(np.median(saturated)) if saturated else None
    return ChannelSignature(
        unit, channel, dtype, level, bright, spread, fraction, len(rows), offset, raw
    )


def log_values(raw: tuple[tuple[float, float, float], ...], offset: float) -> np.ndarray:
    """Return the median over images of ``log(value + offset)`` of each component."""
    return np.median(np.log(np.array(raw) + offset), axis=0)


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
    difference of at least ``min_log_difference``. Against another channel's reference the
    unit's values are summarised again with that channel's offset (from its pool), so a
    channel's data are compared with another channel on that channel's scale.
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
                offset = pools[(dtype, channel)][0].offset
                again = log_values(signature.raw, offset)
                others[channel] = (_distance(again, other), other[2])
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
        # The text always gives the nearest channel's distance: its reference's origin counts.
        across_conditions=own[2] or across,
    )


def _leans(place: _Place, channel: str, limit: float, ratio: float) -> bool:
    """Return True when the channel is ``ratio`` times nearer ``channel``'s reference than its own.

    The distance to ``channel``'s reference must also be within ``limit``.
    """
    found = place.others.get(channel)
    return found is not None and found[0] * ratio < place.distance and found[0] <= limit


class _Pair(NamedTuple):
    """A candidate mutual swap of two channels of one unit, and how strongly it is supported."""

    strength: float
    """The larger of the two cross distances ``d(u, a -> b)`` and ``d(u, b -> a)``."""
    unit: Unit
    channels: tuple[str, str]
    """Sorted, so the pair is the same whichever channel found it."""


def _pairs(
    places: Mapping[tuple[Unit, str], _Place],
    findings: Mapping[tuple[Unit, str], ChannelFinding],
    settings: ChannelIdentitySettings,
) -> list[_Pair]:
    """Return every candidate mutual pair, strongest first.

    A reported channel whose nearest channel ``c'`` is one of its unit's (and checked) is a
    candidate with ``c'`` when each leans on the other (:func:`_leans`, ``partner_ratio``,
    within ``outlier_limit``). The order (strength, then unit and channels) only makes the
    iteration repeatable: :func:`_choose` gives the same answer for any order of equal ones.
    """
    limit, ratio = settings.outlier_limit, settings.partner_ratio
    pairs = set()
    for key, found in findings.items():
        other = (found.unit, found.nearest)
        partner = places.get(other)
        if found.nearest_absent or partner is None:
            continue
        if _leans(places[key], found.nearest, limit, ratio) and _leans(
            partner, found.channel, limit, ratio
        ):
            there = places[key].others[found.nearest][0]
            back = partner.others[found.channel][0]
            channels = tuple(sorted((found.channel, found.nearest)))
            pairs.add(_Pair(max(there, back), found.unit, channels))
    return sorted(pairs)


def _choose(pairs: list[_Pair], tolerance: float) -> tuple[list[_Pair], list[list[_Pair]]]:
    """Choose the mutual pairs by strength; return the pairs kept and the ambiguous groups.

    Greedy from the strongest pair (smallest strength): a channel is in one pair at most. When
    a remaining pair sharing a channel with the strongest one (directly or through each other)
    is within ``pair_tolerance`` of its strength, none of them is chosen: they form one
    ambiguous group, and its channels are not paired with anything else. Channel names and
    input order never decide (#395).
    """
    kept, groups, used = [], [], set()
    remaining = list(pairs)
    while remaining:
        remaining = [p for p in remaining if not used & {(p.unit, c) for c in p.channels}]
        if not remaining:
            break
        first = remaining[0]
        close = [
            p
            for p in remaining
            if p.unit == first.unit and p.strength <= first.strength + tolerance
        ]
        group, channels = [first], set(first.channels)
        grown = True
        while grown:
            grown = False
            for pair in close:
                if pair not in group and channels & set(pair.channels):
                    group.append(pair)
                    channels.update(pair.channels)
                    grown = True
        if len(group) == 1:
            kept.append(first)
        else:
            groups.append(sorted(group, key=lambda p: p.channels))
        used.update((first.unit, c) for c in channels)
    return kept, groups


def _findings(
    places: Mapping[tuple[Unit, str], _Place], settings: ChannelIdentitySettings
) -> list[ChannelFinding]:
    """Return the findings, with the swaps made, sorted by unit and channel.

    A reported channel whose nearest channel ``c'`` is one of its unit's makes a candidate
    mutual swap when it is ``partner_ratio`` times nearer ``c'`` than its own reference and the
    unit's ``c'`` is ``partner_ratio`` times nearer this channel than its own (both within
    ``outlier_limit``), the unit's ``c'`` reported or not. The pairs are chosen by strength
    (:func:`_choose`): each channel is in one pair at most, and pairs that share a channel and
    cannot be told apart are one ``ambiguous_channel_swap`` naming all their channels.
    Otherwise a channel that clearly looks like another channel of its unit is a swap.
    """
    findings = {key: f for key, place in places.items() if (f := _finding(place, settings))}
    kept, groups = _choose(_pairs(places, findings, settings), settings.pair_tolerance)
    for pair in kept:
        a, b = ((pair.unit, c) for c in pair.channels)
        items = {
            key: findings.get(key) or _finding(places[key], settings, force=True) for key in (a, b)
        }
        across = any(items[key].across_conditions for key in (a, b))
        across = across or any(places[key].others[other[1]][1] for key, other in ((a, b), (b, a)))
        for key, other in ((a, b), (b, a)):
            findings[key] = replace(
                items[key],
                code=POSSIBLE_CHANNEL_SWAP,
                nearest=other[1],
                nearest_distance=places[key].others[other[1]][0],
                nearest_absent=False,
                clear=True,
                mutual=True,
                across_conditions=across,
            )
    for group in groups:
        unit = group[0].unit
        partners: dict[str, set[str]] = {}
        for pair in group:
            a, b = pair.channels
            partners.setdefault(a, set()).add(b)
            partners.setdefault(b, set()).add(a)
        for channel, others in partners.items():
            key = (unit, channel)
            item = findings.get(key) or _finding(places[key], settings, force=True)
            alternatives = tuple((c, places[key].others[c][0]) for c in sorted(others))
            across = item.across_conditions or any(places[key].others[c][1] for c in others)
            findings[key] = replace(
                item,
                code=AMBIGUOUS_CHANNEL_SWAP,
                alternatives=alternatives,
                across_conditions=across,
            )
    for key, found in findings.items():
        if found.code != CHANNEL_UNLIKE_OTHERS or found.mutual:
            continue
        if found.clear and not found.nearest_absent:
            findings[key] = replace(found, code=POSSIBLE_CHANNEL_SWAP)
    return [findings[key] for key in sorted(findings)]


__all__ = [
    "AMBIGUOUS_CHANNEL_SWAP",
    "CHANNEL_IDENTITY_CODES",
    "CHANNEL_UNLIKE_OTHERS",
    "COMPONENTS",
    "KEY",
    "MAD_SCALE",
    "OFFSET_FRACTION",
    "PAIR_TOLERANCE",
    "POSSIBLE_CHANNEL_SWAP",
    "ChannelFinding",
    "ChannelIdentityReport",
    "ChannelIdentitySettings",
    "ChannelSignature",
    "NotChecked",
    "Unit",
    "channel_identity_summary",
    "channel_identity_text",
    "check_channel_identity",
    "describe_settings",
    "not_compared_text",
    "channel_offsets",
    "log_values",
    "recorded_settings",
    "settings_entry",
    "single_type_summaries",
    "unit_presence",
    "unit_signatures",
]
