"""PP-7: the run modes, the output folder's lock and the report (design v0.4 §1, §4).

- ``restart`` (default) reuses unchanged rows; ``full`` recomputes every row and check.
- A later run reuses every recorded input; a value given that differs is used and reported;
  dropping the masks must be asked for.
- One run at a time holds the output folder's lock; a stale lock is removed only on request.
- Every run writes ``discovery_report.json`` and ``.txt``, also when it stops.
"""

import json
import os
import socket

import numpy as np
import pytest

from morphobricks_preprocessing import (
    AxisMapping,
    RunMode,
    default_design,
    default_output_folder,
    prefill_mapping,
    preflight,
    read_manifest,
    run_experiment,
    write_experiment_design,
)
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.discovery_report import REPORT_FILENAME, REPORT_TEXT_FILENAME
from morphobricks_preprocessing.manifest import file_sha256
from morphobricks_preprocessing.mask_scan import MASK_REPORT_FILENAME
from morphobricks_preprocessing.run import (
    LOCK_FILENAME,
    OutputLockedError,
    read_lock,
    remove_stale_lock,
)
from morphobricks_preprocessing.run.lock import output_lock
from morphobricks_preprocessing.testing import make_clean_run
from run_files import check_outputs, finished_pid, only_the_report
from tiff_tree import write_data, write_plain

TYX = prefill_mapping("TYX")


def stack(seed=0, shape=(3, 6, 8)):
    return np.random.default_rng(seed).integers(0, 200, shape, dtype=np.uint8)


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    for seed, rel in enumerate(["ctrl/r1/a.tif", "ctrl/r1/b.tif", "drug/r1/a.tif"]):
        write_data(root / rel, stack(seed), "TYX")
    return root


def report(result):
    return json.loads(result.report_path.read_text(encoding="utf-8"))


def codes(issues):
    return [issue.code for issue in issues]


# Modes -----------------------------------------------------------------------------------


def test_an_unknown_mode_is_refused_before_anything_is_written(experiment):
    with pytest.raises(ValueError, match="mode must be one of restart, full"):
        run_experiment(experiment, TYX, mode="rebuild")
    assert not default_output_folder(experiment).exists()


def test_the_removed_force_rebuild_is_refused(experiment):
    with pytest.raises(TypeError):
        run_experiment(experiment, TYX, force_rebuild=True)


def test_a_restart_that_changes_nothing_leaves_the_table_byte_identical(experiment):
    first = run_experiment(experiment, TYX)
    table = first.csv_path.read_bytes()
    again = run_experiment(experiment, TYX)
    assert again.written and again.read == () and again.reused == again.unchanged
    assert again.csv_path.read_bytes() == table
    assert report(again)["reused"] == list(again.unchanged) and report(again)["recomputed"] == {}


def test_full_recomputes_every_row_and_keeps_the_confirmations(tmp_path):
    clean = make_clean_run(tmp_path, axes="YX", shape=(16, 20), mask_types=("Cells",), design=True)
    design = (clean.output / "experiment_design.csv").read_bytes()
    full = run_experiment(clean.experiment, output_folder=clean.output, mode="full")
    assert full.written and full.mode == "full" and len(full.read) == 4
    manifest = read_manifest(full.manifest_path)
    # The design, masks and image type recorded earlier are kept, without being given again.
    assert manifest.masks["folder"] == clean.masks.resolve().as_posix()
    assert (clean.output / "experiment_design.csv").read_bytes() == design
    assert set(report(full)["recomputed"].values()) == {"a full run was asked for (mode full)"}


def test_full_removes_the_mask_report_and_restart_keeps_it_while_the_masks_are_unchanged(
    tmp_path,
):
    clean = make_clean_run(tmp_path, axes="YX", shape=(16, 20), mask_types=("Cells",))
    mask_report = clean.output / MASK_REPORT_FILENAME
    mask_report.write_text("{}", encoding="utf-8")
    again = run_experiment(clean.experiment, output_folder=clean.output)
    assert again.written and mask_report.exists() and MASK_REPORT_FILENAME not in again.replaced
    full = run_experiment(clean.experiment, output_folder=clean.output, mode="full")
    assert full.written and not mask_report.exists()
    assert "full run" in report(full)["replaced"][MASK_REPORT_FILENAME]


def test_a_changed_mask_invalidates_the_mask_report(tmp_path):
    clean = make_clean_run(tmp_path, axes="YX", shape=(16, 20), mask_types=("Cells",))
    mask_report = clean.output / MASK_REPORT_FILENAME
    mask_report.write_text("{}", encoding="utf-8")
    mask = next((clean.masks / "Cells").rglob("a.tif"))
    write_data(mask, np.ones((16, 20), np.uint16), "YX")
    result = run_experiment(clean.experiment, output_folder=clean.output)
    assert result.written and not mask_report.exists()
    assert result.replaced == tuple(sorted(report(result)["replaced"]))


# A later run reuses every recorded input (design v0.4 §1) ------------------------------------


def test_a_later_run_keeps_the_masks_and_dropping_them_must_be_asked_for(tmp_path):
    clean = make_clean_run(tmp_path, axes="YX", shape=(16, 20), mask_types=("Cells",))
    recorded = read_manifest(clean.result.manifest_path).masks
    again = run_experiment(clean.experiment, prefill_mapping("YX"), output_folder=clean.output)
    assert read_manifest(again.manifest_path).masks == recorded and again.input_changes == ()
    dropped = run_experiment(clean.experiment, output_folder=clean.output, masks_folder=False)
    assert read_manifest(dropped.manifest_path).masks is None
    (change,) = dropped.input_changes
    assert change.name == "masks_folder" and change.given == "none (dropped)"


def test_a_later_run_reuses_the_recorded_inputs_and_settings(experiment):
    first = run_experiment(
        experiment,
        TYX,
        bin_count=64,
        exclude=["drug/r1/a.tif"],
        image_type="fluorescence",
        missing_as_singleton=True,
    )
    again = run_experiment(output_folder=first.output_folder)
    assert again.written and again.read == () and again.input_changes == ()
    manifest = read_manifest(again.manifest_path)
    assert manifest.settings.bin_count == 64 and manifest.settings.exclude == ("drug/r1/a.tif",)
    assert manifest.settings.missing_as_singleton and manifest.image_type == "fluorescence"
    assert manifest.mapping == TYX


def test_a_value_given_that_differs_is_used_and_the_report_names_its_effect(experiment):
    first = run_experiment(experiment, TYX, exclude=["drug/r1/a.tif"])
    again = run_experiment(experiment, bin_count=32, exclude=[], output_folder=first.output_folder)
    assert again.written and again.read == ("ctrl/r1/a.tif", "ctrl/r1/b.tif", "drug/r1/a.tif")
    changes = {change.name: change for change in again.input_changes}
    assert set(changes) == {"bin_count", "exclude"}
    assert changes["bin_count"].recorded == "256" and changes["bin_count"].given == "32"
    assert changes["bin_count"].effect == "every row is recomputed"
    data = report(again)
    assert {c["input"] for c in data["input_changes"]} == {"bin_count", "exclude"}
    assert "Changed bin_count: 256 -> 32 (every row is recomputed)" in (
        again.output_folder / REPORT_TEXT_FILENAME
    ).read_text(encoding="utf-8")


def test_without_roles_given_or_recorded_the_run_stops(experiment):
    result = run_experiment(experiment)
    assert not result.written and codes(result.issues) == ["axes_not_confirmed"]
    assert only_the_report(result.output_folder)


def test_a_version_1_manifest_gives_no_inputs(experiment):
    first = run_experiment(experiment, TYX, mode=RunMode("restart", verify_content=False))
    data = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    data["manifest_version"] = "1"
    first.manifest_path.write_text(json.dumps(data), encoding="utf-8")
    result = run_experiment(experiment)
    assert "axes_not_confirmed" in codes(result.issues)
    with pytest.raises(ValueError, match="experiment_not_recorded|records none"):
        run_experiment(output_folder=first.output_folder)


def test_recorded_plain_stack_confirmations_cover_only_the_files_as_they_were(tmp_path):
    root = tmp_path / "exp"
    write_plain(root / "ctrl/a.tif", stack())
    assert run_experiment(root, AxisMapping("IYX", "TYX")).written
    again = run_experiment(root)  # the recorded confirmation covers a.tif as it is
    assert again.written and again.read == ()
    write_plain(root / "ctrl/b.tif", stack(1))  # new: never read with roles meant for a.tif
    result = run_experiment(root)
    assert not result.written
    (issue,) = [i for i in result.issues if i.code == "plain_stack_not_confirmed"]
    assert issue.paths == ("ctrl/b.tif",)


def test_recorded_plain_stack_confirmations_do_not_follow_another_experiment_folder(tmp_path):
    root = tmp_path / "exp"
    write_plain(root / "ctrl/a.tif", stack())
    first = run_experiment(root, AxisMapping("IYX", "TYX"), output_folder=tmp_path / "out")
    assert first.written
    # Same relative path, size and modification time, but not the file that was confirmed.
    other = root.rename(tmp_path / "other")
    result = run_experiment(other, output_folder=first.output_folder)
    assert not result.written
    (issue,) = [i for i in result.issues if i.code == "plain_stack_not_confirmed"]
    assert issue.paths == ("ctrl/a.tif",)
    assert [c.name for c in result.input_changes] == ["experiment_root"]
    assert "confirmed again" in result.input_changes[0].effect
    # Confirmed again for the files of the new folder, it runs.
    assert run_experiment(
        other, AxisMapping("IYX", "TYX"), output_folder=first.output_folder
    ).written


def test_the_recorded_design_is_reused_and_new_unconfirmed_repeats_stop_a_restart(tmp_path):
    clean = make_clean_run(tmp_path, axes="YX", shape=(16, 20), design=True)
    again = run_experiment(clean.experiment, output_folder=clean.output)
    assert again.written and again.design is not None and again.design.confirmed
    write_data(clean.experiment / "ctrl/rep2/c.tif", np.ones((16, 20), np.uint16), "YX")
    stopped = run_experiment(clean.experiment, output_folder=clean.output)
    assert not stopped.written and "design_not_confirmed" in codes(stopped.issues)
    assert "design_not_confirmed" in (clean.output / REPORT_TEXT_FILENAME).read_text("utf-8")


def test_a_changed_design_is_named_with_its_effect(tmp_path):
    clean = make_clean_run(tmp_path, axes="YX", shape=(16, 20), design=True)
    same = run_experiment(clean.experiment, output_folder=clean.output, design=clean.result.design)
    assert same.written and same.input_changes == ()
    given = clean.result.design.with_control("drug", True)
    changed = run_experiment(clean.experiment, output_folder=clean.output, design=given)
    (change,) = changed.input_changes
    assert change.name == "design" and change.effect == "no row is affected"
    assert "drug/rep1" in change.given and "control" in change.given
    assert "control" not in change.recorded and changed.read == ()
    assert report(changed)["input_changes"] == [change.to_dict()]
    # The file edited in the output folder, as the CLI's user does, is a change too.
    path = clean.output / "experiment_design.csv"
    write_experiment_design(given.with_control("drug", False), path)
    edited = run_experiment(clean.experiment, output_folder=clean.output)
    assert [c.name for c in edited.input_changes] == ["design"]
    assert "Changed design:" in (clean.output / REPORT_TEXT_FILENAME).read_text("utf-8")


# The lock ------------------------------------------------------------------------------------


def test_a_second_run_is_refused_and_names_the_holder(experiment):
    output = default_output_folder(experiment)
    first = run_experiment(experiment, TYX)
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    with output_lock(output) as holder:
        result = run_experiment(experiment, TYX)
        assert read_lock(output) == holder
    assert not result.written and codes(result.issues) == ["output_locked"]
    (issue,) = result.issues
    assert f"process {os.getpid()} on {socket.gethostname()}" in issue.message
    assert "--remove-stale-lock" in issue.action
    # Nothing was touched, not even the report.
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before
    assert first.written and not (output / LOCK_FILENAME).exists()


def test_the_lock_is_released_when_a_run_fails(experiment, monkeypatch):
    from morphobricks_preprocessing import run as run_module

    def broken(*args, **kwargs):
        raise OSError("simulated")

    monkeypatch.setattr(run_module.pipeline, "write_plane_histograms", broken)
    with pytest.raises(OSError, match="simulated"):
        run_experiment(experiment, TYX)
    assert read_lock(default_output_folder(experiment)) is None


def test_a_lock_that_cannot_be_written_is_not_left_behind(experiment, monkeypatch):
    from morphobricks_preprocessing.run import lock as lock_module

    def broken(*args, **kwargs):
        raise OSError("simulated: disk full")

    output = default_output_folder(experiment)
    output.mkdir(parents=True)
    monkeypatch.setattr(lock_module.json, "dumps", broken)
    with pytest.raises(OSError, match="disk full"), output_lock(output):
        pass
    assert not (output / LOCK_FILENAME).exists()


def test_the_masks_command_writes_nothing_while_a_run_holds_the_lock(tmp_path, capsys):
    clean = make_clean_run(tmp_path, mask_types=("Cells",), axes="YX", shape=(16, 20))
    args = ["masks", clean.experiment, "--masks", clean.masks, "--output", clean.output]
    with output_lock(clean.output):
        assert main([str(arg) for arg in args]) == 1
    assert "Another run is using this output folder" in capsys.readouterr().out
    assert not (clean.output / MASK_REPORT_FILENAME).exists()
    assert main([str(arg) for arg in args]) == 0 and (clean.output / MASK_REPORT_FILENAME).exists()


def stale_lock(output, pid, host=None):
    output.mkdir(parents=True, exist_ok=True)
    data = {"host": host or socket.gethostname(), "pid": pid, "started_at": "2026-11-01"}
    (output / LOCK_FILENAME).write_text(json.dumps(data), encoding="utf-8")


def test_a_stale_lock_is_removed_only_on_request(experiment):
    output = default_output_folder(experiment)
    stale_lock(output, finished_pid(), host="another-computer")
    assert codes(run_experiment(experiment, TYX).issues) == ["output_locked"]
    removed = remove_stale_lock(output)
    assert removed.host == "another-computer" and read_lock(output) is None
    assert run_experiment(experiment, TYX).written
    assert remove_stale_lock(output) is None


@pytest.mark.skipif(os.name != "posix", reason="only POSIX can tell a process runs")
def test_a_lock_of_a_running_process_on_this_computer_is_kept(experiment):
    output = default_output_folder(experiment)
    stale_lock(output, os.getpid())
    with pytest.raises(OutputLockedError):
        remove_stale_lock(output)
    assert read_lock(output).pid == os.getpid()


def test_the_cli_removes_a_stale_lock_on_request(experiment, capsys):
    output = default_output_folder(experiment)
    stale_lock(output, finished_pid(), host="another-computer")
    assert main(["check", str(experiment)]) == 1
    assert "Another run is using this output folder" in capsys.readouterr().out
    assert main(["check", str(experiment), "--remove-stale-lock"]) == 0
    out = capsys.readouterr().out
    assert "Removed the lock of process" in out and "another-computer" in out
    assert (output / REPORT_FILENAME).exists() and read_lock(output) is None


# The report ----------------------------------------------------------------------------------


def test_a_written_run_reports_its_manifest_and_what_it_found(tmp_path):
    clean = make_clean_run(tmp_path, mask_types=("Cells", "Nuclei"), axes="YX", shape=(16, 20))
    data = report(clean.result)
    assert data["status"] == "written" and data["step"] == "run" and not data["provisional"]
    assert data["manifest_sha256"] == file_sha256(clean.result.manifest_path)
    assert data["mode"] == "restart" and data["experiment_root_changed"] is False
    assert [g["condition"] for g in data["groups"]] == ["ctrl", "drug"]
    assert data["groups"][0]["positions"] == ["a", "b"]
    image = data["images"]["ctrl/rep1/a.ome.tif"]
    assert image["axes_from"] == "header" and image["roles"] == "YX"
    assert image["pixel_sizes"]["X"] == {"value": 0.5, "unit": "micrometer", "source": "header"}
    assert data["masks"]["types"]["Cells"] == {"paired": 4, "images_without": []}
    assert data["replaced"] == {}  # the first run replaces nothing
    assert data["channels"] == 1
    text = (clean.output / REPORT_TEXT_FILENAME).read_text(encoding="utf-8")
    assert "run, written" in text and data["manifest_sha256"] in text
    assert "Masks: 2 mask types" in text


def test_uncalibrated_images_are_reported(tmp_path):
    clean = make_clean_run(tmp_path, axes="YX", shape=(16, 20), pixel_size=None)
    data = report(clean.result)
    assert {image["pixel_sizes"] for image in data["images"].values()} == {"uncalibrated"}


def test_a_stopped_run_writes_its_report_and_keeps_the_last_good_run(experiment):
    first = run_experiment(experiment, TYX)
    kept = {p.name: p.read_bytes() for p in (first.csv_path, first.manifest_path)}
    stopped = run_experiment(experiment, TYX, bin_count=0)
    assert not stopped.written
    data = report(stopped)
    assert data["status"] == "stopped" and data["manifest_sha256"] is None
    (issue,) = [i for i in data["issues"] if i["code"] == "invalid_bin_count"]
    assert issue["severity"] == "error" and issue["action"]
    assert {p.name: p.read_bytes() for p in (first.csv_path, first.manifest_path)} == kept


def test_the_report_is_provisional_while_the_pixels_are_read(experiment):
    seen = []

    def progress(done, total, path):
        output = default_output_folder(experiment)
        seen.append(json.loads((output / REPORT_FILENAME).read_text(encoding="utf-8")))

    result = run_experiment(experiment, TYX, progress=progress)
    assert seen and all(r["provisional"] and r["status"] == "stopped" for r in seen)
    assert report(result)["status"] == "written" and not report(result)["provisional"]


def test_the_report_names_why_files_were_recomputed(experiment):
    first = run_experiment(experiment, TYX)
    write_data(experiment / "ctrl/r1/a.tif", stack(9), "TYX")
    write_data(experiment / "drug/r1/c.tif", stack(7), "TYX")
    data = report(run_experiment(experiment, TYX, output_folder=first.output_folder))
    assert data["recomputed"] == {
        "ctrl/r1/a.tif": "changed since the earlier run",
        "drug/r1/c.tif": "new since the earlier run",
    }
    assert data["reused"] == ["ctrl/r1/b.tif", "drug/r1/a.tif"]
    assert data["replaced"][REPORT_FILENAME] == "replaced"


def test_a_moved_experiment_is_reported(experiment, tmp_path):
    first = run_experiment(experiment, TYX, output_folder=tmp_path / "out")
    moved = experiment.rename(tmp_path / "moved")
    again = run_experiment(moved, output_folder=first.output_folder)
    data = report(again)
    assert data["experiment_root_changed"] is True and len(again.read) == 3
    assert {c.name for c in again.input_changes} == {"experiment_root"}


def test_check_writes_the_report_and_the_design_template(experiment):
    assert main(["check", str(experiment)]) == 0
    output = default_output_folder(experiment)
    assert check_outputs(output)
    data = json.loads((output / REPORT_FILENAME).read_text(encoding="utf-8"))
    assert data["step"] == "check" and data["mode"] is None
    assert data["images"]["ctrl/r1/a.tif"]["roles"] == "TYX"


def test_nothing_is_written_inside_the_experiment(experiment):
    result = run_experiment(experiment, TYX, output_folder=experiment / "results")
    assert codes([i for i in result.issues if i.is_error]) == ["output_inside_experiment"]
    assert not (experiment / "results").exists()


# The command line: --no-exclude and running from --output alone (owner, PP-7 review) --------


def cli_run(*args):
    return main(["run", *map(str, args)])


def confirmed_design(root, output):
    """Confirm every repeat group, as the user does before 'run' (contract §12)."""
    design = default_design(preflight(root).dataset.repeats)
    for group in design.groups:
        design = design.with_confirmed(group, True, "2026-11-02")
    write_experiment_design(design, output / "experiment_design.csv")


def test_no_exclude_clears_the_recorded_exclusions(experiment, tmp_path, capsys):
    out = tmp_path / "out"
    confirmed_design(experiment, out)
    assert cli_run(experiment, "--axes", "TYX", "--output", out, "--exclude", "ctrl/r1/b.tif") == 0
    # Left out, the recorded exclusions are kept.
    assert cli_run(experiment, "--output", out) == 0
    assert read_manifest(out / "run_manifest.json").settings.exclude == ("ctrl/r1/b.tif",)
    capsys.readouterr()
    assert cli_run(experiment, "--output", out, "--no-exclude") == 0
    assert read_manifest(out / "run_manifest.json").settings.exclude == ()
    out_text = capsys.readouterr().out
    assert "Changed exclude: ctrl/r1/b.tif -> none" in out_text
    assert "Read: 1 image" in out_text


def test_exclude_and_no_exclude_together_are_refused(experiment, capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli_run(experiment, "--axes", "TYX", "--exclude", "drug/r1/a.tif", "--no-exclude")
    assert excinfo.value.code == 2
    assert "--exclude and --no-exclude cannot be given together" in capsys.readouterr().err
    assert not default_output_folder(experiment).exists()


def test_the_cli_runs_from_the_output_folder_alone(experiment, tmp_path, capsys):
    out = tmp_path / "out"
    confirmed_design(experiment, out)
    assert cli_run(experiment, "--axes", "TYX", "--output", out) == 0
    capsys.readouterr()
    assert cli_run("--output", out) == 0
    text = capsys.readouterr().out
    assert "Unchanged (rows kept): 3 images" in text and "Changed" not in text


def test_the_cli_names_a_changed_experiment_and_its_effect(experiment, tmp_path, capsys):
    out = tmp_path / "out"
    confirmed_design(experiment, out)
    assert cli_run(experiment, "--axes", "TYX", "--output", out) == 0
    moved = experiment.rename(tmp_path / "moved")
    capsys.readouterr()
    assert cli_run(moved, "--output", out) == 0
    text = capsys.readouterr().out
    assert f"Changed experiment_root: {experiment.as_posix()} -> {moved.as_posix()}" in text
    assert "every row is recomputed (the images are read from the new place)" in text


@pytest.mark.parametrize("folder", ["none", "empty", "version 1"])
def test_without_a_recorded_experiment_the_cli_names_what_is_missing(
    experiment, tmp_path, capsys, folder
):
    out = tmp_path / "out"
    if folder == "version 1":
        run_experiment(
            experiment, TYX, output_folder=out, mode=RunMode("restart", verify_content=False)
        )
        data = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
        data["manifest_version"] = "1"
        (out / "run_manifest.json").write_text(json.dumps(data), encoding="utf-8")
    elif folder == "empty":
        out.mkdir()
    assert cli_run(*(() if folder == "none" else ("--output", out))) == 1
    text = capsys.readouterr().out
    assert "No experiment folder was given" in text and "--output" in text
    expected = {
        "none": "neither the experiment folder nor the output folder was given",
        "empty": "there is no earlier run in the output folder",
        "version 1": "its run is older than v0.4.0",
    }[folder]
    assert expected in text
