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


def summary(unit, index, stains, dtype="uint16", jitter=None, unit_scale=None):
    """One image of ``unit`` with ``stains``: {channel: (median, low, high)}, scaled by jitter.

    ``unit_scale`` scales each channel of the unit (between-unit variation); ``jitter`` adds an
    image-to-image factor of about 4 %.
    """
    channels = tuple(sorted(stains))
    unit_scale = unit_scale or {}
    scale = 1.0 if jitter is None else float(jitter.lognormal(0.0, 0.04))
    values = [tuple(scale * unit_scale.get(c, 1.0) * v for v in stains[c]) for c in channels]
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


def experiment(units=UNITS, edit=None, dtype=None, images=3, seed=0, unit_sd=0.0, noise=True):
    """Summaries of every unit; ``edit(unit, stains)`` may change one unit's stains.

    ``unit_sd`` is the between-unit variation: each unit's channel is scaled by a lognormal
    factor of that log standard deviation (0.1 is about 10 %).
    """
    rng = np.random.default_rng(seed)
    found = []
    for unit in units:
        stains = dict(STAINS) if edit is None else edit(unit, dict(STAINS))
        kind = "uint16" if dtype is None else dtype(unit)
        scale = {c: float(rng.lognormal(0.0, unit_sd)) for c in stains} if unit_sd else None
        jitter = rng if noise else None
        found += [summary(unit, i, stains, kind, jitter, scale) for i in range(images)]
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
    assert len(report.units) == 8 and len(report.signatures) == 24 and report.compared == 24
    assert "24 unit channels compared: each looks like the same channel" in (
        channel_identity_text(report)
    )


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
    assert issue.message.count("distance") == 2  # both channels' distances are given
    assert "swapped" in issue.message and "nearest" not in issue.message.casefold()


def test_a_channel_keeps_one_swap_partner():
    """#395: A and C look alike, and B is swapped with both in one unit: one pair, not two."""
    ref = {"A": (800.0, 100.0, 4000.0), "B": (4000.0, 500.0, 20000.0), "C": (800.0, 100.0, 4000.0)}
    swapped = {"A": ref["B"], "B": ref["A"], "C": ref["B"]}
    units = [Unit("ctrl", "rep1", f"t{i}") for i in range(5)]
    summaries = [
        summary(unit, i, swapped if unit.technical_repeat == "t0" else ref)
        for unit in units
        for i in range(2)
    ]
    report = check_channel_identity(summaries)
    found = {f.channel: (f.code, f.nearest, f.mutual) for f in report.findings}
    assert found == {
        "A": (POSSIBLE_CHANNEL_SWAP, "B", True),
        "B": (POSSIBLE_CHANNEL_SWAP, "A", True),
        "C": (POSSIBLE_CHANNEL_SWAP, "B", False),
    }
    messages = [issue.message for issue in report.validation_issues()]
    assert sum("look swapped" in m for m in messages) == 1
    assert "channels A and B look swapped" in messages[0]
    assert check_channel_identity(summaries[::-1]).findings == report.findings


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
    assert "swapped" not in issue.message


def test_an_all_zero_channel_is_unlike_the_others():
    def zero(unit, stains):
        if unit == SWAPPED:
            stains["Actin"] = (0.0, 0.0, 0.0)
        return stains

    report = check_channel_identity(experiment(edit=zero))
    (found,) = report.findings
    assert (found.code, found.unit, found.channel) == (CHANNEL_UNLIKE_OTHERS, SWAPPED, "Actin")
    assert found.resembles is None


def test_a_unit_between_two_channels_is_unlike_the_others_with_its_nearest_channel():
    # DAPI three quarters of the way to Actin on the log scale: unlike DAPI, nearer Actin, but
    # not clearly within Actin, so it is no swap.
    def between(unit, stains):
        if unit == SWAPPED:
            dapi, actin = np.log(stains["DAPI"]), np.log(stains["Actin"])
            stains["DAPI"] = tuple(np.exp(dapi + 0.74 * (actin - dapi)))
        return stains

    report = check_channel_identity(experiment(edit=between, noise=False))
    (found,) = report.findings
    assert (found.code, found.channel, found.nearest) == (CHANNEL_UNLIKE_OTHERS, "DAPI", "Actin")
    assert not found.clear and found.nearest_distance < found.distance
    (issue,) = report.validation_issues()
    assert issue.message.endswith("Nearest channel: Actin.") and "swapped" not in issue.message


def test_a_channel_like_one_the_unit_does_not_have_is_no_swap():
    # Units of rep2 have a fourth channel, Cy5; a rep1 unit's Actin looks like Cy5.
    cy5 = (60.0, 20.0, 300.0)

    def stains(unit, found):
        if unit.biological_repeat == "rep2":
            found["Cy5"] = cy5
        if unit == UNITS[0]:  # ctrl/rep1/t1, without Cy5
            found["Actin"] = cy5
        return found

    report = check_channel_identity(experiment(edit=stains))
    (found,) = report.findings
    assert (found.code, found.unit, found.channel) == (CHANNEL_UNLIKE_OTHERS, UNITS[0], "Actin")
    assert found.nearest == "Cy5" and found.nearest_absent and found.clear
    (issue,) = report.validation_issues()
    assert "It looks like Cy5, a channel this unit does not have." in issue.message
    assert "swap" not in issue.message


def test_the_same_experiment_in_16_bits_and_as_0_to_1_floats_gives_the_same_findings():
    def edit(unit, stains):
        stains = swap_dapi_gfp(unit, stains)
        if unit == UNITS[5]:
            stains["Actin"] = tuple(0.2 * v for v in stains["Actin"])
        return stains

    def floats(unit, stains):
        return {c: tuple(v / 65535 for v in values) for c, values in edit(unit, stains).items()}

    def verdicts(report):
        return [
            (f.code, f.unit, f.channel, f.nearest, f.mutual, round(f.distance, 6))
            for f in report.findings
        ]

    sixteen = check_channel_identity(experiment(edit=edit))
    as_float = check_channel_identity(experiment(edit=floats, dtype=lambda unit: "float32"))
    assert len(sixteen.findings) == 3  # the mutual pair and the dim channel
    assert verdicts(as_float) == verdicts(sixteen)
    assert [i.code for i in as_float.issues] == [i.code for i in sixteen.issues]


def test_with_realistic_unit_variation_only_real_swaps_are_reported():
    # Every unit's channel varies by about 10 % (log sd 0.1); seeded, small and fast.
    for seed in range(20):
        consistent = check_channel_identity(experiment(seed=seed, unit_sd=0.1))
        assert consistent.issues == (), seed
        swapped = check_channel_identity(experiment(edit=swap_dapi_gfp, seed=seed, unit_sd=0.1))
        assert [(i.code, i.paths) for i in swapped.issues] == [
            (POSSIBLE_CHANNEL_SWAP, (SWAPPED.label,))
        ], seed


def test_a_treatment_of_a_whole_condition_is_not_a_finding():
    def treated(unit, stains):
        if unit.condition == "treated":  # GFP three times brighter, DAPI halved
            stains["GFP"] = tuple(3 * v for v in stains["GFP"])
            stains["DAPI"] = tuple(0.5 * v for v in stains["DAPI"])
        return stains

    assert check_channel_identity(experiment(edit=treated)).findings == ()
    # With one unit in its condition, ctrl/rep1/t1 is compared with the four treated units
    # (across conditions), and the treatment is then reported, saying so.
    lonely = [UNITS[0], *UNITS[4:8]]
    across = check_channel_identity(experiment(lonely, edit=treated))
    assert {f.unit for f in across.findings} == {UNITS[0]}
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
    text = channel_identity_text(report)
    assert "Not checked (9):" in text and report.compared == 0
    assert "Channel identity: no channel could be checked" in text
    assert "looks like" not in text


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
    settings = ChannelIdentitySettings(
        outlier_limit=5, min_reference=4, mad_floor=0.1, min_log_difference=1, swap_limit=1.5
    )
    assert recorded_settings(settings_entry(settings)) == settings
    assert settings_entry(None) is None and recorded_settings(None) is None
    with pytest.raises(ValueError, match="channel identity limits"):
        ChannelIdentitySettings.from_dict({"outlier_limit": 3.5})  # never defaulted
    bad_limits = (
        {"outlier_limit": 0},
        {"min_reference": 0},
        {"mad_floor": float("nan")},
        {"min_log_difference": -1},
        {"swap_ratio": 0.5},
        {"partner_ratio": 0.9},
    )
    for bad in bad_limits:
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


def test_an_8_bit_channel_whose_median_is_0_or_1_count_is_not_reported():
    # A sparse 8-bit channel: its median is 0 in most units and 1 count in others. One count
    # is the smallest step measured, so the log offset is at least one count (no 1/0.2 ratio).
    def sparse(unit, stains):
        stains = {c: tuple(v / 16 for v in values) for c, values in stains.items()}
        stains["Sparse"] = (float(unit.technical_repeat == "t2"), 0.0, 40.0)
        return stains

    report = check_channel_identity(experiment(edit=sparse, dtype=lambda unit: "uint8"))
    assert report.issues == () and report.compared == 32
    assert ("uint8", "Sparse", 1.0) in report.offsets


def test_a_bright_channel_does_not_change_a_dim_channels_verdict():
    dim = {"Dim": (20.0, 5.0, 80.0), "Dim2": (40.0, 8.0, 160.0), "Mid": (2000.0, 200.0, 8000.0)}
    bright = {"Bright": (20000.0, 500.0, 60000.0)}

    def weaker(extra):
        def edit(unit, stains):
            found = {**dim, **extra}
            if unit == SWAPPED:  # its Dim three times weaker
                found["Dim"] = tuple(v / 3 for v in found["Dim"])
            return found

        return edit

    def verdicts(report):
        return [(f.code, f.unit, f.channel, round(f.distance, 9)) for f in report.findings]

    alone = check_channel_identity(experiment(edit=weaker({})))
    with_bright = check_channel_identity(experiment(edit=weaker(bright)))
    assert [(f.code, f.unit, f.channel) for f in alone.findings] == [
        (CHANNEL_UNLIKE_OTHERS, SWAPPED, "Dim")
    ]
    assert verdicts(with_bright) == verdicts(alone)


def test_a_normal_channel_near_a_failed_one_is_not_half_of_a_swap():
    # A and B are 1.5 times apart. In one unit A's stain failed (3 times weaker, so it now
    # looks like B) and its B happens to lie a little towards A: B is nearer A's reference
    # than its own, but not twice nearer, so no swap is claimed.
    pair = {"A": (300.0, 50.0, 1500.0), "B": (200.0, 33.0, 1000.0), "C": (2000.0, 200.0, 8000.0)}

    def failed(unit, stains):
        found = dict(pair)
        if unit == SWAPPED:
            found["A"] = tuple(v / 3 for v in pair["A"])
            found["B"] = tuple(1.3 * v for v in pair["B"])
        return found

    found = experiment(edit=failed, noise=False)
    report = check_channel_identity(found)
    assert [(f.code, f.channel) for f in report.findings] == [(CHANNEL_UNLIKE_OTHERS, "A")]
    # Without the partner's ratio (any nearer is enough) the same data are called a swap.
    loose = check_channel_identity(found, ChannelIdentitySettings(partner_ratio=1.0))
    assert {i.code for i in loose.issues} == {POSSIBLE_CHANNEL_SWAP}


def test_a_sparse_bright_channel_copied_into_another_slot_is_a_close_look_alike():
    # Spots: a low median and a high top percentile, so its log offset differs a lot from
    # Actin's. A unit whose Actin holds Spots data is measured against Spots' reference on
    # Spots' scale: a clear look-alike at a small distance.
    spots = (5.0, 2.0, 5000.0)

    def copied(unit, stains):
        stains["Spots"] = spots
        if unit == SWAPPED:
            stains["Actin"] = spots
        return stains

    report = check_channel_identity(experiment(edit=copied))
    (found,) = report.findings
    assert (found.code, found.channel, found.nearest) == (POSSIBLE_CHANNEL_SWAP, "Actin", "Spots")
    assert found.nearest_distance < 0.5


def test_a_present_but_uncheckable_channel_is_not_called_absent():
    # Every unit of rep2 has Cy5; the first unit has it too, but with a negative low percentile
    # (no signature). Its Actin looks like Cy5: the unit has that channel, so the message must
    # not say it does not.
    cy5 = (60.0, 20.0, 300.0)

    def stains(unit, found):
        if unit.biological_repeat == "rep2" or unit == UNITS[0]:
            found["Cy5"] = (60.0, -20.0, 300.0) if unit == UNITS[0] else cy5
        if unit == UNITS[0]:
            found["Actin"] = cy5
        return found

    report = check_channel_identity(experiment(edit=stains))
    reasons = {(n.unit, n.channel): n.reason for n in report.not_checked}
    assert reasons[(UNITS[0], "Cy5")] == "it has negative intensities"
    (found,) = report.findings
    assert (found.unit, found.channel, found.nearest) == (UNITS[0], "Actin", "Cy5")
    assert not found.nearest_absent
    (issue,) = report.validation_issues()
    assert "does not have" not in issue.message


def test_the_compared_across_conditions_note_is_kept_for_a_nearest_channel_only_result():
    # Actin exists only in the treated units, so the nearest channel's reference of a control
    # unit's DAPI is taken across conditions, while its own reference is within its condition.
    def between(unit, stains):
        if unit.condition == "ctrl":
            del stains["Actin"]
        if unit == SWAPPED:
            stains["Actin"] = STAINS["Actin"]
            dapi, actin = np.log(stains["DAPI"]), np.log(stains["Actin"])
            stains["DAPI"] = tuple(np.exp(dapi + 0.74 * (actin - dapi)))
            del stains["Actin"]
        return stains

    report = check_channel_identity(experiment(edit=between, noise=False))
    (found,) = report.findings
    assert (found.channel, found.nearest, found.clear) == ("DAPI", "Actin", False)
    assert found.across_conditions
    (issue,) = report.validation_issues()
    assert "compared across conditions" in issue.message


def test_the_across_conditions_note_is_kept_when_the_nearest_channel_is_farther():
    # The control units have only DAPI, so every other channel's reference is taken across
    # conditions. A brighter DAPI is unlike the others, and the nearest other channel (whose
    # distance the text gives) is farther than its own reference: the note must still be there.
    def brighter(unit, stains):
        if unit.condition == "ctrl":
            del stains["Actin"], stains["GFP"]
        if unit == SWAPPED:
            stains["DAPI"] = tuple(v * 3 for v in stains["DAPI"])
        return stains

    report = check_channel_identity(experiment(edit=brighter, noise=False))
    (found,) = report.findings
    assert found.channel == "DAPI" and not found.clear
    assert found.nearest_distance > 2 * 5.0  # farther than the own reference (about 5.5)
    assert found.across_conditions
    (issue,) = report.validation_issues()
    assert "compared across conditions" in issue.message


def test_a_channel_some_images_of_a_unit_lack_is_not_checked():
    # One image of the unit lists a channel (Zeta, sorted last) the other two do not:
    # never a partial signature.
    def one_image_only(unit, stains):
        return stains

    summaries = list(experiment(edit=one_image_only, noise=False))
    target = next(
        s
        for s in summaries
        if (s.condition, s.biological_repeat, s.technical_repeat) == tuple(SWAPPED)
    )
    extra = replace(
        target,
        channels=(*target.channels, "Zeta"),
        median=(*target.median, 500.0),
        p_low=(*target.p_low, 100.0),
        p_high=(*target.p_high, 3000.0),
        saturated_fraction=(*target.saturated_fraction, 0.0),
        exact=(*target.exact, True),
    )
    summaries[summaries.index(target)] = extra
    report = check_channel_identity(summaries)
    assert not any(s.channel == "Zeta" for s in report.signatures)
    reasons = [n.reason for n in report.not_checked if n.channel == "Zeta"]
    assert reasons == ["not every image of the unit has this channel"]


def test_a_unit_with_two_pixel_types_does_not_change_the_offsets():
    def louder(unit, stains):
        if unit == UNITS[0]:
            stains = {c: tuple(10 * v for v in s) for c, s in stains.items()}
        return stains

    found = experiment(edit=louder, noise=False)
    mixed = [*found, summary(UNITS[0], 9, STAINS, "uint8")]
    without = [
        s for s in found if (s.condition, s.biological_repeat, s.technical_repeat) != UNITS[0]
    ]
    assert check_channel_identity(mixed).offsets == check_channel_identity(without).offsets


def test_no_channel_checked_does_not_blame_too_few_units():
    text = channel_identity_text(
        check_channel_identity(
            [summary(UNITS[0], 0, STAINS, "uint8"), summary(UNITS[0], 1, STAINS, "uint16")]
        )
    )
    assert "no channel could be checked" in text and "too few" not in text
    assert "see the list of what was not checked" in text
