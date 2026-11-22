"""The Cellpose glue (step 2 of the segmentation hand-off), against a stand-in for Cellpose.

Cellpose 4.2.1.1 substitutes a model it cannot find, falls back from the GPU to the CPU and
downloads uncached weights; the stand-in (``tests/cellpose_stub.py``) does the same, so each
refusal is tested against the behaviour it guards. The run is the hand-off tests' mixed-channel
case: DNA, ACTIN and TUB everywhere and a fourth stain listed first in one repeat, so a stain's
C index differs between repeats while its pixels (``LEVELS``) tell which stain a plane holds.
"""

import dataclasses
import hashlib
import json
import subprocess
import sys

import numpy as np
import pytest

from cellpose_stub import custom_model, install, weights_bytes
from described_tree import check_masks, run_described
from description_tree import MAPPING
from morphobricks_preprocessing import segmentation_cellpose as cp
from morphobricks_preprocessing import segmentation_cellpose_model as model_module
from morphobricks_preprocessing.manifest import MANIFEST_FILENAME
from morphobricks_preprocessing.run_inputs import RunInputs
from morphobricks_preprocessing.segmentation_handoff import (
    images_to_segment,
    mask_path,
    resolve_segmentation,
)
from morphobricks_preprocessing.segmentation_record import SegmentationError, read_record
from test_segmentation_handoff import MASKS_SECTION, REPEAT_2
from test_stage3_channels import LEVELS


@pytest.fixture
def run(mixed_run):
    return mixed_run


@pytest.fixture
def stub(monkeypatch, tmp_path):
    return install(monkeypatch, tmp_path)


def make_job(run, ids=("TUB", "DNA"), model="cpsam_v2", device="cpu", settings=None, **extra):
    loaded = cp.load_model(model, device)
    return cp.SegmentationJob(
        inputs=run.inputs,
        plan=resolve_segmentation(run.inputs, ids),
        loaded=loaded,
        settings=settings or cp.Settings(),
        mask_type="Cells",
        masks_root=run.masks,
        **extra,
    )


# inert import ------------------------------------------------------------------------------


def test_importing_the_modules_imports_no_cellpose_torch_or_dinov3():
    code = (
        "import sys\n"
        "import morphobricks_preprocessing\n"
        "import morphobricks_preprocessing.segmentation_cellpose\n"
        "import morphobricks_preprocessing.segmentation_cellpose_model\n"
        "bad = [m for m in ('cellpose', 'torch', 'dinov3') if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


# the model is validated before it is loaded (audit B7) --------------------------------------


@pytest.mark.parametrize("name", ["cpsam_v2", "cpsam", "cpdino", "cpdino-vitb"])
def test_the_four_built_in_names_are_accepted(name):
    assert cp.choose_model(name) == cp.ModelChoice(name)
    assert cp.DEFAULT_MODEL == "cpsam_v2" and cp.BUILTIN_MODELS[0] == "cpsam_v2"


@pytest.mark.parametrize("name", ["cpsam_v3", "cyto3", "nuclei", "CPSAM", "cpsam ", "", "no/such"])
def test_a_name_or_path_that_is_not_a_model_is_refused_before_loading(stub, name):
    with pytest.raises(SegmentationError, match="never substitutes a model"):
        cp.load_model(name, "cpu")
    assert stub.constructed == [] and stub.downloaded == []  # nothing loaded, nothing fetched


def test_a_custom_model_is_an_existing_file_not_a_folder(stub, tmp_path):
    path = custom_model(tmp_path)
    choice = cp.choose_model(path)
    assert choice == cp.ModelChoice("my_model", path.resolve()) and not choice.builtin
    with pytest.raises(SegmentationError, match="is a folder"):
        cp.choose_model(path.parent)
    with pytest.raises(SegmentationError, match="not a model name or a file"):
        cp.choose_model(path.parent / "missing")


def test_a_built_in_model_on_disk_is_loaded_by_its_absolute_path(stub):
    loaded = cp.load_model("cpsam", "cpu")
    (built,) = stub.constructed
    assert built.pretrained_model == str(
        stub.models_dir / "cpsam"
    )  # no name Cellpose could resolve
    assert (loaded.name, loaded.file, loaded.device, loaded.tile_size) == (
        "cpsam",
        "cpsam",
        "cpu",
        256,
    )


def test_a_custom_model_is_loaded_by_its_absolute_path_and_recorded_by_it(stub, tmp_path):
    path = custom_model(tmp_path)
    loaded = cp.load_model(str(path), "cpu")
    assert stub.constructed[0].pretrained_model == str(path.resolve())
    assert loaded.name == "my_model" and loaded.file == str(path.resolve())
    assert loaded.sha256 == hashlib.sha256(weights_bytes("my_model")).hexdigest()


def test_a_substituted_model_is_refused_after_loading(stub, tmp_path):
    stub.substitute = str(stub.models_dir / "cpsam_v2")  # Cellpose's silent fallback
    with pytest.raises(SegmentationError, match="substituted another model"):
        cp.load_model("cpsam", "cpu")
    with pytest.raises(SegmentationError, match="substituted another model"):
        cp.load_model(str(custom_model(tmp_path)), "cpu")


def test_the_stand_in_really_substitutes_what_the_checks_guard_against(stub):
    # The unguarded call: an unknown name silently gives cpsam_v2 (4.2.1.1, models.py).
    model = sys.modules["cellpose.models"].CellposeModel(
        pretrained_model="no_such_model", device=sys.modules["torch"].device("cpu")
    )
    assert model.pretrained_model.endswith("cpsam_v2")


# the device is explicit and verified (audit B8) ---------------------------------------------


def test_a_gpu_that_is_not_available_is_refused_and_never_replaced_by_the_cpu(stub):
    for device in ("cuda", "mps"):
        with pytest.raises(SegmentationError, match=f"{device} was asked for but is not available"):
            cp.load_model("cpsam_v2", device)
    assert stub.constructed == []


def test_a_model_that_sits_on_another_device_than_asked_is_refused(stub):
    stub.cuda, stub.device_type = True, "cpu"  # Cellpose's silent GPU -> CPU fallback
    with pytest.raises(SegmentationError, match="cuda was asked for but the model sits on cpu"):
        cp.load_model("cpsam_v2", "cuda")


def test_the_gpu_is_used_when_asked_and_present(stub):
    stub.cuda = True
    loaded = cp.load_model("cpsam_v2", "cuda")
    assert loaded.device == "cuda" and stub.constructed[0].gpu is True
    assert stub.constructed[0].device.type == "cuda"
    assert cp.load_model("cpsam_v2", "cpu").device == "cpu"
    with pytest.raises(SegmentationError, match="must be one of"):
        cp.load_model("cpsam_v2", "gpu")


# no silent download, the weights are hashed (audit B9) --------------------------------------


def test_weights_that_are_not_on_disk_are_not_downloaded_unless_allowed(stub):
    (stub.models_dir / "cpsam").unlink()
    with pytest.raises(SegmentationError, match="Hugging Face"):
        cp.load_model("cpsam", "cpu")
    assert stub.constructed == [] and stub.downloaded == []
    loaded = cp.load_model("cpsam", "cpu", allow_download=True)
    assert stub.downloaded == ["cpsam"] and stub.constructed[0].pretrained_model == "cpsam"
    assert loaded.sha256 == hashlib.sha256(weights_bytes("cpsam")).hexdigest()


def test_a_file_in_the_working_folder_that_shadows_a_download_is_refused(
    stub, tmp_path, monkeypatch
):
    (stub.models_dir / "cpsam").unlink()
    (tmp_path / "cpsam").write_bytes(b"not the model")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SegmentationError, match="working folder"):
        cp.load_model("cpsam", "cpu", allow_download=True)
    assert stub.constructed == []


@pytest.mark.parametrize("found", [None, "4.2.0", "4.3.0"])
def test_another_cellpose_version_or_none_is_refused(stub, found):
    stub.version = found
    with pytest.raises(SegmentationError, match="runs Cellpose 4.2.1.1"):
        cp.load_model("cpsam_v2", "cpu")
    assert stub.constructed == []


def test_the_model_name_file_and_weights_hash_are_in_the_record(run, stub):
    job = make_job(run)
    result = cp.run_batch(job, batch_id="b1")
    (batch,) = read_record(run.masks, "Cells").batches
    assert batch == result.batch
    assert (batch.tool, batch.tool_version) == ("cellpose", "4.2.1.1")
    assert (batch.model_name, batch.model_file) == ("cpsam_v2", "cpsam_v2")
    assert batch.model_sha256 == hashlib.sha256(weights_bytes("cpsam_v2")).hexdigest()
    assert batch.device == "cpu"
    manifest = (run.out / MANIFEST_FILENAME).read_bytes()
    assert batch.run_manifest_sha256 == hashlib.sha256(manifest).hexdigest()


# DINOv3 models: installed only when chosen, at a pinned commit ------------------------------


def test_the_dinov3_install_command_pins_the_commit():
    command = model_module.dinov3_install_command()
    assert command[1:4] == ["-m", "pip", "install"]
    assert command[-1] == f"git+https://github.com/facebookresearch/dinov3@{cp.DINOV3_COMMIT}"
    assert len(cp.DINOV3_COMMIT) == 40


@pytest.mark.parametrize("commit", [None, "0" * 40])
def test_a_dino_model_needs_the_pinned_dinov3_commit(stub, commit):
    stub.dinov3_commit = commit
    with pytest.raises(SegmentationError, match=f"commit {cp.DINOV3_COMMIT}.*pip install"):
        cp.load_model("cpdino", "cpu", allow_download=True)
    assert stub.constructed == []
    cp.load_model("cpsam_v2", "cpu")  # a SAM model needs no DINOv3 at all


def test_a_dino_model_needs_cellpose_to_have_imported_dinov3(monkeypatch, tmp_path):
    state = install(monkeypatch, tmp_path, has_cpdino=False)
    with pytest.raises(SegmentationError, match="Restart the runtime"):
        cp.load_model("cpdino-vitb", "cpu", allow_download=True)
    assert state.constructed == []


def test_a_dino_model_records_the_dinov3_commit(run, stub):
    loaded = cp.load_model("cpdino", "cpu", allow_download=True)
    assert loaded.dinov3_commit == cp.DINOV3_COMMIT and loaded.tile_size == 384
    assert stub.downloaded == ["cpdino"]
    params = cp.inference_parameters(loaded, cp.Settings(), ("DNA",))
    assert params["dinov3_commit"] == cp.DINOV3_COMMIT and params["bsize"] == 384


def test_dinov3_is_installed_only_when_asked_and_a_failure_is_refused(monkeypatch):
    ran = []
    monkeypatch.setattr(model_module.subprocess, "run", lambda cmd, check: ran.append(cmd))
    assert ran == []  # nothing ran on import or on choosing a model
    model_module.install_dinov3()
    assert ran == [model_module.dinov3_install_command()]

    def failing(cmd, check):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(model_module.subprocess, "run", failing)
    with pytest.raises(SegmentationError, match="could not be installed"):
        model_module.install_dinov3()


# channels by ID, channel_axis=0 (audit: channels) -------------------------------------------


def test_channel_axis_is_0_and_the_stack_follows_the_requested_ids_on_a_mixed_run(run, stub):
    job = make_job(run, ids=("TUB", "DNA"))
    result = cp.run_batch(job, batch_id="b1")
    fovs = run.inputs.fovs()
    assert result.counts() == {"written": len(fovs), "skipped": 0, "failed": 0}
    assert {f.repeat_group for f in fovs} >= {REPEAT_2}  # the repeat that stores EXTRA first
    assert len(stub.calls) == len(fovs)
    for call in stub.calls:
        assert call.kwargs["channel_axis"] == 0
        assert call.x.shape == (2, 8, 8)
        tub, dna = call.x
        assert LEVELS["TUB"] <= tub.min() and tub.max() < LEVELS["TUB"] + 20
        assert LEVELS["DNA"] <= dna.min() and dna.max() < LEVELS["DNA"] + 20


def test_three_channels_come_in_the_order_given(run, stub):
    cp.run_batch(make_job(run, ids=("DNA", "ACTIN", "TUB")), batch_id="b1")
    assert len(stub.calls) == len(run.inputs.fovs())
    for call in stub.calls:
        assert call.x.shape == (3, 8, 8)
        for plane, stain in zip(call.x, ("DNA", "ACTIN", "TUB"), strict=True):
            assert LEVELS[stain] <= plane.min() and plane.max() < LEVELS[stain] + 20


def test_a_channel_some_images_lack_is_refused_with_the_resolvers_message(run, stub):
    with pytest.raises(Exception) as caught:
        resolve_segmentation(run.inputs, ("DNA", "EXTRA"))
    assert getattr(caught.value, "code", None) == "A_SCOPE_INCOMPLETE"
    assert stub.calls == []


def test_more_than_three_channels_are_refused_because_cellpose_drops_the_rest(stub):
    cp.check_channel_count(("a", "b", "c"))
    with pytest.raises(SegmentationError, match="at most 3 channels"):
        cp.check_channel_count(("a", "b", "c", "d"))
    loaded = cp.load_model("cpsam_v2", "cpu")
    with pytest.raises(SegmentationError, match="at most 3 channels"):
        cp.segment_image(loaded, cp.Settings(), np.zeros((4, 8, 8), np.uint8))
    assert stub.calls == []
    with pytest.raises(SegmentationError, match="Cellpose gets"):
        cp.segment_image(loaded, cp.Settings(), np.zeros((8, 8), np.uint8))


def test_coverage_names_how_many_images_have_each_channel(run):
    coverage = cp.channel_coverage(run.inputs)
    fovs = run.inputs.fovs()
    stained = sum(f.repeat_group == REPEAT_2 for f in fovs)
    assert list(coverage) == ["DNA", "ACTIN", "TUB", "EXTRA"]  # the order first met
    assert coverage["EXTRA"] == (stained, len(fovs)) and coverage["DNA"] == (len(fovs),) * 2


# the diameter and the other settings (audit B7 effective settings) ---------------------------


def test_a_blank_diameter_is_the_native_scale_and_is_recorded_as_such(run, stub):
    result = cp.run_batch(make_job(run), batch_id="b1")
    assert all(call.kwargs["diameter"] is None for call in stub.calls)
    params = result.batch.parameters
    assert params["diameter"] is None and params["rescale"] == 1.0
    assert (
        "native scale" in params["diameter_unit"] and "does not estimate" in params["diameter_unit"]
    )
    assert cp.diameter_from_text("  ") is None


def test_a_diameter_is_in_pixels_and_its_scale_is_recorded(run, stub):
    result = cp.run_batch(make_job(run, settings=cp.Settings(diameter=40)), batch_id="b1")
    assert all(call.kwargs["diameter"] == 40 for call in stub.calls)
    assert result.batch.parameters["diameter_unit"] == "pixels"
    assert result.batch.parameters["rescale"] == 0.75
    assert cp.diameter_from_text("30.5") == 30.5


@pytest.mark.parametrize("text", ["0", "-3", "nan", "inf", "abc", "0.0"])
def test_a_diameter_of_zero_or_not_a_positive_number_is_refused(text):
    with pytest.raises(SegmentationError, match="diameter"):
        cp.diameter_from_text(text)
    for value in (0, -1, float("nan"), float("inf")):
        with pytest.raises(SegmentationError, match="positive number of pixels"):
            cp.Settings(diameter=value)


@pytest.mark.parametrize(
    "changes",
    [
        {"flow_threshold": -0.1},
        {"flow_threshold": float("nan")},
        {"cellprob_threshold": float("inf")},
        {"normalize": 1},
        {"batch_size": 0},
        {"batch_size": 2.5},
    ],
)
def test_other_settings_are_checked(changes):
    with pytest.raises(SegmentationError):
        cp.Settings(**changes)


def test_every_effective_setting_is_in_the_record(run, stub):
    settings = cp.Settings(
        diameter=25, flow_threshold=0.2, cellprob_threshold=-1.5, normalize=False, batch_size=4
    )
    batch = cp.run_batch(make_job(run, settings=settings), batch_id="b1").batch
    params = read_record(run.masks, "Cells").batches[0].parameters
    assert params == batch.parameters
    assert (params["flow_threshold"], params["cellprob_threshold"]) == (0.2, -1.5)
    assert params["normalize"] == {"normalize": False, "percentile": [1.0, 99.0]}
    assert (params["batch_size"], params["bsize"], params["tile_overlap"]) == (4, 256, 0.1)
    assert (params["device_type"], params["cellpose_version"]) == ("cpu", "4.2.1.1")
    assert params["channel_axis"] == 0 and params["do_3D"] is False
    assert params["channel_ids_order"] == ["TUB", "DNA"] and batch.channel_ids == ("TUB", "DNA")
    for key in ("flow_threshold", "cellprob_threshold", "batch_size", "bsize", "normalize"):
        assert stub.calls[0].kwargs[key] == params[key]  # what ran is what was recorded


# per image: written, skipped or failed, never silently dropped -------------------------------


def test_the_final_table_has_every_image_with_written_skipped_and_failed(run, stub):
    fovs = run.inputs.fovs()
    cp.run_batch(make_job(run), fovs=fovs[:1], batch_id="first")  # one mask exists already
    stub.calls.clear()

    def fail_second(stack):
        if len(stub.calls) == 2:
            raise RuntimeError("out of memory")

    stub.fail = fail_second
    result = cp.run_batch(make_job(run), batch_id="second")
    rows = {image: (status, message) for image, status, message in result.rows()}
    assert sorted(rows) == sorted(f.relative_path for f in fovs)  # nothing left out
    assert rows[fovs[0].relative_path] == ("skipped", cp.IMAGE_SKIPPED)
    assert rows[fovs[2].relative_path] == ("failed", "RuntimeError: out of memory")
    assert result.counts() == {"written": len(fovs) - 2, "skipped": 1, "failed": 1}
    assert len(stub.calls) == len(fovs) - 1  # the skipped image was never segmented
    assert not mask_path(run.masks, "Cells", fovs[2]).exists()
    record = read_record(run.masks, "Cells")
    assert [b.batch_id for b in record.batches] == ["first", "second"]
    assert record.batches[1] == result.batch


def test_a_failed_image_does_not_stop_the_batch_and_the_next_batch_lists_it(run, stub):
    stub.fail = lambda stack: (
        (_ for _ in ()).throw(ValueError("boom")) if len(stub.calls) == 1 else None
    )
    result = cp.run_batch(make_job(run), batch_id="b1")
    assert (
        result.counts()["failed"] == 1 and result.counts()["written"] == len(run.inputs.fovs()) - 1
    )
    (left,) = images_to_segment(run.inputs, "Cells", run.masks)
    assert left.relative_path == run.inputs.fovs()[0].relative_path


def test_an_image_with_z_or_t_above_one_fails_with_the_2d_message(run, stub):
    fovs = list(run.inputs.fovs())
    deep = dataclasses.replace(fovs[0], z_count=3)
    result = cp.run_batch(make_job(run), fovs=[deep, *fovs[1:]], batch_id="b1")
    status, message = result.rows()[0][1:]
    assert status == "failed" and "2D only" in message
    assert result.counts()["written"] == len(fovs) - 1
    assert cp.non_2d_images(run.inputs) == ()


def test_overwrite_replaces_masks_and_adds_a_batch_without_erasing_the_first(run, stub):
    cp.run_batch(make_job(run), batch_id="b1")
    again = cp.run_batch(make_job(run), batch_id="b2")
    assert again.counts()["skipped"] == len(run.inputs.fovs())  # never replaced by default
    third = cp.run_batch(make_job(run, overwrite=True), batch_id="b3")
    assert third.counts()["written"] == len(run.inputs.fovs())
    assert [b.batch_id for b in read_record(run.masks, "Cells").batches] == ["b1", "b2", "b3"]


def test_progress_is_reported_before_each_image_and_at_the_end(run, stub):
    seen = []
    cp.run_batch(make_job(run), progress=lambda *args: seen.append(args), batch_id="b1")
    fovs = run.inputs.fovs()
    assert [done for done, _, _ in seen] == [*range(len(fovs)), len(fovs)]
    assert seen[0][1:] == (len(fovs), fovs[0].relative_path) and seen[-1][2] == ""


def test_an_interrupted_batch_still_writes_its_record(run, stub):
    def interrupt(stack):
        if len(stub.calls) == 2:
            raise KeyboardInterrupt

    stub.fail = interrupt
    with pytest.raises(KeyboardInterrupt):
        cp.run_batch(make_job(run), batch_id="b1")
    (batch,) = read_record(run.masks, "Cells").batches
    assert [i.status for i in batch.images] == ["written"]


def test_a_mask_type_or_masks_folder_the_hand_off_refuses_is_refused_before_anything(run, stub):
    job = dataclasses.replace(make_job(run), mask_type="NA")
    with pytest.raises(SegmentationError):
        cp.run_batch(job)
    inside = dataclasses.replace(make_job(run), masks_root=run.experiment / "masks")
    with pytest.raises(SegmentationError, match="masks_inside_experiment|inside"):
        cp.run_batch(inside)
    assert stub.calls == [] and not (run.experiment / "masks").exists()


# round trip: preprocessing reads the masks of the batch --------------------------------------


def test_masks_of_the_batch_pass_preprocessings_own_mask_checks_on_a_rerun(run, stub):
    result = cp.run_batch(make_job(run), batch_id="b1")
    fovs = run.inputs.fovs()
    assert result.counts()["written"] == len(fovs)
    (run.out / "morphobricks.toml").write_bytes(run.data + MASKS_SECTION)
    again = run_described(run.experiment, MAPPING, output_folder=run.out, masks_folder=run.masks)
    assert again.written, [(i.code, i.message) for i in again.issues]
    assert sorted(i.code for i in again.issues) == []
    reopened = RunInputs.open(check_masks(run.out))
    assert reopened.mask_types() == ("Cells",)
    mask = reopened.read_mask(fovs[0].relative_path, "Cells")
    assert mask.dtype == np.int32 and mask.shape == (8, 8) and mask.max() == 2
    assert images_to_segment(reopened, "Cells", run.masks) == ()
    record = json.loads((run.masks / "Cells" / "segmentation_record.json").read_text("utf-8"))
    assert record["batches"][0]["parameters"]["channel_axis"] == 0


# what the preview shows, and the next steps ---------------------------------------------------


def test_the_display_contrast_is_per_channel_and_never_touches_the_stack(run):
    fov = run.inputs.fovs()[0]
    stack = resolve_segmentation(run.inputs, ("TUB", "DNA")).read(fov)
    before = stack.copy()
    shown = cp.display_contrast(stack)
    np.testing.assert_array_equal(stack, before)
    assert shown.dtype == np.float32 and shown.shape == stack.shape
    assert shown.min() >= 0 and shown.max() <= 1
    assert shown[0].max() == 1.0 and shown[1].max() == 1.0  # each channel on its own range
    assert cp.display_contrast(np.full((1, 4, 4), 7)).max() == 0  # no spread shows as 0


def test_label_outlines_mark_the_boundary_of_every_label():
    labels = np.zeros((6, 6), np.uint16)
    labels[1:5, 1:5] = 3
    edge = cp.label_outlines(labels)
    assert edge.sum() > 0 and not edge[0].any()
    assert not edge[labels == 0].any()  # only inside a label


def test_next_steps_tell_how_to_declare_the_mask_type(tmp_path):
    text = "\n".join(cp.next_steps_lines("Cells", tmp_path / "masks"))
    assert "[masks.Cells]" in text and 'representation = "instances"' in text
    assert str(tmp_path / "masks") in text and "MorphoFeature" in text
    with pytest.raises(SegmentationError):
        cp.next_steps_lines("NA", tmp_path)


def test_open_run_refuses_a_blank_folder_and_opens_a_run(run):
    with pytest.raises(SegmentationError, match="output folder"):
        cp.open_run("  ")
    assert cp.open_run(str(run.out)).fovs() == run.inputs.fovs()
