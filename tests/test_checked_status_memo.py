"""PP-1: the mask step's status file is parsed once per content, never reused when it changes.

``read_checked`` reads ``mask_representation.json`` on every call and reuses the parse of the
same bytes only, so a brick that asks ``mask_check_status`` for every position does not decode
the whole file each time, and a status file the mask step rewrote is read again (the result is
the one an uncached read gives). ``RunInputs`` holds no memo: its pickle is unchanged.
"""

from __future__ import annotations

import dataclasses
import json
import pickle

from morphobricks_preprocessing import RunInputs
from morphobricks_preprocessing.mask_representation import (
    REPRESENTATION_FILENAME,
    read_checked,
)
from morphobricks_preprocessing.run.mask_step import describe_masks, prepare_mask_step
from morphobricks_preprocessing.testing import make_described_run
from spec_pin import fixtures

V08 = next(item for item in fixtures("valid") if item.name.startswith("v08")).data


def test_the_same_bytes_are_parsed_once_and_new_bytes_again(tmp_path, monkeypatch):
    run = make_described_run(tmp_path / "run", V08, masks=("Nuclei",))
    first = read_checked(run.output)
    assert read_checked(run.output) is first  # the same bytes: the parse is reused
    calls = []
    real = json.loads
    monkeypatch.setattr(json, "loads", lambda text: calls.append(1) or real(text))
    path = run.output / REPRESENTATION_FILENAME
    document = real(path.read_text(encoding="utf-8"))
    document["masks"]["Nuclei"]["files"] = document["masks"]["Nuclei"]["files"][:1]
    path.write_text(json.dumps(document), encoding="utf-8")
    changed = read_checked(run.output)
    assert calls == [1] and changed == document["masks"] and changed != first
    path.write_text("{not json", encoding="utf-8")
    assert read_checked(run.output) == {}
    path.unlink()
    assert read_checked(run.output) == {}


def test_a_mask_step_after_open_is_seen_by_the_same_run_inputs(tmp_path):
    run = make_described_run(tmp_path / "run", V08, masks=("Nuclei",), mask_step=False)
    inputs = RunInputs.open(run.output)
    fov = inputs.fovs()[0]
    assert inputs.mask_check_status("Nuclei", fov) == "not yet checked"
    assert describe_masks(prepare_mask_step(None, run.output)).issues == ()
    assert inputs.mask_check_status("Nuclei", fov) == "checked"


def test_run_inputs_pickles_as_before(tmp_path):
    run = make_described_run(tmp_path / "run", V08, masks=("Nuclei",))
    inputs = RunInputs.open(run.output)
    statuses = [inputs.mask_check_status("Nuclei", f) for f in inputs.fovs()]
    copy = pickle.loads(pickle.dumps(inputs))
    # Only the dataclass fields are pickled: no memo rides along.
    assert set(vars(copy)) == {f.name for f in dataclasses.fields(RunInputs)}
    assert copy.manifest == inputs.manifest and copy.fovs() == inputs.fovs()
    assert [copy.mask_check_status("Nuclei", f) for f in copy.fovs()] == statuses
