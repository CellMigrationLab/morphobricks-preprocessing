"""The channel-identity check in a run: a deliberate tag swap in one unit's description."""

import dataclasses
import json

import numpy as np
import pytest
from morphobricks_normalization.csv_io import read_plane_histograms

from described_tree import tree_description
from morphobricks_preprocessing import prefill_mapping, read_manifest, run_experiment
from morphobricks_preprocessing.channel_identity import (
    POSSIBLE_CHANNEL_SWAP,
    ChannelIdentitySettings,
    check_channel_identity,
)
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME
from morphobricks_preprocessing.description_write import canonical_bytes
from morphobricks_preprocessing.discovery import default_output_folder
from morphobricks_preprocessing.run.preflight import preflight
from tiff_tree import write_data

CYX = prefill_mapping("CYX")
UNITS = ("t1", "t2", "t3", "t4", "t5")
SWAPPED = "t3"


def write_experiment(root):
    """Five units of two images: channel 1 bright and contrasted, channel 2 dim and flat."""
    rng = np.random.default_rng(3)
    for k, unit in enumerate(UNITS):
        for i in range(2):
            bright = rng.normal(150 * (1 + 0.02 * k), 25, (16, 16))
            dim = rng.normal(30 * (1 + 0.02 * k), 4, (16, 16))
            image = np.clip(np.stack([bright, dim]), 0, 254).astype(np.uint8)
            write_data(root / "ctrl" / "rep1" / unit / f"img_{i}.tif", image, axes="CYX")
    return root


def publish_with_swapped_tags(root):
    """Publish the description the user confirmed, with unit t3's two channel tags swapped."""
    output = default_output_folder(root)
    description = tree_description(preflight(root, output), CYX)
    repeats = {}
    for repeat_id, repeat in description.repeats.items():
        members = {}
        for condition_id, member in repeat.members.items():
            units = {
                unit_id: (
                    dataclasses.replace(unit, channels=tuple(reversed(unit.channels)))
                    if unit.source_folder == SWAPPED
                    else unit
                )
                for unit_id, unit in member.technical_repeats.items()
            }
            members[condition_id] = dataclasses.replace(member, technical_repeats=units)
        repeats[repeat_id] = dataclasses.replace(repeat, members=members)
    swapped = dataclasses.replace(description, repeats=repeats)
    assert swapped != description
    output.mkdir(parents=True, exist_ok=True)
    (output / DESCRIPTION_FILENAME).write_bytes(canonical_bytes(swapped))
    return output


@pytest.fixture
def swapped_run(tmp_path):
    root = write_experiment(tmp_path / "exp")
    publish_with_swapped_tags(root)
    return root, run_experiment(root, CYX)


def test_a_run_flags_a_deliberate_tag_swap_and_never_blocks(swapped_run):
    root, result = swapped_run
    assert result.written and result.ok
    report = result.channel_identity
    assert report is not None and len(report.units) == 5
    (issue,) = report.validation_issues()
    assert issue.code == POSSIBLE_CHANNEL_SWAP and issue.paths == ("ctrl/rep1/t3",)
    assert "channels ch1 and ch2 look swapped" in issue.message
    # A warning beside the intensity checks, never one of the run's issues.
    assert POSSIBLE_CHANNEL_SWAP not in {i.code for i in result.issues}
    # The same from the written table: no image is needed.
    from_table = check_channel_identity(result.intensity.summaries)
    assert from_table.validation_issues() == report.validation_issues()
    # The limits are recorded beside intensity_checks, and the report lists the warning.
    assert read_manifest(result.manifest_path).channel_identity == ChannelIdentitySettings()
    written = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert POSSIBLE_CHANNEL_SWAP in {issue["code"] for issue in written["issues"]}


def test_the_command_line_prints_the_channel_warnings(swapped_run, capsys):
    root, _ = swapped_run
    assert main(["run", str(root), "--axes", "CYX"]) == 0
    out = capsys.readouterr().out
    assert "Channel identity: 10 unit channels compared: 1 channel warning." in out
    assert "(possible_channel_swap, 1):" in out
    assert "In unit ctrl/rep1/t3, channels ch1 and ch2 look swapped" in out
    out.encode("ascii")  # Windows consoles


def test_the_notebook_shows_the_plot_and_findings(swapped_run):
    pytest.importorskip("ipywidgets")
    pytest.importorskip("matplotlib")
    from morphobricks_preprocessing import notebook_style as ui
    from morphobricks_preprocessing.notebook_plots import IntensityPanel

    _, result = swapped_run
    panel = IntensityPanel("Intensity checks", "")
    panel.show(read_plane_histograms(result.csv_path))
    view = panel.identity
    assert ui.is_visible(view.box) and "Some channels do not look like themselves" in (
        view.status.value
    )
    assert "ctrl/rep1/t3" in view.findings.value and "Possible swap" in view.findings.value
    assert bytes(view.plot.value).startswith(b"\x89PNG")
    panel.clear()
    assert not ui.is_visible(view.box) and view.plot.value == b""
