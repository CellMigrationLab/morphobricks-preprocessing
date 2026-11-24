"""Mutual channel swaps chosen by strength, never by name (contract §14.1, #395)."""

import itertools

import pytest

from morphobricks_preprocessing.channel_identity import (
    AMBIGUOUS_CHANNEL_SWAP,
    POSSIBLE_CHANNEL_SWAP,
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
