"""``RunInputs.open`` on a stopped run: ``RunStoppedError`` with the run's own issues (#322).

A run preprocessing stops (Spec fixture ``v29``: ``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED``)
publishes no manifest; its refusal is only in ``discovery_report.json``, which bricks never
read. ``open`` reads it for them and raises ``RunStoppedError`` (``RUN_FOLDER_STOPPED``)
carrying the run's error issues byte-equal to the report's, before any pixel is read. A
report that cannot be read, or of another version, is ``RunReportUnreadableError``
(``RUN_FOLDER_REPORT_UNREADABLE``) naming the file. Every other refusal is unchanged.
"""

from __future__ import annotations

import builtins
import io
import json
import pickle

import pytest
import tifffile

import morphobricks_preprocessing as package
from described_tree import run_described
from description_tree import MAPPING, build_experiment
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.discovery_report import _issue_dict
from morphobricks_preprocessing.run_inputs import RunInputs, RunInputsError
from morphobricks_preprocessing.run_inputs_error import (
    DraftRefusedError,
    RunReportUnreadableError,
    RunStoppedError,
)
from morphobricks_preprocessing.run_inputs_stopped import stopped_run_refusal
from spec_pin import fixtures

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}
REPORT = "discovery_report.json"
NO_RUN = "holds no run_manifest.json: run preprocessing first."


def _tree(tmp_path, name):
    """Write the fixture's tree and publish it; return the root, output and image paths."""
    data = VALID[name].data
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir()
    (out / "morphobricks.toml").write_bytes(data)
    images = [root / path for path in build_experiment(root, description_from_bytes(data), 2)]
    return root, out, images


def _stopped_v29(tmp_path):
    root, out, _ = _tree(tmp_path, "v29")
    result = run_described(root, MAPPING, output_folder=out)
    assert not result.written
    assert not (out / "run_manifest.json").exists()
    return root, out


def _report(out):
    return json.loads((out / REPORT).read_bytes().decode("utf-8"))


def _bytes(issue):
    """One issue as the report writes it, as UTF-8 bytes (the report's own serialiser)."""
    return json.dumps(issue, indent=2, ensure_ascii=False).encode("utf-8")


def _assert_carries_the_report(refusal, out):
    errors = [item for item in _report(out)["issues"] if item["severity"] == "error"]
    assert errors  # a stopped run records why
    assert [_bytes(_issue_dict(issue)) for issue in refusal.issues] == [
        _bytes(item) for item in errors
    ]
    for issue, item in zip(refusal.issues, errors, strict=True):
        assert (issue.code, issue.message, list(issue.paths), issue.action) == (
            item["code"],
            item["message"],
            item["paths"],
            item["action"],
        )


def test_v29_open_raises_run_stopped_with_the_reports_issues_byte_equal(tmp_path):
    _, out = _stopped_v29(tmp_path)
    with pytest.raises(RunStoppedError) as raised:
        RunInputs.open(out)
    refusal = raised.value
    assert isinstance(refusal, RunInputsError)
    assert refusal.code == RunStoppedError.code == "RUN_FOLDER_STOPPED"
    assert [issue.code for issue in refusal.issues] == ["R_HETEROGENEOUS_LAYOUT_NOT_ENABLED"]
    _assert_carries_the_report(refusal, out)
    text = str(refusal)
    assert text.startswith("RUN_FOLDER_STOPPED: ")
    assert "R_HETEROGENEOUS_LAYOUT_NOT_ENABLED" in text and str(out) in text
    # Picklable with its issues (a worker process raises it to its parent).
    again = pickle.loads(pickle.dumps(refusal))
    assert type(again) is RunStoppedError and again.issues == refusal.issues
    assert str(again) == text
    assert package.RunStoppedError is RunStoppedError
    assert package.RunReportUnreadableError is RunReportUnreadableError


def test_several_errors_with_paths_are_carried_in_the_reports_order(tmp_path):
    # A described run stopped on two errors with paths: images that cannot be read and a
    # calibration statement for a target that does not exist.
    root, out, images = _tree(tmp_path, "v01")
    for image in images[:2]:
        image.write_bytes(b"not a TIFF")
    (out / "calibration.csv").write_text("target,axis,size,unit\nnope,X,0.5,um\n")
    assert not run_described(root, MAPPING, output_folder=out).written
    with pytest.raises(RunStoppedError) as raised:
        RunInputs.open(out)
    issues = raised.value.issues
    assert [issue.code for issue in issues] == ["unreadable_tiff", "calibration_target_not_found"]
    assert issues[0].paths == ("CTRL/rep1/img0.tif", "CTRL/rep1/img1.tif")
    _assert_carries_the_report(raised.value, out)


def test_v29_open_reads_no_pixel_and_no_experiment_file(tmp_path, monkeypatch):
    root, out = _stopped_v29(tmp_path)
    opened = []
    real = builtins.open

    def spy(file, *args, **kwargs):
        opened.append(str(file))
        return real(file, *args, **kwargs)

    def refuse(*args, **kwargs):
        raise AssertionError("a pixel was read")

    for cls in (tifffile.TiffFile, tifffile.TiffPage, tifffile.TiffFrame, tifffile.TiffPageSeries):
        monkeypatch.setattr(cls, "asarray", refuse)
    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(io, "open", spy)
    with pytest.raises(RunStoppedError):
        RunInputs.open(out)
    monkeypatch.undo()
    assert any(path.endswith(REPORT) for path in opened)  # the spy saw the report read
    inside = [path for path in opened if path.startswith(str(root)) or str(root.resolve()) in path]
    assert inside == []


def test_a_folder_with_no_run_keeps_its_refusal(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(RunInputsError, match=NO_RUN) as raised:
        RunInputs.open(out)
    assert type(raised.value) is RunInputsError and raised.value.code is None
    # A draft alone (a description not yet published, no run) is still "no run".
    (out / "morphobricks.draft.json").write_text('{"repeats": "garbage"}', encoding="utf-8")
    with pytest.raises(RunInputsError, match=NO_RUN) as raised:
        RunInputs.open(out)
    assert type(raised.value) is RunInputsError


@pytest.mark.parametrize(
    "change",
    [
        {"step": "check"},  # the folder check, not a run
        {"provisional": True},  # a run killed (or still reading) after its header checks
        {"status": "written"},  # a run that published, whose manifest is gone
    ],
    ids=["check", "provisional", "written"],
)
def test_a_report_that_says_no_run_stopped_keeps_the_no_run_refusal(tmp_path, change):
    _, out = _stopped_v29(tmp_path)
    report = _report(out) | change
    (out / REPORT).write_text(json.dumps(report, indent=2), encoding="utf-8")
    with pytest.raises(RunInputsError, match=NO_RUN) as raised:
        RunInputs.open(out)
    assert type(raised.value) is RunInputsError


def test_a_draft_refusal_is_unchanged(tmp_path):
    root, out, _ = _tree(tmp_path, "v01")
    assert run_described(root, MAPPING, output_folder=out).written
    text = VALID["v01"].data.decode().replace("confirmed = true", "confirmed = false")
    (out / "morphobricks.toml").write_text(text, encoding="utf-8")
    with pytest.raises(DraftRefusedError, match="A_DRAFT_REFUSED"):
        RunInputs.open(out)


def test_stale_files_keep_their_refusals_also_after_a_later_run_stopped(tmp_path):
    root, out, images = _tree(tmp_path, "v01")
    assert run_described(root, MAPPING, output_folder=out).written
    RunInputs.open(out)
    # A later run stops: the earlier generation stays published, so open reads the manifest
    # and refuses it as before (here an image changed since), never as a stopped run.
    image = images[0]
    image.write_bytes(b"not a TIFF")
    assert not run_described(root, MAPPING, output_folder=out).written
    assert _report(out)["status"] == "stopped" and (out / "run_manifest.json").exists()
    with pytest.raises(RunInputsError, match="changed or missing") as raised:
        RunInputs.open(out)
    assert not isinstance(raised.value, RunStoppedError)
    # A changed table is still refused as stale (it is checked before the images).
    table = out / "plane_histograms.csv"
    table.write_bytes(table.read_bytes() + b"\n")
    with pytest.raises(RunInputsError, match="histogram table") as raised:
        RunInputs.open(out)
    assert not isinstance(raised.value, RunStoppedError)


@pytest.mark.parametrize("version", ["2", "0", 1])
def test_an_unknown_report_version_is_refused_naming_the_file(tmp_path, version):
    _, out = _stopped_v29(tmp_path)
    report = _report(out) | {"report_version": version}
    (out / REPORT).write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(RunReportUnreadableError) as raised:
        RunInputs.open(out)
    refusal = raised.value
    assert refusal.code == "RUN_FOLDER_REPORT_UNREADABLE"
    assert refusal.path == out.resolve() / REPORT
    assert str(out.resolve() / REPORT) in str(refusal)
    assert str(refusal).startswith("RUN_FOLDER_REPORT_UNREADABLE: ")
    again = pickle.loads(pickle.dumps(refusal))
    assert type(again) is RunReportUnreadableError and again.path == refusal.path


def _issue_change(report, **change):
    issues = [dict(report["issues"][0], **change)]
    return report | {"issues": issues}


@pytest.mark.parametrize(
    "damage",
    [
        lambda report: b"\xff\xfe not UTF-8",
        lambda report: b'{"report_version": "1", ',
        lambda report: b"[]",
        lambda report: json.dumps({k: v for k, v in report.items() if k != "report_version"}),
        lambda report: json.dumps(report | {"status": "halted"}),
        lambda report: json.dumps(report | {"provisional": "no"}),
        lambda report: json.dumps(report | {"issues": {}}),
        lambda report: json.dumps(_issue_change(report, paths="a.tif")),
        lambda report: json.dumps(_issue_change(report, severity="fatal")),
        lambda report: json.dumps(_issue_change(report, message=None)),
        lambda report: json.dumps(report | {"issues": [{"code": "x"}]}),
        lambda report: json.dumps(report | {"issues": []}),  # stopped, with no error
        lambda report: json.dumps(report)[:-1] + ', "status": "stopped"}',  # a key twice
        lambda report: json.dumps(report).replace(
            '"provisional": false', '"x": NaN, "provisional": false'
        ),
    ],
    ids=[
        "not-utf8",
        "not-json",
        "not-object",
        "no-version",
        "status",
        "provisional",
        "issues-not-list",
        "paths",
        "severity",
        "message",
        "issue-shape",
        "no-error",
        "duplicate-key",
        "nan",
    ],
)
def test_an_unreadable_report_is_refused_naming_the_file(tmp_path, damage):
    _, out = _stopped_v29(tmp_path)
    data = damage(_report(out))
    (out / REPORT).write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    with pytest.raises(
        RunReportUnreadableError, match="cannot be read as a preprocessing"
    ) as raised:
        RunInputs.open(out)
    assert raised.value.path == out.resolve() / REPORT
    assert str(out.resolve() / REPORT) in str(raised.value)


def test_a_report_that_cannot_be_opened_is_refused_naming_the_file(tmp_path):
    out = tmp_path / "out"
    (out / REPORT).mkdir(parents=True)  # not a file: the read fails with an OSError
    with pytest.raises(RunReportUnreadableError, match="it cannot be read") as raised:
        RunInputs.open(out)
    assert raised.value.path == out.resolve() / REPORT


def test_a_manifest_published_while_the_report_was_read_makes_open_read_again(tmp_path):
    # read_stable counts a FileNotFoundError as a try: the next try reads the manifest.
    _, out = _stopped_v29(tmp_path)
    (out / "run_manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        stopped_run_refusal(out)
