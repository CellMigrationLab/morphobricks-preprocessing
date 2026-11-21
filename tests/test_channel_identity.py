"""The channel-identity check (contract §14.1): synthetic image summaries, no image read."""

from dataclasses import replace

import numpy as np
import pytest
from morphobricks_normalization.summaries import ImageSummary

from morphobricks_preprocessing import channel_identity
from morphobricks_preprocessing.channel_identity import (
    CHANNEL_UNLIKE_OTHERS,
    POSSIBLE_CHANNEL_SWAP,
    ChannelIdentitySettings,
    Unit,
    channel_identity_text,
    check_channel_identity,
    recorded_settings,
    settings_entry,
)

# (median, low, high percentile) of three stains: clearly different levels and contrasts.
STAINS = {
    "Actin": (300.0, 50.0, 1500.0),
    "DAPI": (800.0, 100.0, 4000.0),
    "GFP": (150.0, 80.0, 600.0),
}
UNITS = [
    Unit(condition, repeat, tech)
    for condition in ("ctrl", "treated")
    for repeat in ("rep1", "rep2")
    for tech in ("t1", "t2")
]


def summary(unit, index, stains, dtype="uint16", jitter=None):
    """One image of ``unit`` with ``stains``: {channel: (median, low, high)}, scaled by jitter."""
    channels = tuple(sorted(stains))
    scale = 1.0 if jitter is None else float(jitter.lognormal(0.0, 0.04))
    values = [tuple(scale * v for v in stains[c]) for c in channels]
    return ImageSummary(
        source_relative_path=f"{'/'.join(unit)}/img_{index}.tif",
        condition=unit.condition,
        biological_repeat=unit.biological_repeat,
        technical_repeat=unit.technical_repeat,
        dtype=np.dtype(dtype),
        percentile=(1.0, 99.8),
        channels=channels,
        median=tuple(v[0] for v in values),
        p_low=tuple(v[1] for v in values),
        p_high=tuple(v[2] for v in values),
        saturated_fraction=tuple(0.0 for _ in channels),
        exact=tuple(True for _ in channels),
    )


def experiment(units=UNITS, edit=None, dtype=None, images=3, seed=0):
    """Summaries of every unit; ``edit(unit, stains)`` may change one unit's stains."""
    rng = np.random.default_rng(seed)
    found = []
    for unit in units:
        stains = dict(STAINS) if edit is None else edit(unit, dict(STAINS))
        kind = "uint16" if dtype is None else dtype(unit)
        found += [summary(unit, i, stains, kind, rng) for i in range(images)]
    return found


SWAPPED = UNITS[2]  # ctrl/rep2/t1


def swap_dapi_gfp(unit, stains):
    if unit == SWAPPED:
        stains["DAPI"], stains["GFP"] = stains["GFP"], stains["DAPI"]
    return stains


def test_consistent_units_give_no_finding():
    report = check_channel_identity(experiment())
    assert report.findings == () and report.not_checked == ()
    assert report.validation_issues() == []
    assert len(report.units) == 8 and len(report.signatures) == 24
    assert "every channel looks like itself" in channel_identity_text(report)


def test_two_swapped_channels_give_one_mutual_swap():
    report = check_channel_identity(experiment(edit=swap_dapi_gfp))
    assert {(f.unit, f.channel, f.resembles) for f in report.findings} == {
        (SWAPPED, "DAPI", "GFP"),
        (SWAPPED, "GFP", "DAPI"),
    }
    assert all(f.code == POSSIBLE_CHANNEL_SWAP and f.mutual for f in report.findings)
    assert not any(f.across_conditions for f in report.findings)
    (issue,) = report.validation_issues()
    assert issue.code == POSSIBLE_CHANNEL_SWAP and not issue.is_error
    assert issue.paths == ("ctrl/rep2/t1",)
    assert "channels DAPI and GFP look swapped" in issue.message


def test_a_dim_channel_is_unlike_the_others_but_no_swap():
    def dim(unit, stains):
        if unit == SWAPPED:
            stains["Actin"] = tuple(0.2 * v for v in stains["Actin"])
        return stains

    report = check_channel_identity(experiment(edit=dim))
    (found,) = report.findings
    assert (found.code, found.unit, found.channel) == (CHANNEL_UNLIKE_OTHERS, SWAPPED, "Actin")
    assert found.resembles is None and set(found.off) == {"level", "bright", "spread"}
    (issue,) = report.validation_issues()
    assert issue.code == CHANNEL_UNLIKE_OTHERS
    assert "its level and brightness and contrast differ" in issue.message


def test_a_treatment_of_a_whole_condition_is_never_a_finding():
    def treated(unit, stains):
        if unit.condition == "treated":  # GFP three times brighter, DAPI halved
            stains["GFP"] = tuple(3 * v for v in stains["GFP"])
            stains["DAPI"] = tuple(0.5 * v for v in stains["DAPI"])
        return stains

    assert check_channel_identity(experiment(edit=treated)).findings == ()
    # Compared across conditions instead (one unit per condition), it would be reported.
    lonely = [Unit(c, "rep1", "t1") for c in ("ctrl", "treated")] + UNITS[4:7]
    across = check_channel_identity(experiment(lonely, edit=treated))
    assert {f.unit for f in across.findings} == {Unit("ctrl", "rep1", "t1")}
    assert all(f.across_conditions for f in across.findings)
    assert "compared across conditions" in across.validation_issues()[0].message


def test_units_of_different_pixel_types_are_not_compared():
    eight_bit = {UNITS[0], UNITS[1]}

    def stains(unit, found):
        if unit in eight_bit:  # the same stains in 8 bits: much lower values
            return {c: tuple(v / 16 for v in values) for c, values in found.items()}
        return found

    report = check_channel_identity(
        experiment(edit=stains, dtype=lambda unit: "uint8" if unit in eight_bit else "uint16")
    )
    assert report.findings == ()
    assert report.pixel_types == (("uint16", 6), ("uint8", 2))
    assert {n.unit for n in report.not_checked} == eight_bit
    assert all("of its pixel type" in n.reason for n in report.not_checked)
    text = channel_identity_text(report)
    assert "not compared with each other: uint16 (6 units), uint8 (2 units)" in text


def test_a_unit_with_images_of_two_pixel_types_is_not_checked():
    found = experiment()
    mixed = summary(UNITS[0], 9, STAINS, "uint8")
    report = check_channel_identity([*found, mixed])
    assert {n.unit for n in report.not_checked} == {UNITS[0]}
    assert all("different pixel types" in n.reason for n in report.not_checked)


def test_too_few_units_are_not_checked():
    report = check_channel_identity(experiment(UNITS[:3], edit=swap_dapi_gfp))
    assert report.findings == ()
    assert len(report.not_checked) == 9  # 3 units x 3 channels
    assert all("fewer than 3 other units" in n.reason for n in report.not_checked)
    assert "Not checked (9):" in channel_identity_text(report)


def test_negative_intensities_and_blank_channels_are_not_checked():
    found = experiment()
    blank = (None, None, None)  # Actin without finite pixels in every image of the first unit
    for i in range(3):
        s = found[i]
        median, low, high = (
            (b, *v[1:]) for b, v in zip(blank, (s.median, s.p_low, s.p_high), strict=True)
        )
        found[i] = replace(s, median=median, p_low=low, p_high=high)
    for i in range(3, 6):  # Actin with a negative low percentile in the second unit
        found[i] = replace(found[i], p_low=(-5.0, *found[i].p_low[1:]))
    report = check_channel_identity(found)
    reasons = {(n.unit, n.channel): n.reason for n in report.not_checked}
    assert found[0].channels[0] == "Actin"
    assert reasons[(UNITS[0], "Actin")].startswith("no image of the unit has finite pixels")
    assert reasons[(UNITS[1], "Actin")] == "it has negative intensities"
    assert len(reasons) == 2


def test_the_limits_are_typed_and_recorded_exactly():
    settings = ChannelIdentitySettings(outlier_limit=5, min_reference=4, mad_floor=0.1)
    assert recorded_settings(settings_entry(settings)) == settings
    assert settings_entry(None) is None and recorded_settings(None) is None
    with pytest.raises(ValueError, match="channel identity limits"):
        ChannelIdentitySettings.from_dict({"outlier_limit": 3.5})  # never defaulted
    for bad in ({"outlier_limit": 0}, {"min_reference": 0}, {"mad_floor": float("nan")}):
        with pytest.raises(ValueError):
            ChannelIdentitySettings(**bad)
    # Changing a limit recomputes nothing: the same summaries, another verdict.
    found = experiment(edit=swap_dapi_gfp)
    loose = ChannelIdentitySettings(outlier_limit=1000)
    assert check_channel_identity(found, loose).findings == ()


# Mutation checks: the tests above fail when the reference is not the other units.


def test_the_swap_test_fails_when_the_reference_is_the_unit_itself(monkeypatch):
    def itself(pool, unit):
        return [s for s in pool if s.unit == unit] * 3

    monkeypatch.setattr(channel_identity, "_others", itself)
    with pytest.raises(AssertionError):
        test_two_swapped_channels_give_one_mutual_swap()


def test_the_too_few_test_fails_when_the_reference_includes_the_unit(monkeypatch):
    monkeypatch.setattr(channel_identity, "_others", lambda pool, unit: list(pool))
    with pytest.raises(AssertionError):
        test_too_few_units_are_not_checked()
