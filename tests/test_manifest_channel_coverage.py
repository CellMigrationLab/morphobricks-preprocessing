"""The manifest's recorded channel coverage must be the published description's (#490).

``experiment_description.channel_coverage`` is what the run computed from the description
(:func:`.channel_plan.channel_coverage`). ``RunInputs.open`` recomputes it from the published
description whose SHA-256 it checked and refuses a manifest recording anything else
(``manifest_channel_coverage_mismatch``, a ``RunInputsError``), naming the field and each
biological repeat that differs. Nothing is filled in or repaired: the file is left as it was.
Partial and unavailable channels, recorded as the run wrote them, open as before.
"""

from __future__ import annotations

import json
import pickle

import pytest

from morphobricks_preprocessing.channel_plan import channel_coverage
from morphobricks_preprocessing.description_consume import read_published_description
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME
from morphobricks_preprocessing.manifest import MANIFEST_FILENAME, read_manifest
from morphobricks_preprocessing.run_inputs import RunInputs
from morphobricks_preprocessing.run_inputs_error import (
    ChannelCoverageMismatchError,
    RunInputsError,
)
from morphobricks_preprocessing.testing import make_described_run
from spec_pin import fixtures

VALID = {f.name.split("_")[0]: f for f in fixtures("valid")}
MISMATCH = "manifest_channel_coverage_mismatch"
FIELD = "experiment_description.channel_coverage"


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    """A run of ``v17`` (full, partial and unavailable channels), made once."""
    return make_described_run(tmp_path_factory.mktemp("v17") / "run", VALID["v17"].data)


@pytest.fixture
def run(made):
    """The module's run with its manifest restored before each test."""
    path = made.output / MANIFEST_FILENAME
    original = path.read_bytes()
    yield made
    path.write_bytes(original)


def _edit(run, change):
    """Rewrite the run's manifest with ``change`` applied to its channel coverage."""
    path = run.output / MANIFEST_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data["experiment_description"]["channel_coverage"])
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path.read_bytes()


def _published(run):
    return channel_coverage(read_published_description(run.output / DESCRIPTION_FILENAME)[0])


def test_a_normal_run_with_partial_and_unavailable_channels_opens(run):
    expected = _published(run)
    statuses = {entry["status"] for channels in expected.values() for entry in channels.values()}
    assert statuses == {"full", "partial", "unavailable"}
    recorded = read_manifest(run.output / MANIFEST_FILENAME).experiment_description
    assert recorded["channel_coverage"] == expected
    RunInputs.open(run.output)


def _status(coverage):
    repeat = sorted(coverage)[0]
    channel = sorted(coverage[repeat])[0]
    entry = coverage[repeat][channel]
    entry["status"] = "full" if entry["status"] != "full" else "unavailable"


def _count(coverage):
    repeat = sorted(coverage)[0]
    channel = sorted(coverage[repeat])[0]
    coverage[repeat][channel]["units"] += 1


def _lacking(coverage):
    repeat = sorted(coverage)[0]
    channel = sorted(coverage[repeat])[0]
    coverage[repeat][channel]["lacking"].append("X/single")


def _drop_channel(coverage):
    repeat = sorted(coverage)[0]
    coverage[repeat].pop(sorted(coverage[repeat])[0])


def _drop_repeat(coverage):
    coverage.pop(sorted(coverage)[0])


def _extra_repeat(coverage):
    coverage["Zrepeat"] = coverage[sorted(coverage)[0]]


@pytest.mark.parametrize(
    "change", [_status, _count, _lacking, _drop_channel, _drop_repeat, _extra_repeat]
)
def test_a_changed_coverage_is_refused_at_open_naming_the_repeat(run, change):
    expected = _published(run)
    before = _edit(run, change)
    recorded = read_manifest(run.output / MANIFEST_FILENAME).experiment_description
    differ = sorted(
        key
        for key in set(expected) | set(recorded["channel_coverage"])
        if expected.get(key) != recorded["channel_coverage"].get(key)
    )
    assert len(differ) == 1
    with pytest.raises(ChannelCoverageMismatchError) as refused:
        RunInputs.open(run.output)
    error = refused.value
    assert isinstance(error, RunInputsError) and error.code == MISMATCH
    message = str(error)
    assert message.startswith(f"{MISMATCH}: ")
    assert f"{FIELD}.{differ[0]}" in message and "Run preprocessing again" in message
    others = [key for key in expected if key != differ[0]]
    assert not any(f"{FIELD}.{key} " in message for key in others)
    assert (run.output / MANIFEST_FILENAME).read_bytes() == before  # never repaired
    again = pickle.loads(pickle.dumps(error))
    assert type(again) is ChannelCoverageMismatchError and str(again) == message


def test_an_emptied_coverage_is_refused_naming_every_repeat(run):
    expected = _published(run)
    _edit(run, lambda coverage: coverage.clear())
    with pytest.raises(ChannelCoverageMismatchError) as refused:
        RunInputs.open(run.output)
    message = str(refused.value)
    assert all(f"{FIELD}.{key}" in message for key in sorted(expected)[:5])
