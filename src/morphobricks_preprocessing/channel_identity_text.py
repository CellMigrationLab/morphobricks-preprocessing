"""The words of the channel-identity check: warnings, limits and the command line's text.

Pure, ASCII only (the command line prints it on Windows consoles too); ``docs/contract.md``
§14.1.
"""

from __future__ import annotations

from collections.abc import Sequence

from .channel_identity_model import (
    CHANNEL_IDENTITY_CODES,
    CHANNEL_UNLIKE_OTHERS,
    POSSIBLE_CHANNEL_SWAP,
    ChannelFinding,
    ChannelIdentityReport,
    ChannelIdentitySettings,
)
from .validation import ValidationIssue, plural, warning

COMPONENT_WORDS = {"level": "level", "bright": "brightness", "spread": "contrast"}
ACTIONS = {
    POSSIBLE_CHANNEL_SWAP: (
        "Check the channel tags of this unit (its channel order) against how its images were "
        "acquired. If they are swapped, correct the unit's channels in the experiment "
        "description and run again. Nothing was changed or blocked."
    ),
    CHANNEL_UNLIKE_OTHERS: (
        "Look at this unit's images in this channel: check its channel tag and how the images "
        "were exported, and whether the stain, exposure or acquisition differed from the other "
        "units. Nothing was changed or blocked."
    ),
}
TITLES = {
    POSSIBLE_CHANNEL_SWAP: "Channels that look swapped with another channel of their unit",
    CHANNEL_UNLIKE_OTHERS: "Channels unlike the same channel of the other units",
}


def finding_issues(
    findings: Sequence[ChannelFinding], settings: ChannelIdentitySettings
) -> list[ValidationIssue]:
    """Return the findings as warnings; a mutual swap is one warning, naming its unit once."""
    by_place = {(f.unit, f.channel): f for f in findings}
    issues, done = [], set()
    for found in findings:
        if (found.unit, found.channel) in done:
            continue
        partner = by_place.get((found.unit, found.nearest)) if found.mutual else None
        if partner is not None:
            done.add((partner.unit, partner.channel))
            message = _swap_message(found, partner)
        else:
            message = _message(found, settings)
        issues.append(warning(found.code, message, (found.unit.label,), ACTIONS[found.code]))
    return issues


def _others(across: bool) -> str:
    """Return what the unit was compared with."""
    if across:
        return "the other units of the experiment (compared across conditions)"
    return "the other units of its condition"


def _swap_message(first: ChannelFinding, second: ChannelFinding) -> str:
    """Word a mutual swap: both channels, with their distances, in channel order."""
    first, second = sorted((first, second), key=lambda f: f.channel)
    a, b = first.channel, second.channel
    others = _others(first.across_conditions or second.across_conditions)
    return (
        f"In unit {first.unit.label}, channels {a} and {b} look swapped: compared with {others}, "
        f"its {a} looks like {b} (distance {first.distance:.1f} to {a}, "
        f"{first.nearest_distance:.1f} to {b}) and its {b} like {a} (distance "
        f"{second.distance:.1f} to {b}, {second.nearest_distance:.1f} to {a})."
    )


def _message(found: ChannelFinding, settings: ChannelIdentitySettings) -> str:
    """Word one finding: the unit, the channel, the distances and the components off."""
    unit, channel, limit = found.unit.label, found.channel, settings.outlier_limit
    others, near = _others(found.across_conditions), found.nearest
    distances = f"distance {found.distance:.1f} to {channel}"
    if near is not None:
        distances += f", {found.nearest_distance:.1f} to {near}"
    if found.code == POSSIBLE_CHANNEL_SWAP:
        return (
            f"In unit {unit}, channel {channel} does not look like {channel} in {others} but "
            f"like {near}, another channel of this unit ({distances}; limit {limit:g})."
        )
    words = " and ".join(COMPONENT_WORDS[name] for name in found.off)
    text = (
        f"In unit {unit}, channel {channel} does not look like {channel} in {others}: its "
        f"{words} {'differs' if len(found.off) == 1 else 'differ'} ({distances}; limit "
        f"{limit:g})."
    )
    if found.clear and found.nearest_absent:
        text += f" It looks like {near}, a channel this unit does not have."
    elif near is not None and found.nearest_distance < found.distance:
        text += f" Nearest channel: {near}."
    return text


def describe_settings(settings: ChannelIdentitySettings) -> str:
    """Describe the limits in plain words, for the command line and the notebook."""
    return (
        f"distance over {settings.outlier_limit:g} robust deviations and a difference of at "
        f"least {settings.min_log_difference:g} log units, references of at least "
        f"{plural(settings.min_reference, 'other unit')}, robust spread at least "
        f"{settings.mad_floor:g} log units; a swap when two channels of a unit look like each "
        f"other ({settings.partner_ratio:g} times nearer each other), or one is at distance "
        f"{settings.swap_limit:g} or less from another channel and {settings.swap_ratio:g} times "
        "nearer it than its own"
    )


def channel_identity_summary(report: ChannelIdentityReport) -> str:
    """Describe the check in one line, e.g. ``"24 unit channels compared: 1 channel warning."``.

    Only the unit channels compared with a reference count; with none, it says that no
    channel could be checked.
    """
    if not report.compared:
        return "no channel could be checked (see the list of what was not checked)."
    compared = plural(report.compared, "unit channel")
    if not report.issues:
        return f"{compared} compared: each looks like the same channel of the other units."
    return f"{compared} compared: {plural(len(report.issues), 'channel warning')}."


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
    for code in CHANNEL_IDENTITY_CODES:
        found = [issue for issue in report.issues if issue.code == code]
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
