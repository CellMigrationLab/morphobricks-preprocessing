"""The channel-identity view shows why nothing was checked (contract §14.1, PR #364)."""

import pytest

from test_channel_identity import UNITS, experiment
from test_channel_identity_pairs import one_unit_off

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_channel_identity import ChannelIdentityView  # noqa: E402


def zero(unit, stains):
    return {c: (0.0, 0.0, 0.0) for c in stains}


def negative(unit, stains):
    return {c: (m, -5.0, h) for c, (m, _, h) in stains.items()}


def shown(summaries):
    view = ChannelIdentityView()
    view.show(summaries)
    return view


@pytest.mark.parametrize(
    ("summaries", "reason"),
    [
        (experiment(edit=zero), "every intensity of this channel is zero"),
        (experiment(edit=negative), "it has negative intensities"),
        (
            [
                *experiment(units=UNITS[:2]),
                *experiment(units=UNITS[:2], dtype=lambda unit: "uint8"),
            ],
            "different pixel types",
        ),
    ],
    ids=["all-zero", "all-negative", "mixed-type"],
)
def test_the_section_shows_the_status_and_the_reasons_when_nothing_was_checked(summaries, reason):
    view = shown(summaries)
    assert view.report is not None and not view.report.signatures
    assert ui.is_visible(view.box)
    assert "No channel could be checked" in view.status.value
    assert reason in view.findings.value
    assert not ui.is_visible(view.plot)
    view.clear()
    assert not ui.is_visible(view.box)


def test_the_section_with_a_plot_shows_the_plot():
    view = shown(experiment())
    assert ui.is_visible(view.box) and ui.is_visible(view.plot) and view.plot.value


def test_an_ambiguous_swap_is_one_row_naming_every_channel():
    """#395: tied mutual pairs are one row, with their own words and what to do."""
    ref = {"A": (800.0, 100.0, 4000.0), "B": (4000.0, 500.0, 20000.0), "C": (800.0, 100.0, 4000.0)}
    view = shown(one_unit_off(ref, {"A": ref["B"], "B": ref["A"], "C": ref["B"]}))
    assert view.findings.value.count("Swap, pair unclear") == 1
    assert "channels A, B and C look swapped in more than one way" in view.findings.value
    assert "Channels that look swapped in more than one way" in view.findings.value
    assert ui.is_visible(view.plot) and view.plot.value
