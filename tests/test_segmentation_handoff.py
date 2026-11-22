"""The segmentation hand-off (contract §15.5): channels in by ID, masks out as §15 expects.

The run is the owner's mixed-channel case: DNA, ACTIN and TUB everywhere, a fourth stain EXTRA
only in ``Biological_repeat_2``, listed first there so that the shared stains sit at other C
indices in that repeat. Each stain's pixels lie in their own range (``LEVELS``), so the tests
see at once which stain a plane holds, whatever its position.
"""

import dataclasses
import errno
import json
import os
from types import SimpleNamespace

import numpy as np
import pytest
import tifffile

from described_tree import check_masks, run_described
from description_tree import MAPPING
from morphobricks_preprocessing import Request
from morphobricks_preprocessing.manifest import MANIFEST_FILENAME
from morphobricks_preprocessing.run_inputs import RunInputs, RunInputsError
from morphobricks_preprocessing.segmentation_handoff import (
    images_to_segment,
    mask_path,
    resolve_segmentation,
    segmentation_input,
    write_mask,
)
from morphobricks_preprocessing.segmentation_record import (
    RECORD_FILENAME,
    SegmentationError,
    write_record,
)
from test_segmentation_record import batch
from test_stage3_channels import LEVELS, described_run, extra_stain

EXTRA_FIRST = (
    b'channels = ["DNA", "ACTIN", "TUB", "EXTRA"]',
    b'channels = ["EXTRA", "DNA", "ACTIN", "TUB"]',
)
MASKS_SECTION = b'\n[masks.Cells]\nsource_type = "Cells"\nrepresentation = "instances"\n'
REPEAT_2 = "Biological_repeat_2"
EXPECTED_ROUND_TRIP_ISSUES = []


@pytest.fixture
def run(tmp_path):
    data = extra_stain().replace(*EXTRA_FIRST)
    result, out, _ = described_run(tmp_path, data)
    assert result.written, [(i.code, i.message) for i in result.issues]
    return SimpleNamespace(
        inputs=RunInputs.open(out),
        out=out,
        experiment=tmp_path / "exp",
        masks=tmp_path / "masks",
        data=data,
    )


def labels_like(run, fov, value=1):
    height, width = run.inputs.plane_shape(fov)
    labels = np.zeros((height, width), np.uint16)
    labels[1:4, 1:4] = value
    return labels


# segmentation_input -----------------------------------------------------------------------


def test_channels_are_picked_by_id_at_each_units_own_index(run):
    fovs = run.inputs.fovs()
    assert {f.repeat_group for f in fovs} == {
        "Biological_repeat_1",
        REPEAT_2,
        "Biological_repeat_3",
    }
    for fov in fovs:
        image = segmentation_input(run.inputs, ("ACTIN", "DNA"), fov)
        assert image.shape == (2, 8, 8) and image.dtype == np.uint8
        actin, dna = image
        assert LEVELS["ACTIN"] <= actin.min() and actin.max() < LEVELS["ACTIN"] + 20
        assert LEVELS["DNA"] <= dna.min() and dna.max() < LEVELS["DNA"] + 20
        # The stored values, from the C index this image stores each ID at.
        stored = tifffile.imread(run.experiment / fov.relative_path)
        assert fov.channel_ids.index("DNA") == (1 if fov.repeat_group == REPEAT_2 else 0)
        np.testing.assert_array_equal(dna, stored[fov.channel_ids.index("DNA")])
        np.testing.assert_array_equal(actin, stored[fov.channel_ids.index("ACTIN")])


def test_the_order_given_is_the_order_returned(run):
    fov = next(f for f in run.inputs.fovs() if f.repeat_group == REPEAT_2)
    forward = segmentation_input(run.inputs, ["DNA", "TUB"], fov)
    backward = segmentation_input(run.inputs, ["TUB", "DNA"], fov)
    np.testing.assert_array_equal(forward, backward[::-1])
    assert segmentation_input(run.inputs, ("DNA",), fov).shape == (1, 8, 8)


def test_a_channel_some_images_lack_is_refused_with_the_resolvers_code(run):
    fov = run.inputs.fovs()[0]
    with pytest.raises(RunInputsError) as caught:
        segmentation_input(run.inputs, ("DNA", "EXTRA"), fov)
    (refusal,) = run.inputs.resolve_request(Request(("DNA", "EXTRA"))).refusals
    assert refusal.code == "A_SCOPE_INCOMPLETE"
    assert str(caught.value) == refusal.message
    assert getattr(caught.value, "code", None) == "A_SCOPE_INCOMPLETE"


def test_even_an_image_that_has_the_channel_is_refused_when_others_lack_it(run):
    stained = next(f for f in run.inputs.fovs() if f.repeat_group == REPEAT_2)
    assert "EXTRA" in stained.channel_ids
    with pytest.raises(RunInputsError, match="EXTRA"):
        segmentation_input(run.inputs, ("EXTRA",), stained)


@pytest.mark.parametrize("ids", [(), ("DNA", "DNA")])
def test_no_channel_or_a_channel_twice_is_refused(run, ids):
    with pytest.raises(SegmentationError, match="at least one channel"):
        segmentation_input(run.inputs, ids, run.inputs.fovs()[0])


@pytest.mark.parametrize("counts", [{"z_count": 3}, {"t_count": 2}])
def test_an_image_with_z_or_t_above_one_is_refused(run, counts):
    fov = dataclasses.replace(run.inputs.fovs()[0], **counts)
    with pytest.raises(SegmentationError, match="2D only"):
        segmentation_input(run.inputs, ("DNA",), fov)
    with pytest.raises(SegmentationError, match="2D only"):
        write_mask(run.masks, "Cells", fov, labels_like(run, fov), run.inputs)
    assert not run.masks.exists()


# images_to_segment and mask_path -----------------------------------------------------------


def test_mask_path_follows_the_layout_of_contract_15(run):
    fov = run.inputs.fovs()[0]
    assert fov.relative_path == "Control/Biological_repeat_1/img0.tif"
    assert mask_path(run.masks, "Cells", fov) == (
        run.masks / "Cells" / "Control" / "Biological_repeat_1" / "img0.tif"
    )
    for bad in ("", "..", "a/b", "."):
        with pytest.raises(SegmentationError, match="mask type|one folder"):
            mask_path(run.masks, bad, fov)


def test_images_to_segment_before_and_after_writing_some_masks(run):
    fovs = run.inputs.fovs()
    assert len(fovs) == 12
    assert images_to_segment(run.inputs, "Cells", run.masks) == fovs  # no masks folder yet
    for fov in fovs[:5]:
        write_mask(run.masks, "Cells", fov, labels_like(run, fov), run.inputs)
    assert images_to_segment(run.inputs, "Cells", run.masks) == fovs[5:]
    assert images_to_segment(run.inputs, "Nuclei", run.masks) == fovs  # another type
    for fov in fovs[5:]:
        write_mask(run.masks, "Cells", fov, labels_like(run, fov), run.inputs)
    assert images_to_segment(run.inputs, "Cells", run.masks) == ()


def test_a_mask_with_another_case_or_tiff_extension_counts_as_made(run):
    first, second, third = run.inputs.fovs()[:3]
    folder = run.masks / "Cells" / "Control" / "Biological_repeat_1"
    folder.mkdir(parents=True)
    (folder / "IMG0.TIFF").write_bytes(b"")
    (folder / "img1.ome.tif").write_bytes(b"")
    (folder / "note.txt").write_bytes(b"")
    left = images_to_segment(run.inputs, "Cells", run.masks)
    assert first not in left and second not in left and third in left
    with pytest.raises(SegmentationError, match="already exists"):
        write_mask(run.masks, "Cells", first, labels_like(run, first), run.inputs, overwrite=True)


def test_excluded_images_are_never_listed(tmp_path):
    data = extra_stain()
    result, out, _ = described_run(tmp_path, data)
    assert result.written
    excluded = "Control/Biological_repeat_1/img0.tif"
    rerun = run_described(tmp_path / "exp", MAPPING, output_folder=out, exclude=[excluded])
    assert rerun.written, [(i.code, i.message) for i in rerun.issues]
    inputs = RunInputs.open(out)
    listed = [f.relative_path for f in images_to_segment(inputs, "Cells", tmp_path / "masks")]
    assert excluded not in listed and len(listed) == 11


# write_mask --------------------------------------------------------------------------------


def test_write_mask_writes_a_compressed_int32_yx_tiff(run):
    fov = run.inputs.fovs()[0]
    path = write_mask(run.masks, "Cells", fov, labels_like(run, fov, 7), run.inputs)
    assert path == mask_path(run.masks, "Cells", fov)
    with tifffile.TiffFile(path) as tif:
        assert tif.series[0].axes == "YX"
        assert tif.pages[0].dtype == np.int32 and tif.pages[0].compression.name == "ADOBE_DEFLATE"
    stored = tifffile.imread(path)
    assert stored.dtype == np.int32 and stored.max() == 7 and stored.shape == (8, 8)
    assert [p.name for p in path.parent.iterdir() if p.name.startswith(".")] == []  # no temp left


def test_write_mask_accepts_boolean_and_any_integer_type(run):
    fovs = run.inputs.fovs()
    for fov, dtype in zip(fovs, (np.bool_, np.uint8, np.int64, np.uint64), strict=False):
        labels = labels_like(run, fov).astype(dtype)
        stored = tifffile.imread(write_mask(run.masks, "Cells", fov, labels, run.inputs))
        np.testing.assert_array_equal(stored, labels.astype(np.int32))
    top = np.zeros((8, 8), np.int64)
    top[0, 0] = 2_147_483_647
    path = write_mask(run.masks, "Cells", fovs[5], top, run.inputs)
    assert tifffile.imread(path).max() == 2_147_483_647


@pytest.mark.parametrize("name", ["NA", "nan", " Cells", "Cells ", "Cafe\u0301", "a/b", ".."])
def test_a_mask_type_preprocessing_would_refuse_is_refused_before_anything_is_written(run, name):
    fov = run.inputs.fovs()[0]
    with pytest.raises(SegmentationError):
        write_mask(run.masks, name, fov, labels_like(run, fov), run.inputs)
    with pytest.raises(SegmentationError):
        images_to_segment(run.inputs, name, run.masks)
    with pytest.raises(SegmentationError):
        mask_path(run.masks, name, fov)
    assert not run.masks.exists()


def test_write_mask_refuses_labels_that_are_not_a_mask(run):
    fov = run.inputs.fovs()[0]
    good = labels_like(run, fov)
    wrong = {
        "3D": (np.zeros((1, 8, 8), np.uint8), "2D"),
        "1D": (np.zeros(8, np.uint8), "2D"),
        "wrong size": (np.zeros((8, 9), np.uint8), "never resize"),
        "transposed": (np.zeros((9, 8), np.uint8), "never resize"),
        "float": (good.astype(np.float32), "integer"),
        "whole floats": (good.astype(np.float64), "integer"),
        "negative": (good.astype(np.int16) - 1, "negative"),
        "too large": (good.astype(np.int64) + 2_147_483_647, "above the limit"),
        "too large unsigned": (good.astype(np.uint64) + 2**40, "above the limit"),
    }
    for _name, (labels, message) in wrong.items():
        with pytest.raises(SegmentationError, match=message):
            write_mask(run.masks, "Cells", fov, labels, run.inputs)
    assert not run.masks.exists()  # nothing was created


def test_write_mask_never_overwrites_unless_asked(run):
    fov = run.inputs.fovs()[0]
    path = write_mask(run.masks, "Cells", fov, labels_like(run, fov, 1), run.inputs)
    before = path.read_bytes()
    with pytest.raises(SegmentationError, match="already exists"):
        write_mask(run.masks, "Cells", fov, labels_like(run, fov, 2), run.inputs)
    assert path.read_bytes() == before
    write_mask(run.masks, "Cells", fov, labels_like(run, fov, 2), run.inputs, overwrite=True)
    assert tifffile.imread(path).max() == 2


def test_a_failed_write_leaves_the_earlier_mask_and_no_temporary_file(run, monkeypatch):
    fov = run.inputs.fovs()[0]
    path = write_mask(run.masks, "Cells", fov, labels_like(run, fov, 1), run.inputs)
    before = path.read_bytes()

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(tifffile, "imwrite", broken)
    with pytest.raises(OSError, match="disk full"):
        write_mask(run.masks, "Cells", fov, labels_like(run, fov, 2), run.inputs, overwrite=True)
    assert path.read_bytes() == before
    assert sorted(os.listdir(path.parent)) == [path.name]


def test_write_mask_refuses_a_masks_folder_inside_the_experiment_or_output(run):
    fov = run.inputs.fovs()[0]
    for folder, code in (
        (run.experiment / "masks", "masks_inside_experiment"),
        (run.experiment, "masks_inside_experiment"),
        (run.out / "masks", "inside the output folder"),
        (run.out, "output_inside_masks"),
    ):
        with pytest.raises(SegmentationError, match=code):
            write_mask(folder, "Cells", fov, labels_like(run, fov), run.inputs)
        with pytest.raises(SegmentationError, match=code):
            images_to_segment(run.inputs, "Cells", folder)
    assert not (run.experiment / "masks").exists() and not (run.out / "masks").exists()
    with pytest.raises(SegmentationError, match="experiment_inside_masks"):
        write_mask(run.experiment.parent, "Cells", fov, labels_like(run, fov), run.inputs)


def test_masks_written_here_pass_preprocessings_own_mask_checks(run):
    fovs = run.inputs.fovs()
    for fov in fovs:
        write_mask(run.masks, "Cells", fov, labels_like(run, fov, 3), run.inputs)
    write_record(run.masks, "Cells", batch())  # the record beside the masks is not a mask
    (run.out / "morphobricks.toml").write_bytes(run.data + MASKS_SECTION)
    again = run_described(run.experiment, MAPPING, output_folder=run.out, masks_folder=run.masks)
    assert again.written, [(i.code, i.message) for i in again.issues]
    assert sorted(i.code for i in again.issues) == EXPECTED_ROUND_TRIP_ISSUES
    manifest = json.loads((run.out / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    entries = manifest["masks"]["files"]
    assert sorted(e["image"] for e in entries) == sorted(f.relative_path for f in fovs)
    assert {e["type"] for e in entries} == {"Cells"}
    reopened = RunInputs.open(check_masks(run.out))  # the mask step reads every mask
    assert reopened.mask_types() == ("Cells",)
    mask = reopened.read_mask(fovs[0].relative_path, "Cells")
    assert mask.dtype == np.int32 and mask.shape == (8, 8) and mask.max() == 3
    assert images_to_segment(reopened, "Cells", run.masks) == ()


# The pairing is the run's own: any case, either layout (review of #367) ------------------------


def twin_cases(run):
    """Return the existing masks of ``img0`` that the run would pair, each in its own tree."""
    return {
        "other case path": run.masks / "Cells" / "control" / "biological_repeat_1" / "img0.tif",
        "second layout": run.masks / "Control" / "Biological_repeat_1" / "img0_Cells.tif",
    }


def assert_only_the_twin_is_on_disk(run, twin):
    """Nothing was written beside the twin, and the twin is untouched.

    On a file system that ignores case (Windows, macOS) the twin and the mask path are one file, so
    "the mask path does not exist" cannot be asserted there; the names listed from disk can.
    """
    files = [path for path in run.masks.rglob("*") if path.is_file()]
    assert [path.relative_to(run.masks).as_posix() for path in files] == [
        twin.relative_to(run.masks).as_posix()
    ]
    assert twin.read_bytes() == b""


@pytest.mark.parametrize("case", ["other case path", "second layout"])
def test_a_mask_the_run_would_pair_counts_as_made(run, case):
    fovs = run.inputs.fovs()
    first = fovs[0]
    assert first.relative_path == "Control/Biological_repeat_1/img0.tif"
    twin = twin_cases(run)[case]
    twin.parent.mkdir(parents=True)
    twin.write_bytes(b"")
    assert images_to_segment(run.inputs, "Cells", run.masks) == fovs[1:]
    for overwrite in (False, True):
        with pytest.raises(SegmentationError, match="already exists") as caught:
            write_mask(
                run.masks, "Cells", first, labels_like(run, first), run.inputs, overwrite=overwrite
            )
        assert twin.relative_to(run.masks).as_posix() in str(caught.value)
    assert_only_the_twin_is_on_disk(run, twin)


@pytest.mark.parametrize("spelling", ["cells", "CELLS"])
def test_a_type_spelled_in_two_ways_is_refused_as_the_run_refuses_it(run, spelling):
    twin = run.masks / spelling / "Control" / "Biological_repeat_1" / "img0.tif"
    twin.parent.mkdir(parents=True)
    twin.write_bytes(b"")
    first = run.inputs.fovs()[0]
    with pytest.raises(SegmentationError, match="mask_names_differ_in_case"):
        images_to_segment(run.inputs, "Cells", run.masks)
    with pytest.raises(SegmentationError, match="mask_names_differ_in_case"):
        write_mask(run.masks, "Cells", first, labels_like(run, first), run.inputs)
    assert_only_the_twin_is_on_disk(run, twin)


def test_two_masks_of_one_image_and_type_are_refused_before_writing(run):
    fovs = run.inputs.fovs()
    write_mask(run.masks, "Cells", fovs[0], labels_like(run, fovs[0]), run.inputs)
    twin = twin_cases(run)["second layout"]  # the other layout: the same image and type again
    twin.parent.mkdir(parents=True)
    twin.write_bytes(b"")
    with pytest.raises(SegmentationError, match="duplicate_mask"):
        write_mask(
            run.masks, "Cells", fovs[0], labels_like(run, fovs[0]), run.inputs, overwrite=True
        )
    with pytest.raises(SegmentationError, match="duplicate_mask"):
        write_mask(run.masks, "Cells", fovs[3], labels_like(run, fovs[3]), run.inputs)
    with pytest.raises(SegmentationError, match="duplicate_mask"):
        images_to_segment(run.inputs, "Cells", run.masks)
    assert not mask_path(run.masks, "Cells", fovs[3]).exists()


def test_overwrite_replaces_only_the_file_at_mask_path_and_refuses_a_twin_elsewhere(run):
    first = run.inputs.fovs()[0]
    path = write_mask(run.masks, "Cells", first, labels_like(run, first, 1), run.inputs)
    write_mask(run.masks, "Cells", first, labels_like(run, first, 2), run.inputs, overwrite=True)
    assert tifffile.imread(path).max() == 2
    twin = twin_cases(run)["second layout"]
    twin.parent.mkdir(parents=True)
    twin.write_bytes(b"")
    before = path.read_bytes()
    with pytest.raises(SegmentationError, match="img0_Cells.tif"):
        write_mask(
            run.masks, "Cells", first, labels_like(run, first, 3), run.inputs, overwrite=True
        )
    assert path.read_bytes() == before and twin.read_bytes() == b""


def test_a_masks_folder_the_run_would_refuse_is_refused(run, tmp_path):
    fovs = run.inputs.fovs()
    outside = tmp_path / "elsewhere.tif"
    outside.write_bytes(b"")
    folder = run.masks / "Cells" / "Control" / "Biological_repeat_1"
    folder.mkdir(parents=True)
    (folder / "img1.tif").symlink_to(outside)  # a link leaving the masks folder (§1 links)
    with pytest.raises(SegmentationError, match="link"):
        images_to_segment(run.inputs, "Cells", run.masks)
    with pytest.raises(SegmentationError, match="link"):
        write_mask(run.masks, "Cells", fovs[5], labels_like(run, fovs[5]), run.inputs)
    assert not mask_path(run.masks, "Cells", fovs[5]).exists()


# Publishing never replaces a mask another writer made in between ----------------------------


def test_a_mask_that_appears_before_claiming_is_never_replaced(run, monkeypatch):
    fov = run.inputs.fovs()[0]
    target = mask_path(run.masks, "Cells", fov)
    real = os.open

    def racing(path, flags, *args, **kwargs):
        if os.fspath(path) == os.fspath(target):
            with open(target, "wb") as other:  # the other writer's mask, made after our check
                other.write(b"other writer")
        return real(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", racing)
    with pytest.raises(SegmentationError, match="appeared"):
        write_mask(run.masks, "Cells", fov, labels_like(run, fov), run.inputs)
    assert target.read_bytes() == b"other writer"
    assert sorted(os.listdir(target.parent)) == [target.name]  # our temporary file is gone


def test_two_writers_one_claims_the_name_and_the_other_is_refused(run, monkeypatch):
    fov = run.inputs.fovs()[0]
    target = mask_path(run.masks, "Cells", fov)
    real, seen = os.replace, {}

    def second_writer_in_the_window(source, destination, **kwargs):
        if os.fspath(destination) == os.fspath(target) and "size" not in seen:
            seen["size"] = target.stat().st_size  # the claim is an empty file, not half a mask
            with pytest.raises(SegmentationError, match="already exists"):
                write_mask(run.masks, "Cells", fov, labels_like(run, fov, 9), run.inputs)
        return real(source, destination, **kwargs)

    monkeypatch.setattr(os, "replace", second_writer_in_the_window)
    write_mask(run.masks, "Cells", fov, labels_like(run, fov, 4), run.inputs)
    assert seen["size"] == 0
    assert tifffile.imread(target).max() == 4  # the first writer's mask, untouched by the second
    assert os.listdir(target.parent) == [target.name]


def test_a_failure_after_the_claim_leaves_no_placeholder_and_no_temporary_file(run, monkeypatch):
    fov = run.inputs.fovs()[0]

    def failing(source, destination, **kwargs):
        raise OSError(errno.EIO, "disk error")

    monkeypatch.setattr(os, "replace", failing)
    with pytest.raises(OSError, match="disk error"):
        write_mask(run.masks, "Cells", fov, labels_like(run, fov), run.inputs)
    assert os.listdir(mask_path(run.masks, "Cells", fov).parent) == []
    monkeypatch.undo()
    write_mask(run.masks, "Cells", fov, labels_like(run, fov), run.inputs)  # nothing blocks it


def test_a_failed_move_keeps_a_file_another_writer_put_there(run, monkeypatch):
    fov = run.inputs.fovs()[0]
    target = mask_path(run.masks, "Cells", fov)

    def failing(source, destination, **kwargs):
        target.write_bytes(b"other writer")  # replaced our placeholder with a real mask
        raise OSError(errno.EIO, "disk error")

    monkeypatch.setattr(os, "replace", failing)
    with pytest.raises(OSError, match="disk error"):
        write_mask(run.masks, "Cells", fov, labels_like(run, fov), run.inputs)
    assert target.read_bytes() == b"other writer"


def test_without_hard_links_the_mask_is_written_and_never_replaced(run, monkeypatch):
    fov = run.inputs.fovs()[0]

    def no_links(*args, **kwargs):
        raise OSError(errno.EPERM, "hard links are not supported here")

    monkeypatch.setattr(os, "link", no_links)  # a Google Drive mount
    target = write_mask(run.masks, "Cells", fov, labels_like(run, fov, 4), run.inputs)
    assert tifffile.imread(target).max() == 4
    assert os.listdir(target.parent) == [target.name]
    with pytest.raises(SegmentationError, match="already exists"):
        write_mask(run.masks, "Cells", fov, labels_like(run, fov, 5), run.inputs)
    assert tifffile.imread(target).max() == 4


# The record file is skipped at the type folder's root only ------------------------------------


def test_a_stray_file_named_like_the_record_still_gets_its_warning(run):
    for fov in run.inputs.fovs():
        write_mask(run.masks, "Cells", fov, labels_like(run, fov, 3), run.inputs)
    write_record(run.masks, "Cells", batch())
    stray = run.masks / "Cells" / "Control" / RECORD_FILENAME
    stray.write_text("{}", encoding="utf-8")
    (run.out / "morphobricks.toml").write_bytes(run.data + MASKS_SECTION)
    again = run_described(run.experiment, MAPPING, output_folder=run.out, masks_folder=run.masks)
    assert again.written, [(i.code, i.message) for i in again.issues]
    (warning,) = [i for i in again.issues if i.code == "mask_without_image"]
    assert list(warning.paths) == [f"Cells/Control/{RECORD_FILENAME}"]


# One resolution for a whole batch ----------------------------------------------------------------


def test_a_plan_reads_what_segmentation_input_reads(run):
    plan = resolve_segmentation(run.inputs, ["TUB", "DNA"])
    assert plan.channel_ids == ("TUB", "DNA")
    for fov in run.inputs.fovs():
        np.testing.assert_array_equal(
            plan.read(fov), segmentation_input(run.inputs, ("TUB", "DNA"), fov)
        )


def test_a_batch_resolves_once(run, monkeypatch):
    calls = []
    original = RunInputs.resolve_request

    def counting(self, request):
        calls.append(request)
        return original(self, request)

    monkeypatch.setattr(RunInputs, "resolve_request", counting)
    plan = resolve_segmentation(run.inputs, ("DNA",))
    for fov in run.inputs.fovs():
        plan.read(fov)
    assert len(calls) == 1
    segmentation_input(run.inputs, ("DNA",), run.inputs.fovs()[0])
    assert len(calls) == 2  # the one-image form resolves on every call


def test_a_plan_refuses_as_the_resolver_does_and_as_the_wrapper_does(run):
    with pytest.raises(RunInputsError) as caught:
        resolve_segmentation(run.inputs, ("DNA", "EXTRA"))
    assert getattr(caught.value, "code", None) == "A_SCOPE_INCOMPLETE"
    for ids in ((), ("DNA", "DNA")):
        with pytest.raises(SegmentationError, match="at least one channel"):
            resolve_segmentation(run.inputs, ids)
    plan = resolve_segmentation(run.inputs, ("DNA",))
    fov = dataclasses.replace(run.inputs.fovs()[0], t_count=2)
    with pytest.raises(SegmentationError, match="2D only"):
        plan.read(fov)
    other = dataclasses.replace(run.inputs.fovs()[0], relative_path="nowhere.tif")
    with pytest.raises(SegmentationError, match="not an image of this run"):
        plan.read(other)
