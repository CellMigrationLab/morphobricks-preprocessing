"""The run side of the Z projection (issue #176): manifest, ``RunInputs``, warning, report.

The regression is ``test_a_run_on_projected_copies_records_and_exposes_the_projection``:
before, the manifest had no ``projection`` key and ``RunInputs`` had no ``projection``, so a
brick reading the run saw plain 2-D images with no trace of the method or the source.
"""

import json

import numpy as np
import pytest

from morphobricks_preprocessing import (
    AxisMapping,
    Projection,
    RunInputs,
    convert_experiment,
    run_experiment,
)
from morphobricks_preprocessing.conversion_record import RECORD_FILENAME
from morphobricks_preprocessing.manifest import ManifestFormatError, read_manifest
from morphobricks_preprocessing.projection_run import MASKS_WARNING, RunProjection
from morphobricks_preprocessing.run_inputs_error import RunInputsError
from morphobricks_preprocessing.testing import _confirmed_design
from test_projection_convert import (
    NAMES,
    ZCYX,
    codes,
    stack_experiment,
    write_mask,
    write_stack,
)

CYX = AxisMapping("CYX", tuple("CYX"))
pytestmark = pytest.mark.filterwarnings("ignore:No experiment description:UserWarning")


def projected_run(tmp_path, *, method="max", z_range=None, masks=False):
    """Project two stacks, run on the copies; return (copies, output, masks folder, result)."""
    exp, copies, out = tmp_path / "exp", tmp_path / "copies", tmp_path / "out"
    stack_experiment(exp)
    folder = tmp_path / "masks"
    if masks:
        for name in NAMES:
            write_mask(folder / "Nuclei" / name, (8, 9), "YX")
    done = convert_experiment(exp, ZCYX, copies, projection=Projection(method, z_range),
                              masks_folder=folder if masks else None)  # fmt: skip
    assert done.written, done.issues
    run = run_experiment(copies, CYX, output_folder=out, design=_confirmed_design(copies),
                         masks_folder=folder if masks else None)  # fmt: skip
    assert run.written, run.issues
    return copies, out, folder, run


def manifest_of(out):
    return json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))


def test_a_run_on_projected_copies_records_and_exposes_the_projection(tmp_path):
    copies, out, _, run = projected_run(tmp_path, method="mean", z_range=(1, 4))
    record = json.loads((copies / RECORD_FILENAME).read_text(encoding="utf-8"))
    data = manifest_of(out)["projection"]
    assert data["method"] == "mean" and data["z_range"] == [1, 4]
    assert data["conversion_record"]["sha256"] == manifest_of(out)["conversion_record_sha256"]
    assert [i["relative_path"] for i in data["images"]] == sorted(NAMES)
    assert {tuple(i["z_range"]) for i in data["images"]} == {(1, 4)}
    entry = record["files"][0]["projection"]
    image = next(
        i for i in data["images"] if i["relative_path"] == record["files"][0]["relative_path"]
    )
    for key in ("source_content_sha256", "dtype_rule", "rounding", "source_dtype", "output_dtype"):
        assert image[key] == entry[key]
    assert data["masks_paired"] == [] and data["warnings"] == []
    inputs = RunInputs.open(out)
    assert isinstance(inputs.projection, RunProjection) and inputs.projection.method == "mean"
    assert inputs.projection.image(NAMES[0]).z_range == (1, 4)
    assert inputs.projection.to_dict() == data
    assert "mean projections of slices 1:4 (2 images)" in (out / "discovery_report.txt").read_text()
    assert "projection" in json.loads((out / "discovery_report.json").read_text())["conversion"]
    assert run.issues == () or MASKS_WARNING not in codes(run.issues)


def test_a_run_without_projection_has_no_projection_key(tmp_path):
    """Plain images, and named-axis copies that are not projections, record no projection."""
    exp = tmp_path / "exp"
    for name in NAMES:
        data = np.arange(3 * 8 * 9, dtype=np.uint16).reshape(3, 8, 9)
        write_stack(exp / name, data, "CYX")
    for label, root in (("plain", exp), ("copies", tmp_path / "copies")):
        if root != exp:
            done = convert_experiment(exp, AxisMapping("CYX", tuple("CYX")), root)
            assert done.written, done.issues
        out = tmp_path / f"out_{label}"
        run = run_experiment(root, CYX, output_folder=out, design=_confirmed_design(root))
        assert run.written, run.issues
        assert "projection" not in manifest_of(out)
        assert RunInputs.open(out).projection is None


def test_2d_masks_on_projected_images_record_the_warning(tmp_path):
    _, out, _, run = projected_run(tmp_path, masks=True)
    warned = [i for i in run.issues if i.code == MASKS_WARNING]
    assert len(warned) == 1 and not warned[0].is_error
    assert "max projections" in warned[0].message and len(warned[0].paths) == 2
    data = manifest_of(out)["projection"]
    assert data["warnings"] == [MASKS_WARNING] and len(data["masks_paired"]) == 2
    assert RunInputs.open(out).projection.warnings == (MASKS_WARNING,)
    text = (out / "discovery_report.txt").read_text(encoding="utf-8")
    assert MASKS_WARNING in text and "masks are never projected" in text


def test_3d_masks_next_to_projected_copies_stop_the_run(tmp_path):
    copies, out, masks, _ = projected_run(tmp_path)
    write_mask(masks / "Nuclei" / NAMES[0], (5, 8, 9), "ZYX")
    stopped = run_experiment(copies, CYX, output_folder=tmp_path / "out2", masks_folder=masks,
                             design=_confirmed_design(copies))  # fmt: skip
    assert not stopped.written and "mask_planes_mismatch" in codes(stopped.issues)


def edit(path, change):
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_a_manifest_that_disagrees_with_the_record_is_refused(tmp_path):
    _, out, _, _ = projected_run(tmp_path)
    manifest = out / "run_manifest.json"
    original = manifest.read_text(encoding="utf-8")
    for change in (
        lambda d: d["projection"].__setitem__("method", "sum"),
        lambda d: d["projection"]["images"][0].__setitem__("z_range", [0, 4]),
        lambda d: d["projection"]["images"][0].__setitem__("source_content_sha256", "0" * 64),
        lambda d: d["projection"]["images"].pop(),
        lambda d: d.pop("projection"),  # projected copies read as plain images
    ):
        edit(manifest, change)
        with pytest.raises(RunInputsError):  # (the table is checked first: same file bytes)
            RunInputs.open(out)
        manifest.write_text(original, encoding="utf-8")
    assert RunInputs.open(out).projection.method == "max"


def test_a_projection_without_its_record_is_refused(tmp_path):
    _, out, _, _ = projected_run(tmp_path)
    edit(out / "run_manifest.json", lambda d: d.__setitem__("conversion_record_sha256", None))
    with pytest.raises(RunInputsError, match="no conversion record"):
        RunInputs.open(out)


def test_a_missing_or_changed_record_is_refused(tmp_path):
    copies, out, _, _ = projected_run(tmp_path)
    record = copies / RECORD_FILENAME
    original = record.read_text(encoding="utf-8")
    edit(record, lambda d: d["projection"].__setitem__("method", "sum"))
    with pytest.raises(RunInputsError, match="not the one the run read"):
        RunInputs.open(out)
    record.unlink()
    with pytest.raises(RunInputsError, match="not the one the run read"):
        RunInputs.open(out)
    record.write_text(original, encoding="utf-8")
    assert RunInputs.open(out).projection is not None


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["projection"].__setitem__("method", "median"),
        lambda d: d["projection"].__setitem__("extra", 1),
        lambda d: d["projection"].__setitem__("warnings", [MASKS_WARNING]),
        lambda d: d["projection"]["images"][0].__setitem__("source_content_sha256", "abc"),
    ],
)
def test_a_malformed_projection_in_the_manifest_is_a_format_error(tmp_path, change):
    _, out, _, _ = projected_run(tmp_path)
    edit(out / "run_manifest.json", change)
    with pytest.raises(ManifestFormatError):
        read_manifest(out / "run_manifest.json")
