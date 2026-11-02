"""PP-8c item 2: no pixel size is recorded for an axis the image does not have.

A max projection saved as ``CYX`` (MorphoRelate's HPMEC data) keeps the z-step of its
original stack in its metadata (Z = 0.43 µm). The run recorded it as the image's Z size,
from the header, although the image has no Z. Now a Z (or T) size is recorded only for an
image with that role; otherwise the axis has no value and no source. A ``calibration.csv``
statement for an axis an image lacks is not recorded for it either: the warning
``calibration_axis_absent`` names the images.
"""

import json

import numpy as np
import tifffile

from morphobricks_preprocessing import (
    RunInputs,
    prefill_mapping,
    read_manifest,
    run_experiment,
)
from morphobricks_preprocessing.cli import main

HEADER = {"PhysicalSizeX": 0.5, "PhysicalSizeY": 0.5, "PhysicalSizeZ": 0.43, "TimeIncrement": 2.0}


def write(path, axes, shape, **metadata):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.random.default_rng(0).integers(0, 4000, shape, dtype=np.uint16)
    tifffile.imwrite(
        path, data, ome=True, photometric="minisblack", metadata={"axes": axes, **metadata}
    )


def sizes(output, path):
    entry = read_manifest(output / "run_manifest.json").file(path)
    return {size.role: size.to_dict() for size in entry.pixel_sizes}


def none(role):
    return {"role": role, "value": None, "unit": None, "source": None}


def report(output):
    return json.loads((output / "discovery_report.json").read_text(encoding="utf-8"))


def state(output, text):
    output.mkdir(parents=True, exist_ok=True)
    (output / "calibration.csv").write_text("target,axis,size,unit\n" + text, encoding="utf-8")


def test_a_projection_records_no_z_or_t_size_from_its_header(tmp_path):
    root, output = tmp_path / "exp", tmp_path / "out"
    write(root / "ctrl" / "a.ome.tif", "CYX", (2, 8, 8), **HEADER)
    result = run_experiment(root, prefill_mapping("CYX"), output_folder=output)
    assert result.written
    found = sizes(output, "ctrl/a.ome.tif")
    assert found["X"]["source"] == found["Y"]["source"] == "header"
    assert found["Z"] == none("Z")
    assert found["T"] == none("T")
    assert set(report(output)["images"]["ctrl/a.ome.tif"]["pixel_sizes"]) == {"X", "Y"}
    inputs = RunInputs.open(output)
    assert inputs.pixel_sizes("ctrl/a.ome.tif")["Z"].source is None
    assert "calibration_axis_absent" not in {issue.code for issue in result.issues}


def test_an_image_with_the_role_keeps_its_header_size(tmp_path):
    root, output = tmp_path / "exp", tmp_path / "out"
    write(root / "ctrl" / "a.ome.tif", "TZYX", (2, 3, 8, 8), **HEADER)
    assert run_experiment(root, prefill_mapping("TZYX"), output_folder=output).written
    found = sizes(output, "ctrl/a.ome.tif")
    assert found["Z"]["value"] == 0.43 and found["Z"]["source"] == "header"
    assert found["T"]["value"] == 2.0 and found["T"]["source"] == "header"


def test_a_z_statement_is_not_recorded_for_images_without_z(tmp_path):
    root, output = tmp_path / "exp", tmp_path / "out"
    write(root / "ctrl" / "a.ome.tif", "YX", (8, 8), PhysicalSizeX=0.5, PhysicalSizeY=0.5)
    write(root / "ctrl" / "b.ome.tif", "YX", (8, 8), PhysicalSizeX=0.5, PhysicalSizeY=0.5)
    state(output, ".,Z,2,um\nctrl/a.ome.tif,X,0.4,um\n")
    result = run_experiment(root, prefill_mapping("YX"), output_folder=output)
    assert result.written
    (absent,) = [issue for issue in result.issues if issue.code == "calibration_axis_absent"]
    assert absent.severity.value == "warning"
    assert absent.paths == ("ctrl/a.ome.tif", "ctrl/b.ome.tif")
    assert "Z" in absent.message and "no Z" in absent.message
    assert sizes(output, "ctrl/a.ome.tif")["Z"] == none("Z")
    assert sizes(output, "ctrl/a.ome.tif")["X"]["source"] == "stated"
    codes = [issue["code"] for issue in report(output)["issues"]]
    assert "calibration_axis_absent" in codes


def test_a_statement_for_the_whole_experiment_sizes_only_the_images_with_the_axis(tmp_path):
    root, output = tmp_path / "exp", tmp_path / "out"
    write(root / "ctrl" / "stack.ome.tif", "ZYX", (3, 8, 8))
    write(root / "ctrl" / "flat.ome.tif", "YX", (8, 8))
    state(output, ".,Z,2,um\n")
    result = run_experiment(
        root, prefill_mapping("ZYX"), output_folder=output, missing_as_singleton=True
    )
    assert result.written
    assert sizes(output, "ctrl/stack.ome.tif")["Z"]["source"] == "stated"
    assert sizes(output, "ctrl/flat.ome.tif")["Z"] == none("Z")
    (absent,) = [issue for issue in result.issues if issue.code == "calibration_axis_absent"]
    assert absent.paths == ("ctrl/flat.ome.tif",)


def test_rows_recorded_with_a_z_size_from_an_earlier_version_are_reused(tmp_path):
    root, output = tmp_path / "exp", tmp_path / "out"
    write(root / "ctrl" / "a.ome.tif", "CYX", (2, 8, 8), **HEADER)
    assert run_experiment(root, prefill_mapping("CYX"), output_folder=output).written
    table = (output / "plane_histograms.csv").read_bytes()
    # The manifest as an earlier version wrote it: the unused z-step recorded.
    path = output / "run_manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["files"][0]["pixel_sizes"][2] = {
        "role": "Z", "value": 0.43, "unit": "micrometer", "source": "header"
    }  # fmt: skip
    path.write_text(json.dumps(data), encoding="utf-8")
    again = run_experiment(output_folder=output)
    assert again.written and again.reused == ("ctrl/a.ome.tif",) and again.read == ()
    assert (output / "plane_histograms.csv").read_bytes() == table
    assert sizes(output, "ctrl/a.ome.tif")["Z"] == none("Z")


def test_check_reports_no_z_size_for_a_projection(tmp_path, capsys):
    root, output = tmp_path / "exp", tmp_path / "out"
    write(root / "ctrl" / "a.ome.tif", "CYX", (2, 8, 8), **HEADER)
    assert main(["check", str(root), "--output", str(output)]) == 0
    assert set(report(output)["images"]["ctrl/a.ome.tif"]["pixel_sizes"]) == {"X", "Y"}


def test_the_text_report_counts_z_sizes_only_for_images_with_z(tmp_path):
    root, output = tmp_path / "exp", tmp_path / "out"
    write(root / "ctrl" / "stack.ome.tif", "ZYX", (3, 8, 8), **HEADER)
    write(root / "ctrl" / "flat.ome.tif", "YX", (8, 8), **HEADER)
    result = run_experiment(
        root, prefill_mapping("ZYX"), output_folder=output, missing_as_singleton=True
    )
    assert result.written
    text = (output / "discovery_report.txt").read_text(encoding="utf-8")
    assert "  Z step: 1 from the header, 0 stated, 0 in pixels, 0 unknown; 1 without Z" in text
    assert "Time step" not in text  # no image has T: no T size is recorded
