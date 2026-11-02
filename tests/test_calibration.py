"""PP-8: calibration, the pixel sizes the user states (design v0.4 §3, §4, §6).

- Each axis's size comes from the header or ``calibration.csv`` (experiment, folder or
  image), recorded with its source: ``header``, ``stated`` or ``pixels``.
- Nothing is guessed: without either, an axis is uncalibrated (no default of 1 µm).
- A statement that differs from the header is a warning, and the statement is used.
- A run never writes ``calibration.csv``; changing it is a recorded input that recomputes
  no row.
- Y and X are stated and recorded by role: nothing exchanges their sizes.
"""

import json

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    CALIBRATION_FILENAME,
    Calibration,
    CalibrationFormatError,
    RunInputs,
    RunInputsError,
    calibration_entry,
    prefill_mapping,
    preflight,
    read_calibration,
    read_manifest,
    run_experiment,
    write_calibration,
)
from morphobricks_preprocessing.calibration_io import calibration_text
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.run import OutputLockedError
from morphobricks_preprocessing.run.lock import output_lock
from morphobricks_preprocessing.testing import make_clean_run

A = "ctrl/rep1/a.ome.tif"


def codes(issues):
    return [issue.code for issue in issues]


def issue(issues, code):
    (found,) = [i for i in issues if i.code == code]
    return found


def report(result):
    return json.loads(result.report_path.read_text(encoding="utf-8"))


def state(output, text):
    path = output / CALIBRATION_FILENAME
    output.mkdir(parents=True, exist_ok=True)
    path.write_text("target,axis,size,unit\n" + text, encoding="utf-8")
    return path


def sizes(output, path=A):
    entry = read_manifest(output / "run_manifest.json").file(path)
    return {size.role: size for size in entry.pixel_sizes}


@pytest.fixture
def uncalibrated(tmp_path):
    return make_clean_run(tmp_path, axes="YX", shape=(16, 20), pixel_size=None)


@pytest.fixture
def calibrated(tmp_path):
    return make_clean_run(tmp_path, axes="YX", shape=(16, 20), pixel_size=0.5)


def again(clean, **kwargs):
    return run_experiment(output_folder=clean.output, **kwargs)


# The statements ------------------------------------------------------------------------------


def test_a_statement_is_exact_and_normalised():
    entry = calibration_entry("ctrl\\rep1/", "X", "0.65", "µm")
    assert (entry.target, entry.axis, entry.size, entry.unit) == (
        "ctrl/rep1",
        "X",
        0.65,
        "micrometer",
    )
    assert calibration_entry("./", "Z", None, "pixels").in_pixels
    assert calibration_entry(".", "Y", 162.5, "nanometre").unit == "nanometer"


@pytest.mark.parametrize(
    ("args", "words"),
    [
        ((".", "C", 1, "um"), "is not X, Y or Z"),
        ((".", "x", 1, "um"), "is not X, Y or Z"),
        ((".", "X", 0, "um"), "positive finite"),
        ((".", "X", float("nan"), "um"), "positive finite"),
        ((".", "X", "-1", "um"), "positive finite"),
        # Plain ASCII decimals only: float() would read these as 10, 0.65, 1 and nan.
        ((".", "X", "1_0", "um"), "not a number"),
        ((".", "X", "0.6_5", "um"), "not a number"),
        ((".", "X", "\u0661", "um"), "not a number"),
        ((".", "X", "nan", "um"), "not a number"),
        ((".", "X", "0,5", "um"), "not a number"),
        ((".", "X", True, "um"), "not a number"),
        ((".", "X", 1, "inch"), "not supported"),
        ((".", "X", 1, ""), "no unit"),
        ((".", "X", 1, "pixels"), "has no size"),
        (("../other", "X", 1, "um"), "inside the experiment"),
        (("/abs", "X", 1, "um"), "inside the experiment"),
        (("C:\\data\\ctrl", "X", 1, "um"), "inside the experiment"),
        (("ctrl/./rep1", "X", 1, "um"), "inside the experiment"),
        (("ctrl/.", "X", 1, "um"), "inside the experiment"),
        (("ctrl//rep1", "X", 1, "um"), "inside the experiment"),
        ((" ctrl", "X", 1, "um"), "spaces"),
        (("", "X", 1, "um"), "empty"),
    ],
)
def test_a_statement_that_is_not_exact_is_refused(args, words):
    with pytest.raises(ValueError, match=words):
        calibration_entry(*args)


def test_the_most_specific_statement_applies():
    calibration = Calibration(
        (
            calibration_entry(".", "X", 1, "um"),
            calibration_entry("ctrl", "X", 2, "um"),
            calibration_entry("ctrl/rep1", "X", 3, "um"),
            calibration_entry(A, "X", 4, "um"),
        )
    )
    assert calibration.statement(A, "X").size == 4
    assert calibration.statement("ctrl/rep1/b.ome.tif", "X").size == 3
    assert calibration.statement("ctrl/rep2/b.ome.tif", "X").size == 2
    assert calibration.statement("drug/rep1/a.ome.tif", "X").size == 1
    # "ctrl" never covers a folder that only starts with the same letters.
    assert calibration.statement("ctrl2/rep1/a.ome.tif", "X").size == 1
    assert calibration.statement(A, "Y") is None


def test_the_file_is_read_strictly_and_written_back_the_same(tmp_path):
    path = state(tmp_path, ".,X,0.65,um\n.,Y,0.65,um\nctrl,Z,,pixels\n")
    calibration, sha256 = read_calibration(path)
    assert [e.describe() for e in calibration.entries] == [
        "0.65 micrometer",
        "0.65 micrometer",
        "pixels",
    ]
    assert len(sha256) == 64
    rewritten = tmp_path / "again.csv"
    rewritten.write_text(calibration_text(calibration), encoding="utf-8")
    assert read_calibration(rewritten)[0] == calibration
    # A spreadsheet's byte-order mark and Windows line endings are read.
    path.write_bytes(b"\xef\xbb\xbftarget,axis,size,unit\r\n.,X,1,um\r\n")
    assert read_calibration(path)[0].entries[0].size == 1.0


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("target,axis,size\n", "must have the columns"),
        ("target,axis,size,unit\n.,X,1\n", "line 2 has 3 values"),
        ("target,axis,size,unit\n.,X,1,um\n.,X,2,um\n", "line 3: . X is already stated on line 2"),
        ("target,axis,size,unit\n.,X, 1,um\n", "line 2: size ' 1' starts or ends"),
        ("target,axis,size,unit\n.,X,1,inch\n", "line 2: the length unit 'inch'"),
        ('target,axis,size,unit\n".,X,1,um\n', "not a well-formed CSV"),
    ],
)
def test_a_file_that_is_not_exact_is_refused_naming_the_line(tmp_path, text, words):
    path = tmp_path / CALIBRATION_FILENAME
    path.write_text(text, encoding="utf-8")
    with pytest.raises(CalibrationFormatError, match=words):
        read_calibration(path)


# Recorded sizes and their source ----------------------------------------------------------------


def test_a_stated_size_is_recorded_with_its_source(uncalibrated):
    assert sizes(uncalibrated.output)["X"].source is None
    state(uncalibrated.output, ".,X,0.65,um\n.,Y,650,nm\n")
    result = again(uncalibrated)
    assert result.written and result.read == ()  # no row is recomputed
    recorded = sizes(uncalibrated.output)
    assert (recorded["X"].value, recorded["X"].unit, recorded["X"].source) == (
        0.65,
        "micrometer",
        "stated",
    )
    assert (recorded["Y"].value, recorded["Y"].unit) == (650.0, "nanometer")
    assert recorded["Z"].source is None and recorded["Z"].value is None
    inputs = RunInputs.open(uncalibrated.output)
    assert inputs.pixel_sizes(A)["X"].source == "stated"
    data = report(result)
    assert data["images"][A]["pixel_sizes"]["Y"]["source"] == "stated"
    assert data["calibration"]["file"] == CALIBRATION_FILENAME
    assert "4 stated" in result.report_path.with_suffix(".txt").read_text(encoding="utf-8")


def test_nothing_is_guessed_without_a_size(uncalibrated):
    # No header size and no statement: no value, no source, no default of 1 micrometre.
    recorded = sizes(uncalibrated.output)
    assert all(size.value is None and size.source is None for size in recorded.values())
    assert RunInputs.open(uncalibrated.output).pixel_sizes(A)["X"].value is None
    data = report(uncalibrated.result)
    assert data["images"][A]["pixel_sizes"] == "uncalibrated"
    assert data["calibration"] == {"file": None, "sha256": None, "statements": []}


def test_pixels_is_a_deliberate_statement_without_a_value(calibrated):
    state(calibrated.output, "ctrl,X,,pixels\nctrl,Y,,pixels\n")
    result = again(calibrated)
    assert result.written
    recorded = sizes(calibrated.output)
    assert (recorded["X"].value, recorded["X"].source) == (None, "pixels")
    # The header said 0.5 um: the statement is used, and the difference is a warning.
    found = issue(result.issues, "calibration_differs_from_header")
    assert found.paths == (A, "ctrl/rep1/b.ome.tif")
    assert "X stated pixels, header 0.5 micrometer" in found.message
    # Calibrated and uncalibrated images mixed: the existing warning, naming the source.
    missing = issue(result.issues, "missing_pixel_size")
    assert missing.paths == (A, "ctrl/rep1/b.ome.tif")
    assert "2 of these images are measured in pixels, as calibration.csv states" in (
        missing.message
    )
    assert RunInputs.open(calibrated.output).pixel_sizes(A)["X"].source == "pixels"
    assert report(result)["images"][A]["pixel_sizes"]["X"] == {
        "value": None,
        "unit": None,
        "source": "pixels",
    }


def test_a_statement_that_differs_from_the_header_is_used_and_named(calibrated):
    state(calibrated.output, f"{A},X,0.25,um\n{A},Y,0.25,um\n")
    result = again(calibrated)
    found = issue(result.issues, "calibration_differs_from_header")
    assert found.severity.value == "warning" and found.paths == (A,)
    assert f"{A} (X stated 0.25 micrometer, header 0.5 micrometer;" in found.message
    assert sizes(calibrated.output)["X"].value == 0.25
    assert sizes(calibrated.output, "ctrl/rep1/b.ome.tif")["X"].source == "header"
    # The dataset check runs on the sizes used.
    assert issue(result.issues, "different_pixel_sizes").paths == (A,)
    assert "from the image metadata and calibration.csv" in (
        issue(result.issues, "different_pixel_sizes").message
    )


def test_the_same_size_in_another_unit_is_no_conflict(calibrated):
    state(calibrated.output, ".,X,500,nm\n.,Y,0.5,micron\n")
    result = again(calibrated)
    assert "calibration_differs_from_header" not in codes(result.issues)
    assert "different_pixel_sizes" not in codes(result.issues)


def test_the_checks_use_the_resolved_sizes(tmp_path):
    # Headers that differ, stated the same for the whole experiment: nothing differs.
    experiment = tmp_path / "experiment"
    for name, size in (("a", 0.5), ("b", 1.0)):
        path = experiment / "c" / f"{name}.ome.tif"
        path.parent.mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(
            path,
            np.zeros((8, 8), np.uint8),
            ome=True,
            metadata={"axes": "YX", "PhysicalSizeX": size, "PhysicalSizeY": size},
        )
    output = tmp_path / "output"
    assert "different_pixel_sizes" in codes(preflight(experiment, output).issues)
    state(output, ".,X,0.5,um\n.,Y,0.5,um\n")
    pre = preflight(experiment, output)
    assert "different_pixel_sizes" not in codes(pre.issues)
    assert issue(pre.issues, "calibration_differs_from_header").paths == ("c/b.ome.tif",)


def test_y_and_x_are_stated_by_role_and_never_exchanged(tmp_path):
    experiment = tmp_path / "experiment"
    path = experiment / "c" / "a.ome.tif"
    path.parent.mkdir(parents=True)
    tifffile.imwrite(
        path,
        np.zeros((8, 10), np.uint8),
        ome=True,
        metadata={"axes": "YX", "PhysicalSizeX": 0.5, "PhysicalSizeY": 0.25},
    )
    output = tmp_path / "output"
    state(output, "c/a.ome.tif,Y,0.3,um\n")
    result = run_experiment(experiment, prefill_mapping("YX"), output_folder=output)
    assert result.written
    recorded = sizes(output, "c/a.ome.tif")
    assert (recorded["X"].value, recorded["X"].source) == (0.5, "header")
    assert (recorded["Y"].value, recorded["Y"].source) == (0.3, "stated")
    assert "Y stated 0.3 micrometer, header 0.25 micrometer" in (
        issue(result.issues, "calibration_differs_from_header").message
    )


def test_a_stated_axis_has_no_unsupported_header_unit(tmp_path):
    experiment = tmp_path / "experiment"
    path = experiment / "c" / "a.ome.tif"
    path.parent.mkdir(parents=True)
    tifffile.imwrite(
        path,
        np.zeros((8, 8), np.uint8),
        ome=True,
        metadata={
            "axes": "YX",
            "PhysicalSizeX": 1,
            "PhysicalSizeXUnit": "in",
            "PhysicalSizeY": 1,
            "PhysicalSizeYUnit": "in",
        },
    )
    output = tmp_path / "output"
    assert "unsupported_pixel_size_unit" in codes(preflight(experiment, output).issues)
    state(output, ".,X,2,um\n")
    found = issue(preflight(experiment, output).issues, "unsupported_pixel_size_unit")
    assert "Y in 'in'" in found.message and "X and Y" not in found.message


# Refusals -------------------------------------------------------------------------------------


def test_a_target_not_in_the_experiment_stops_the_run(calibrated):
    before = state(calibrated.output, "Ctrl,X,1,um\n").read_bytes()
    table = calibrated.result.csv_path.read_bytes()
    result = again(calibrated)
    assert not result.written
    found = issue(result.issues, "calibration_target_not_found")
    assert found.is_error and found.paths == ("Ctrl",)
    assert (calibrated.output / CALIBRATION_FILENAME).read_bytes() == before
    assert calibrated.result.csv_path.read_bytes() == table
    assert report(result)["status"] == "stopped"


def test_a_statement_for_an_excluded_image_is_not_unknown(calibrated):
    state(calibrated.output, f"{A},X,1,um\n")
    result = again(calibrated, exclude=[A])
    assert result.written and "calibration_target_not_found" not in codes(result.issues)


def test_an_unreadable_file_stops_the_run_and_is_kept(calibrated):
    path = state(calibrated.output, ".,X,1,inch\n")
    before = path.read_bytes()
    result = again(calibrated)
    assert not result.written
    found = issue(result.issues, "calibration_unreadable")
    assert "line 2" in found.message and found.is_error
    assert path.read_bytes() == before


# A recorded input ------------------------------------------------------------------------------


def test_a_changed_calibration_is_named_and_recomputes_no_row(calibrated):
    table = calibrated.result.csv_path.read_bytes()
    path = state(calibrated.output, ".,X,0.5,um\n.,Y,0.5,um\n")
    first = again(calibrated)
    (change,) = first.input_changes
    assert (change.name, change.recorded, change.given) == (
        "calibration",
        "none",
        "calibration.csv added",
    )
    # The same values, now stated instead of from the header: the source changes.
    assert change.effect == "no row is affected; the pixel sizes of 4 images change"
    assert first.read == () and first.csv_path.read_bytes() == table
    manifest = read_manifest(first.manifest_path)
    assert manifest.calibration_sha256 == read_calibration(path)[1]
    state(calibrated.output, f".,X,0.5,um\n.,Y,0.5,um\n{A},X,0.7,um\n")
    (change,) = again(calibrated).input_changes
    assert change.given == "calibration.csv edited"
    assert change.effect == "no row is affected; the pixel sizes of 1 image change"
    assert again(calibrated).input_changes == ()  # unchanged since
    path.unlink()
    (change,) = again(calibrated).input_changes
    assert change.given.startswith("none") and sizes(calibrated.output)["X"].source == "header"


def test_full_and_failed_runs_keep_the_calibration(calibrated):
    path = state(calibrated.output, ".,X,0.5,um\n.,Y,0.5,um\n")
    before = path.read_bytes()
    assert again(calibrated, mode="full").written
    assert path.read_bytes() == before
    assert not again(calibrated, bin_count=0).written
    assert path.read_bytes() == before
    assert "calibration.csv" not in report(again(calibrated))["replaced"]


def test_run_inputs_refuse_a_calibration_changed_or_added_since_the_run(calibrated):
    path = state(calibrated.output, ".,X,1,um\n")
    with pytest.raises(RunInputsError, match="calibration.csv was added since the run"):
        RunInputs.open(calibrated.output)
    assert again(calibrated).written
    RunInputs.open(calibrated.output)
    state(calibrated.output, ".,X,2,um\n")
    with pytest.raises(RunInputsError, match="calibration calibration.csv is not the one"):
        RunInputs.open(calibrated.output)
    path.unlink()
    with pytest.raises(RunInputsError, match="calibration"):
        RunInputs.open(calibrated.output)


def test_check_reports_the_resolved_sizes(calibrated, capsys):
    state(calibrated.output, ".,X,0.5,um\n.,Y,0.5,um\n")
    assert main(["check", str(calibrated.experiment), "--output", str(calibrated.output)]) == 0
    out = capsys.readouterr().out
    assert "Stated pixel sizes (calibration.csv):" in out
    data = json.loads((calibrated.output / "discovery_report.json").read_text(encoding="utf-8"))
    assert data["images"][A]["pixel_sizes"]["X"]["source"] == "stated"
    assert len(data["calibration"]["statements"]) == 2


# Stating sizes ---------------------------------------------------------------------------------


def test_write_calibration_holds_the_lock(tmp_path):
    calibration = Calibration((calibration_entry(".", "X", 1, "um"),))
    path = write_calibration(calibration, tmp_path / "out")
    assert read_calibration(path)[0] == calibration
    with output_lock(tmp_path / "out"):
        with pytest.raises(OutputLockedError, match="is locked by process"):
            write_calibration(Calibration(), tmp_path / "out")
    assert read_calibration(path)[0] == calibration


def test_the_calibrate_command_states_shows_and_removes(tmp_path, capsys):
    output = tmp_path / "out"
    assert main(["calibrate", "--output", str(output)]) == 0
    assert "No pixel size is stated" in capsys.readouterr().out
    assert not (output / CALIBRATION_FILENAME).exists()
    args = ["calibrate", "--output", str(output), "--size", ".", "YX", "0.65", "um"]
    assert main([*args, "--pixels", "ctrl/rep1", "Z"]) == 0
    out = capsys.readouterr().out
    assert ". X: 0.65 micrometer" in out and ". Y: 0.65 micrometer" in out
    assert "ctrl/rep1 Z: pixels" in out
    calibration, _ = read_calibration(output / CALIBRATION_FILENAME)
    assert {(e.target, e.axis) for e in calibration.entries} == {
        (".", "X"),
        (".", "Y"),
        ("ctrl/rep1", "Z"),
    }
    assert main(["calibrate", "--output", str(output), "--remove", ".", "X"]) == 0
    calibration, _ = read_calibration(output / CALIBRATION_FILENAME)
    assert {(e.target, e.axis) for e in calibration.entries} == {(".", "Y"), ("ctrl/rep1", "Z")}


@pytest.mark.parametrize(
    "extra",
    [
        ["--size", ".", "XX", "1", "um"],
        ["--size", ".", "C", "1", "um"],
        ["--size", ".", "X", "1", "inch"],
        ["--size", ".", "X", "1", "pixels"],
        ["--size", ".", "X", "-1", "um"],
    ],
)
def test_the_calibrate_command_refuses_and_writes_nothing(tmp_path, capsys, extra):
    output = tmp_path / "out"
    assert main(["calibrate", "--output", str(output), *extra]) == 1
    assert "Nothing was changed" in capsys.readouterr().out
    assert not (output / CALIBRATION_FILENAME).exists()


def test_the_calibrate_command_refuses_while_a_run_holds_the_lock(tmp_path, capsys):
    output = tmp_path / "out"
    output.mkdir()
    with output_lock(output):
        assert main(["calibrate", "--output", str(output), "--size", ".", "X", "1", "um"]) == 1
    assert "Another run is using this output folder" in capsys.readouterr().out
    assert not (output / CALIBRATION_FILENAME).exists()
