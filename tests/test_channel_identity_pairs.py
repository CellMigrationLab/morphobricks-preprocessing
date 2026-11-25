"""Mutual channel swaps chosen by strength, never by name (contract §14.1, #395)."""

import itertools

import pytest

from morphobricks_preprocessing import channel_identity
from morphobricks_preprocessing.channel_identity import (
    AMBIGUOUS_CHANNEL_SWAP,
    PAIR_TOLERANCE,
    POSSIBLE_CHANNEL_SWAP,
    ChannelIdentitySettings,
    Unit,
    channel_identity_text,
    check_channel_identity,
)
from test_channel_identity import summary


def one_unit_off(ref, shown):
    """Two images in each of five units of ``ref``, except ``t0``, which shows ``shown``."""
    units = [Unit("ctrl", "rep1", f"t{i}") for i in range(5)]
    return [
        summary(unit, i, shown if unit.technical_repeat == "t0" else ref)
        for unit in units
        for i in range(2)
    ]


# #395, the review's case: "dim" shows "bright"'s content, "bright" shows exactly "faint"'s
# reference and "faint" shows "bright"'s content. bright <-> faint is the mutual swap (distance
# 0 both ways); dim <-> bright is weaker (bright is at 1.1 from dim's reference).
ROLE_REF = {
    "dim": (1000.0, 125.0, 5000.0),
    "bright": (4000.0, 500.0, 20000.0),
    "faint": (800.0, 100.0, 4000.0),
}
ROLE_SHOWN = {"dim": "bright", "bright": "faint", "faint": "bright"}


def roles_named(names):
    """The review's case with the roles dim, bright and faint named ``names``."""
    name = dict(zip(("dim", "bright", "faint"), names, strict=True))
    ref = {name[role]: values for role, values in ROLE_REF.items()}
    shown = {name[role]: ROLE_REF[other] for role, other in ROLE_SHOWN.items()}
    return name, one_unit_off(ref, shown)


def test_the_strongest_mutual_pair_is_chosen_not_the_first_name():
    """#395: B <-> C (distance 0 each way) is the swap; A stays a plain look-alike of B."""
    report = check_channel_identity(roles_named("ABC")[1])
    found = {f.channel: (f.code, f.nearest, f.mutual) for f in report.findings}
    assert found == {
        "A": (POSSIBLE_CHANNEL_SWAP, "B", False),
        "B": (POSSIBLE_CHANNEL_SWAP, "C", True),
        "C": (POSSIBLE_CHANNEL_SWAP, "B", True),
    }
    messages = sorted(issue.message for issue in report.validation_issues())
    assert len(messages) == 2
    assert "channel A does not look like A" in messages[0] and "but like B" in messages[0]
    assert "channels B and C look swapped" in messages[1]
    assert "A and B look swapped" not in " ".join(messages)


@pytest.mark.parametrize("names", sorted(itertools.permutations("ABC")))
def test_the_chosen_pair_does_not_depend_on_channel_names(names):
    """#395: renaming the channels (and reordering the images) gives the same physical answer."""
    name, summaries = roles_named(names)
    for order in (summaries, summaries[::-1]):
        report = check_channel_identity(order)
        found = {f.channel: (f.code, f.nearest, f.mutual) for f in report.findings}
        dim, bright, faint = name["dim"], name["bright"], name["faint"]
        assert found == {
            dim: (POSSIBLE_CHANNEL_SWAP, bright, False),
            bright: (POSSIBLE_CHANNEL_SWAP, faint, True),
            faint: (POSSIBLE_CHANNEL_SWAP, bright, True),
        }
        swaps = [i.message for i in report.validation_issues() if "look swapped" in i.message]
        pair = " and ".join(sorted((bright, faint)))
        assert len(swaps) == 1 and f"channels {pair} look swapped" in swaps[0]


def test_tied_mutual_pairs_are_one_ambiguous_finding():
    """#395: A and C look alike and B is swapped with both: no pair is picked, all are named."""
    ref = {"A": (800.0, 100.0, 4000.0), "B": (4000.0, 500.0, 20000.0), "C": (800.0, 100.0, 4000.0)}
    summaries = one_unit_off(ref, {"A": ref["B"], "B": ref["A"], "C": ref["B"]})
    report = check_channel_identity(summaries)
    assert {f.channel: (f.code, f.mutual) for f in report.findings} == {
        c: (AMBIGUOUS_CHANNEL_SWAP, False) for c in "ABC"
    }
    alternatives = {f.channel: tuple(c for c, _ in f.alternatives) for f in report.findings}
    assert alternatives == {"A": ("B",), "B": ("A", "C"), "C": ("B",)}
    (issue,) = report.validation_issues()
    assert issue.code == AMBIGUOUS_CHANNEL_SWAP and not issue.is_error
    assert issue.paths == ("ctrl/rep1/t0",)
    assert "channels A, B and C look swapped in more than one way" in issue.message
    assert "its B looks like A or C" in issue.message and "no pair is named" in issue.message
    assert "every channel named" in issue.action and "names no pair" in issue.action
    assert check_channel_identity(summaries[::-1]).findings == report.findings
    text = channel_identity_text(report)
    assert f"in more than one way ({AMBIGUOUS_CHANNEL_SWAP}, 1):" in text


def strengths_of(monkeypatch, summaries, settings=None):
    """Run the check and return ``({channels: strength}, report)`` of the candidate pairs."""
    seen = []
    choose = channel_identity._choose

    def spy(pairs, tolerance):
        seen.append(pairs)
        return choose(pairs, tolerance)

    monkeypatch.setattr(channel_identity, "_choose", spy)
    report = check_channel_identity(summaries, settings)
    return {p.channels: p.strength for p in seen[0]}, report


def faint_scaled(factor):
    """The review's roles with faint's reference ``factor`` times its base; faint shows bright's.

    dim shows bright, bright shows faint's (scaled) reference and faint shows bright's: bright
    <-> faint is exactly mutual (strength 0), dim <-> bright is the weaker, closer to a tie the
    nearer faint's reference is to dim's (``factor`` 1.25 is dim's reference exactly).
    """
    ref = dict(ROLE_REF, faint=tuple(factor * v for v in ROLE_REF["faint"]))
    shown = {role: ref[other] for role, other in ROLE_SHOWN.items()}
    return one_unit_off(ref, shown)


def test_a_near_tie_just_inside_the_tolerance_is_ambiguous(monkeypatch):
    """#395: strengths 0 and about 0.46 (within the default pair_tolerance) are one group."""
    strengths, report = strengths_of(monkeypatch, faint_scaled(1.14))
    assert strengths[("bright", "faint")] == pytest.approx(0.0, abs=1e-6)
    assert 0.40 < strengths[("bright", "dim")] < PAIR_TOLERANCE
    assert {f.channel: f.code for f in report.findings} == {
        c: AMBIGUOUS_CHANNEL_SWAP for c in ROLE_REF
    }


def test_a_near_tie_just_outside_the_tolerance_is_a_clean_swap(monkeypatch):
    """#395: strengths 0 and about 0.63 are not a tie: bright <-> faint, dim a look-alike."""
    strengths, report = strengths_of(monkeypatch, faint_scaled(1.10))
    assert strengths[("bright", "faint")] == pytest.approx(0.0, abs=1e-6)
    assert PAIR_TOLERANCE < strengths[("bright", "dim")] < 0.75
    found = {f.channel: (f.code, f.nearest, f.mutual) for f in report.findings}
    assert found == {
        "dim": (POSSIBLE_CHANNEL_SWAP, "bright", False),
        "bright": (POSSIBLE_CHANNEL_SWAP, "faint", True),
        "faint": (POSSIBLE_CHANNEL_SWAP, "bright", True),
    }
    swaps = [i.message for i in report.validation_issues() if "look swapped" in i.message]
    assert len(swaps) == 1 and "channels bright and faint look swapped" in swaps[0]


CLEAN_TIE_BREAK = {
    "dim": (POSSIBLE_CHANNEL_SWAP, "bright", False),
    "bright": (POSSIBLE_CHANNEL_SWAP, "faint", True),
    "faint": (POSSIBLE_CHANNEL_SWAP, "bright", True),
}


@pytest.mark.parametrize(
    ("factor", "tolerance", "ambiguous"),
    [
        (1.14, 0.3, False),  # strengths 0 and about 0.46
        (1.14, 0.5, True),
        (1.14, 0.8, True),
        (1.10, 0.3, False),  # strengths 0 and about 0.63
        (1.10, 0.5, False),
        (1.10, 0.8, True),
    ],
)
def test_the_recorded_pair_tolerance_decides_a_near_tie(monkeypatch, factor, tolerance, ambiguous):
    """Owner decision of 24 November 2026: ``pair_tolerance`` is a recorded limit, not a constant.

    The same summaries give a clean swap or an ambiguous one by the limits' tolerance only,
    and the ambiguous warning names the tolerance it was found with.
    """
    settings = ChannelIdentitySettings(pair_tolerance=tolerance)
    strengths, report = strengths_of(monkeypatch, faint_scaled(factor), settings)
    assert strengths[("bright", "faint")] == pytest.approx(0.0, abs=1e-6)
    assert (strengths[("bright", "dim")] <= tolerance) == ambiguous
    assert report.settings.pair_tolerance == tolerance
    found = {f.channel: (f.code, f.nearest, f.mutual) for f in report.findings}
    if not ambiguous:
        assert found == CLEAN_TIE_BREAK
        return
    assert {c: code for c, (code, _, _) in found.items()} == {
        c: AMBIGUOUS_CHANNEL_SWAP for c in ROLE_REF
    }
    (issue,) = report.validation_issues()
    assert f"within {tolerance:g} robust deviations of each other" in issue.message


def units_off(shown_by_unit, ref, conditions=("ctrl", "treated"), per_condition=6):
    """Units of every condition showing ``ref``, except the units in ``shown_by_unit``."""
    units = [Unit(c, "rep1", f"t{i}") for c in conditions for i in range(per_condition)]
    return [summary(u, i, shown_by_unit.get(u, ref)) for u in units for i in range(2)]


FOUR = {
    "A": (1000.0, 125.0, 5000.0),
    "B": (4000.0, 500.0, 20000.0),
    "C": (300.0, 50.0, 1500.0),
    "D": (150.0, 80.0, 600.0),
}


def test_two_independent_swaps_are_two_warnings():
    """#395: A <-> B and C <-> D are separate pairs, each its own warning, nothing ambiguous."""
    shown = {"A": FOUR["B"], "B": FOUR["A"], "C": FOUR["D"], "D": FOUR["C"]}
    report = check_channel_identity(one_unit_off(FOUR, shown))
    assert {f.channel: (f.code, f.nearest, f.mutual) for f in report.findings} == {
        "A": (POSSIBLE_CHANNEL_SWAP, "B", True),
        "B": (POSSIBLE_CHANNEL_SWAP, "A", True),
        "C": (POSSIBLE_CHANNEL_SWAP, "D", True),
        "D": (POSSIBLE_CHANNEL_SWAP, "C", True),
    }
    messages = sorted(i.message for i in report.validation_issues())
    assert len(messages) == 2
    assert "channels A and B look swapped" in messages[0]
    assert "channels C and D look swapped" in messages[1]


def test_an_ambiguous_group_does_not_hold_back_a_clean_swap_of_other_channels():
    """#395: five channels: A, B, C (A and C alike) are one group; D <-> E is still a pair."""
    ref = {"A": FOUR["A"], "B": FOUR["B"], "C": FOUR["A"], "D": FOUR["C"], "E": FOUR["D"]}
    shown = {"A": ref["B"], "B": ref["A"], "C": ref["B"], "D": ref["E"], "E": ref["D"]}
    report = check_channel_identity(one_unit_off(ref, shown))
    assert {f.channel: (f.code, f.mutual) for f in report.findings} == {
        "A": (AMBIGUOUS_CHANNEL_SWAP, False),
        "B": (AMBIGUOUS_CHANNEL_SWAP, False),
        "C": (AMBIGUOUS_CHANNEL_SWAP, False),
        "D": (POSSIBLE_CHANNEL_SWAP, True),
        "E": (POSSIBLE_CHANNEL_SWAP, True),
    }
    messages = sorted(i.message for i in report.validation_issues())
    assert len(messages) == 2
    assert "channels A, B and C look swapped in more than one way" in messages[0]
    assert "channels D and E look swapped" in messages[1]


def test_a_three_cycle_is_three_plain_look_alikes():
    """Current behaviour, locked: A shows B, B shows C, C shows A is no mutual pair at all."""
    ref = {c: FOUR[c] for c in "ABC"}
    report = check_channel_identity(
        one_unit_off(ref, {"A": ref["B"], "B": ref["C"], "C": ref["A"]})
    )
    assert {f.channel: (f.code, f.nearest, f.mutual) for f in report.findings} == {
        "A": (POSSIBLE_CHANNEL_SWAP, "B", False),
        "B": (POSSIBLE_CHANNEL_SWAP, "C", False),
        "C": (POSSIBLE_CHANNEL_SWAP, "A", False),
    }
    messages = [i.message for i in report.validation_issues()]
    assert len(messages) == 3
    assert not any("look swapped" in m for m in messages)
    assert all("does not look like" in m for m in messages)


@pytest.mark.parametrize("names", sorted(itertools.permutations("XYZ")))
def test_the_ambiguous_group_does_not_depend_on_names_or_image_order(names):
    """#395: the tied case is the same physical answer under every naming and image order."""
    one, two, alike = names  # roles: faint-like, bright, and the channel alike to the first
    ref = {one: ROLE_REF["faint"], two: ROLE_REF["bright"], alike: ROLE_REF["faint"]}
    shown = {one: ref[two], two: ref[one], alike: ref[two]}
    summaries = one_unit_off(ref, shown)
    expected = {
        two: tuple(sorted((one, alike))),
        one: (two,),
        alike: (two,),
    }
    for order in (summaries, summaries[::-1]):
        report = check_channel_identity(order)
        assert {f.channel: f.code for f in report.findings} == {
            c: AMBIGUOUS_CHANNEL_SWAP for c in names
        }
        found = {f.channel: tuple(c for c, _ in f.alternatives) for f in report.findings}
        assert found == expected
        (issue,) = report.validation_issues()
        assert f"channels {', '.join(sorted(names)[:2])} and {sorted(names)[2]} look" in (
            issue.message
        )


def test_units_of_other_conditions_and_other_units_do_not_interact():
    """#395: a tie in one unit and a clean swap of the same channels in others stay apart."""
    ref = {"A": FOUR["A"], "B": FOUR["B"], "C": FOUR["A"]}
    tie = Unit("ctrl", "rep1", "t0")
    swap_same_condition = Unit("ctrl", "rep1", "t1")
    swap_other_condition = Unit("treated", "rep1", "t0")
    clean = {"A": ref["B"], "B": ref["A"], "C": ref["C"]}
    summaries = units_off(
        {
            tie: {"A": ref["B"], "B": ref["A"], "C": ref["B"]},
            swap_same_condition: clean,
            swap_other_condition: clean,
        },
        ref,
    )
    report = check_channel_identity(summaries)
    by_unit = {}
    for f in report.findings:
        by_unit.setdefault(f.unit, {})[f.channel] = (f.code, f.mutual)
    assert by_unit[tie] == {c: (AMBIGUOUS_CHANNEL_SWAP, False) for c in "ABC"}
    for unit in (swap_same_condition, swap_other_condition):
        assert by_unit[unit]["A"] == (POSSIBLE_CHANNEL_SWAP, True), by_unit[unit]
        assert by_unit[unit]["B"] == (POSSIBLE_CHANNEL_SWAP, True), by_unit[unit]
    assert set(by_unit) == {tie, swap_same_condition, swap_other_condition}


def test_the_ambiguous_message_says_what_the_tolerance_measures_and_why_no_pair():
    """#395: "within 0.5" names its unit, and look-alike references are named as the cause."""
    ref = {"A": ROLE_REF["faint"], "B": ROLE_REF["bright"], "C": ROLE_REF["faint"]}
    report = check_channel_identity(
        one_unit_off(ref, {"A": ref["B"], "B": ref["A"], "C": ref["B"]})
    )
    (issue,) = report.validation_issues()
    assert (
        f"within {PAIR_TOLERANCE:g} robust deviations of each other in the larger of their two "
        "cross distances, so no pair is named." in issue.message
    )
    assert "A and C look alike in the other units, which is why no pair can be chosen" in (
        issue.message
    )
