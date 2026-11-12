"""``RunInputs.resolve_request``: the shared request resolver on the Spec fixtures (contract v1).

The architecture decision of 10 November 2026: one experiment interpreter in preprocessing for
every route. Shown here on the vendored fixtures through ``make_described_run``:

- ``v08`` (P27): ``TumorRegion`` as objects is ``A_MASK_NOT_INSTANCES`` unless the request
  records ``binary_mask_policy``; as a region it is accepted; the conversion is returned, never
  applied here. ``A_MASK_UNCHECKED`` before the mask step (P28, "k of n files").
- ``v09`` (P11, GS2): masks on 2 of 6 images are ``A_SCOPE_INCOMPLETE`` "present in 2 of 6"
  with ``rep1`` partial; ``v01``: a channel no image has is "present in 0 of n" and every repeat
  unavailable (SI1).
- ``v13``: 90 positions resolved by channel ID per image; a reordered channel list on one
  position changes that position's indices only.
- ``v28`` (P24): ``rgb`` units are ``A_RGB_NOT_SUPPORTED`` unless the capability is declared.
- A volume is refused unless ``volume_support`` is declared; a one-slice volume reads what a
  plane reads.
- The result is immutable and picklable; the provenance is the manifest's record; the resolver
  writes nothing and imports no brick, CellPatcher or provider module, not even indirectly.
- Every run the resolver sees is described: a run without ``morphobricks.toml`` is refused
  with ``A_DESCRIPTION_REQUIRED`` before a ``RunInputs`` exists (ADR 0002 AR10).
"""

from __future__ import annotations

import ast
import dataclasses
import json
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

import morphobricks_preprocessing.run_inputs_request as request_module
import morphobricks_preprocessing.run_inputs_resolve as resolve_module
from morphobricks_preprocessing import (
    RESOLVER_VERSION,
    Capabilities,
    ChannelNotFoundError,
    DescriptionRequiredError,
    MaskNotInstancesError,
    MaskRequest,
    MaskUncheckedError,
    Request,
    RgbNotSupportedError,
    RunInputs,
    RunInputsError,
    VolumeNotSupportedError,
)
from morphobricks_preprocessing.manifest import read_manifest
from morphobricks_preprocessing.run.mask_step import describe_masks, prepare_mask_step
from morphobricks_preprocessing.run_inputs_request import (
    PlaneSelection,
    plane_selections,
)
from morphobricks_preprocessing.testing import described_images, make_described_run
from morphobricks_preprocessing.testing_described import PUBLISHED_NAME, description_of
from spec_pin import fixtures

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}
POLICY = "connected_components"
FORBIDDEN_IMPORTS = (
    "cellpatcher",
    "morphofeature",
    "morphoobjects",
    "morphorelate",
    "fiberfeatures",
    "morphoembed",
    "morphocoverage",
    "torch",
    "skimage",
    "scipy",
)


def _open(tmp_path, name, masks=(), mask_step=True):
    run = make_described_run(tmp_path / "run", VALID[name].data, masks=masks, mask_step=mask_step)
    return RunInputs.open(run.output), run


def _partial_mask_run(tmp_path, name, mask_id, keep):
    """A described run whose ``mask_id`` masks exist for the first ``keep`` images only (PP-1)."""
    data = VALID[name].data.replace(b"image_count = 500", b"image_count = 3")
    images = described_images(description_of(data)[0])
    run = make_described_run(tmp_path / "run", data, masks=(mask_id,), mask_images=images[:keep])
    return RunInputs.open(run.output), list(images)


def _codes(resolved):
    return [(r.code, r.subject) for r in resolved.refusals]


# --- v08: masks (P27, P28) ---------------------------------------------------------------


def test_v08_instances_mask_as_objects_resolves_every_position(tmp_path):
    inputs, _ = _open(tmp_path, "v08", masks=("Nuclei", "TumorRegion"))
    resolved = inputs.resolve_request(Request(("DNA", "ACTIN"), (MaskRequest("Nuclei"),)))
    assert not resolved.refused and len(resolved.selections) == 4
    assert resolved.require() == resolved.selections
    for selection in resolved.selections:
        assert selection.channel_ids == ("DNA", "ACTIN")
        assert selection.channel_indices == (0, 1)
        assert selection.index_of("ACTIN") == 1
        assert selection.repeat_group == "rep1" and selection.condition_id == "CTRL"
        assert selection.technical_repeat_id == "single" and selection.image_type == "channels"
        (mask,) = selection.masks
        assert mask.mask_id == "Nuclei" and mask.source_type == "nuclei"
        assert mask.representation == "instances" and mask.check_status == "checked"
        assert mask.conversion is None and mask.role == "objects"
        assert selection.planes == (PlaneSelection(0, (0,)),)
    assert [(c.subject, c.status, c.present, c.total) for c in resolved.coverage] == [
        ("DNA", "full", 4, 4),
        ("ACTIN", "full", 4, 4),
        ("Nuclei", "full", 4, 4),
    ]


def test_v08_p27a_binary_mask_as_objects_without_a_policy_is_refused(tmp_path):
    inputs, _ = _open(tmp_path, "v08", masks=("Nuclei", "TumorRegion"))
    resolved = inputs.resolve_request(Request(("DNA",), (MaskRequest("TumorRegion"),)))
    assert _codes(resolved) == [("A_MASK_NOT_INSTANCES", "TumorRegion")]
    assert resolved.selections == ()  # a refused request takes no subset
    assert "binary_mask_policy" in resolved.refusals[0].message
    with pytest.raises(MaskNotInstancesError, match="A_MASK_NOT_INSTANCES") as raised:
        resolved.require()
    assert raised.value.code == "A_MASK_NOT_INSTANCES"


def test_v08_p27b_the_recorded_policy_is_returned_for_the_consumer_to_apply(tmp_path):
    inputs, _ = _open(tmp_path, "v08", masks=("Nuclei", "TumorRegion"))
    request = Request(("DNA",), (MaskRequest("TumorRegion", binary_mask_policy=POLICY),))
    resolved = inputs.resolve_request(request)
    assert not resolved.refused and len(resolved.selections) == 4
    for selection in resolved.selections:
        (mask,) = selection.masks
        assert mask.conversion == POLICY and mask.representation == "binary"
        assert mask.source_type == "tumor_region"
    # The resolver converts nothing: the mask is still read as stored (0/1 region).
    assert set(inputs.read_mask(inputs.fovs()[0], "tumor_region").ravel()) <= {0, 1}
    # P27c: a later request without the policy is refused again.
    again = inputs.resolve_request(Request(("DNA",), (MaskRequest("TumorRegion"),)))
    assert _codes(again) == [("A_MASK_NOT_INSTANCES", "TumorRegion")]


def test_v08_binary_mask_as_a_region_is_accepted(tmp_path):
    inputs, _ = _open(tmp_path, "v08", masks=("Nuclei", "TumorRegion"))
    resolved = inputs.resolve_request(Request((), (MaskRequest("TumorRegion", role="region"),)))
    assert not resolved.refused and len(resolved.selections) == 4
    assert resolved.selections[0].masks[0].conversion is None
    assert resolved.selections[0].channel_indices == ()


def test_v08_p28_unchecked_masks_are_refused_with_k_of_n_files(tmp_path):
    inputs, _ = _open(tmp_path, "v08", masks=("Nuclei", "TumorRegion"), mask_step=False)
    request = Request(("DNA",), (MaskRequest("Nuclei"), MaskRequest("TumorRegion", role="region")))
    resolved = inputs.resolve_request(request)
    assert _codes(resolved) == [("A_MASK_UNCHECKED", "Nuclei"), ("A_MASK_UNCHECKED", "TumorRegion")]
    assert "4 of 4 files" in resolved.refusals[0].message
    assert len(resolved.refusals[0].files) == 4
    with pytest.raises(MaskUncheckedError, match="A_MASK_UNCHECKED"):
        resolved.require()
    assert describe_masks(prepare_mask_step(None, inputs.output_folder)).issues == ()
    assert not RunInputs.open(inputs.output_folder).resolve_request(request).refused


def test_v08_p28_a_mask_checked_for_some_files_is_unchecked_for_k_of_n(tmp_path):
    inputs, _ = _open(tmp_path, "v08", masks=("Nuclei",))
    status = inputs.output_folder / "mask_representation.json"
    document = json.loads(status.read_text(encoding="utf-8"))
    dropped = document["masks"]["Nuclei"]["files"].pop(0)["path"]
    status.write_text(json.dumps(document), encoding="utf-8")
    resolved = inputs.resolve_request(Request(("DNA",), (MaskRequest("Nuclei"),)))
    assert _codes(resolved) == [("A_MASK_UNCHECKED", "Nuclei")]
    assert "for 1 of 4 files" in resolved.refusals[0].message
    assert resolved.refusals[0].files == (dropped,)
    assert resolved.selections == ()
    assert [(c.status, c.present, c.total) for c in resolved.coverage if c.subject == "Nuclei"] == [
        ("full", 4, 4)
    ]  # coverage is presence; the check status is the refusal's


def test_v08_every_applicable_refusal_is_reported_together(tmp_path):
    inputs, _ = _open(tmp_path, "v08", masks=("TumorRegion",), mask_step=False)
    request = Request(("PHASE",), (MaskRequest("TumorRegion"), MaskRequest("Nuclei")))
    resolved = inputs.resolve_request(request)
    assert _codes(resolved) == [
        ("A_SCOPE_INCOMPLETE", "PHASE"),
        ("A_MASK_UNCHECKED", "TumorRegion"),
        ("A_MASK_NOT_INSTANCES", "TumorRegion"),
        ("A_SCOPE_INCOMPLETE", "Nuclei"),  # declared, but no file recorded: 0 of 4
    ]
    with pytest.raises(ChannelNotFoundError) as raised:
        resolved.require()
    assert raised.value.code == "A_SCOPE_INCOMPLETE"
    assert "A_MASK_UNCHECKED" in str(raised.value)  # the text lists every refusal


# --- scope and coverage (SI1, GS2) -------------------------------------------------------


def test_v01_a_channel_no_image_has_is_present_in_0_of_n_and_every_repeat_unavailable(tmp_path):
    inputs, _ = _open(tmp_path, "v01")
    fovs = inputs.fovs()
    resolved = inputs.resolve_request(Request(("DNA", "PHASE")))
    assert _codes(resolved) == [("A_SCOPE_INCOMPLETE", "PHASE")]
    assert f"present in 0 of {len(fovs)} acquisitions" in resolved.refusals[0].message
    assert set(resolved.refusals[0].files) == {f.relative_path for f in fovs}
    by_subject = {}
    for item in resolved.coverage:
        by_subject.setdefault(item.subject, set()).add(item.status)
    assert by_subject == {"DNA": {"full"}, "PHASE": {"unavailable"}}
    assert {c.repeat_group for c in resolved.coverage} == {f.repeat_group for f in fovs}


def test_an_undeclared_mask_id_is_present_in_0_of_n(tmp_path):
    inputs, _ = _open(tmp_path, "v01")
    resolved = inputs.resolve_request(Request((), (MaskRequest("Nuclei", role="region"),)))
    assert _codes(resolved) == [("A_SCOPE_INCOMPLETE", "Nuclei")]
    assert "present in 0 of" in resolved.refusals[0].message
    assert {c.status for c in resolved.coverage} == {"unavailable"}


def test_v09_partial_masks_are_present_in_k_of_n_and_the_repeat_is_partial(tmp_path):
    inputs, images = _partial_mask_run(tmp_path, "v09", "Nuclei", keep=2)
    assert len(images) == 6
    resolved = inputs.resolve_request(Request(("DNA",), (MaskRequest("Nuclei"),)))
    assert _codes(resolved) == [("A_SCOPE_INCOMPLETE", "Nuclei")]
    refusal = resolved.refusals[0]
    assert "present in 2 of 6 acquisitions" in refusal.message
    assert refusal.files == tuple(images[2:])
    found = [(c.subject, c.repeat_group, c.status, c.present, c.total) for c in resolved.coverage]
    assert found == [("DNA", "rep1", "full", 6, 6), ("Nuclei", "rep1", "partial", 2, 6)]
    assert resolved.selections == ()
    # The same run without the mask resolves in full: the refusal is the mask's, not the run's.
    assert len(inputs.resolve_request(Request(("DNA",))).selections) == 6


# --- v13: channels by ID per image ----------------------------------------------------------


def test_v13_resolves_every_position_by_channel_id(tmp_path):
    inputs, _ = _open(tmp_path, "v13")
    resolved = inputs.resolve_request(Request(("ACTIN", "DNA")))
    assert not resolved.refused and len(resolved.selections) == 90
    fovs = {f.relative_path: f for f in inputs.fovs()}
    for selection in resolved.selections:
        fov = fovs[selection.relative_path]
        assert selection.key == fov.key
        assert selection.channel_indices == tuple(
            inputs.channel_index(fov, c) for c in selection.channel_ids
        )
        assert selection.channel_indices == (1, 0)  # the request's order, not the image's
    assert [c.repeat_group for c in resolved.coverage if c.subject == "DNA"] == [
        "rep1",
        "rep2",
        "rep3",
    ]
    assert {c.status for c in resolved.coverage} == {"full"}


def test_v13_a_reordered_channel_list_changes_only_that_positions_indices(tmp_path, monkeypatch):
    # Preprocessing processes one channel layout per experiment today (a unit declaring another
    # order is refused before the run is written), so the resolver's per-image rule is shown on
    # the run's own positions with one position's channel order reversed.
    inputs, _ = _open(tmp_path, "v13")
    original = inputs.fovs()
    swapped = original[7]
    reordered = dataclasses.replace(swapped, channel_ids=tuple(reversed(swapped.channel_ids)))
    changed = tuple(reordered if f is swapped else f for f in original)
    monkeypatch.setattr(RunInputs, "fovs", lambda self: changed)
    resolved = inputs.resolve_request(Request(("DNA", "ACTIN")))
    by_path = {s.relative_path: s for s in resolved.selections}
    assert by_path[swapped.relative_path].channel_indices == (1, 0)
    others = {s.channel_indices for p, s in by_path.items() if p != swapped.relative_path}
    assert others == {(0, 1)}


# --- v28: RGB (P24) -------------------------------------------------------------------------


def test_v28_rgb_units_are_refused_unless_the_consumer_declares_rgb_support(tmp_path):
    inputs, _ = _open(tmp_path, "v28")
    resolved = inputs.resolve_request(Request(("R", "G", "B")))
    assert _codes(resolved) == [("A_RGB_NOT_SUPPORTED", "rgb")]
    assert len(resolved.refusals[0].files) == len(inputs.fovs()) == 32
    with pytest.raises(RgbNotSupportedError, match="A_RGB_NOT_SUPPORTED"):
        resolved.require()
    accepted = inputs.resolve_request(
        Request(("R", "G", "B"), capabilities=Capabilities(rgb_support=True))
    )
    assert not accepted.refused and len(accepted.selections) == 32
    assert {s.image_type for s in accepted.selections} == {"rgb"}
    assert accepted.selections[0].channel_indices == (0, 1, 2)


def test_v01_channel_units_are_channels_and_need_no_capability(tmp_path):
    inputs, _ = _open(tmp_path, "v01")
    resolved = inputs.resolve_request(Request(("DNA",)))
    assert not resolved.refused and {s.image_type for s in resolved.selections} == {"channels"}


# --- planes and volumes -----------------------------------------------------------------------


def test_a_volume_is_refused_by_default_and_accepted_with_volume_support(tmp_path):
    inputs, _ = _open(tmp_path, "v01")
    refused = inputs.resolve_request(Request(("DNA",), planes="volume"))
    assert _codes(refused) == [("A_VOLUME_NOT_SUPPORTED", "volume")]
    with pytest.raises(VolumeNotSupportedError, match="A_VOLUME_NOT_SUPPORTED") as raised:
        refused.require()
    assert raised.value.code == "A_VOLUME_NOT_SUPPORTED"
    assert isinstance(raised.value, RunInputsError)
    accepted = inputs.resolve_request(
        Request(("DNA",), planes="volume", capabilities=Capabilities(volume_support=True))
    )
    plane = inputs.resolve_request(Request(("DNA",)))
    # A one-slice volume reads what the plane selection reads (today's runs are 2-D).
    assert [s.planes for s in accepted.selections] == [s.planes for s in plane.selections]
    assert accepted.selections[0].planes == (PlaneSelection(0, (0,)),)


def test_plane_selections_enumerate_planes_or_one_volume_per_time_point():
    assert plane_selections(2, 3, "plane") == (
        PlaneSelection(0, (0,)),
        PlaneSelection(0, (1,)),
        PlaneSelection(0, (2,)),
        PlaneSelection(1, (0,)),
        PlaneSelection(1, (1,)),
        PlaneSelection(1, (2,)),
    )
    assert plane_selections(2, 3, "volume") == (
        PlaneSelection(0, (0, 1, 2)),
        PlaneSelection(1, (0, 1, 2)),
    )
    assert plane_selections(1, 1, "volume") == plane_selections(1, 1, "plane")


# --- malformed requests -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("request_", "match"),
    [
        (Request(("DNA", "DNA")), "channel ID is requested twice"),
        (Request((), (MaskRequest("Nuclei"), MaskRequest("Nuclei"))), "mask ID is requested twice"),
        (Request(("DNA",), planes="stack"), "planes must be one of"),
        (Request(("DNA",), device="gpu"), "device must be one of"),
        (Request(("DNA",), device="CUDA"), "device must be one of"),
        (Request(("DNA",), device="cuda:0"), "device must be one of"),
        (Request(["DNA"]), "channel_ids must be a tuple of str"),
        (Request("DNA"), "channel_ids must be a tuple of str"),
        (Request((), [MaskRequest("Nuclei")]), "masks must be a tuple of MaskRequest"),
        (Request((), ("Nuclei",)), "masks must be a tuple of MaskRequest"),
        (Request(("DNA",), capabilities=Capabilities(rgb_support=1)), "capabilities must be"),
        (Request(("DNA",), capabilities=Capabilities(volume_support="no")), "capabilities must be"),
        (Request((), (MaskRequest("Nuclei", role="cells"),)), "mask role must be one of"),
        (
            Request((), (MaskRequest("TumorRegion", binary_mask_policy="threshold"),)),
            "binary_mask_policy must be one of",
        ),
        (
            Request((), (MaskRequest("Nuclei", binary_mask_policy=POLICY),)),
            "applies to a binary mask used as objects",
        ),
        (
            Request((), (MaskRequest("TumorRegion", role="region", binary_mask_policy=POLICY),)),
            "applies to a binary mask used as objects",
        ),
    ],
)
def test_a_malformed_request_is_a_value_error_not_a_refusal(tmp_path, request_, match):
    inputs, _ = _open(tmp_path, "v08", masks=("Nuclei", "TumorRegion"))
    with pytest.raises(ValueError, match=match):
        inputs.resolve_request(request_)


# --- provenance, immutability, no writes -----------------------------------------------------


def test_provenance_is_the_manifests_record_and_the_request_with_its_device(tmp_path):
    inputs, run = _open(tmp_path, "v01")
    request = Request(("DNA",), device="cuda")
    resolved = inputs.resolve_request(request)
    recorded = read_manifest(run.output / "run_manifest.json").experiment_description
    assert resolved.provenance.resolver_version == RESOLVER_VERSION == 2
    assert resolved.provenance.description_sha256 == recorded["sha256"]
    assert dict(resolved.provenance.section_digests) == recorded["section_digests"]
    assert resolved.provenance.request == request and resolved.provenance.request.device == "cuda"
    assert inputs.resolve_request(Request(("DNA",))).provenance.request.device == "cpu"


def test_a_description_changed_since_the_run_is_refused_not_resolved(tmp_path):
    inputs, run = _open(tmp_path, "v01")
    published = run.output / PUBLISHED_NAME
    published.write_bytes(published.read_bytes() + b"# edited after the run\n")
    with pytest.raises(RunInputsError, match="not the one the run read"):
        inputs.resolve_request(Request(("DNA",)))


def test_the_result_is_immutable_and_picklable(tmp_path):
    inputs, _ = _open(tmp_path, "v08", masks=("Nuclei",))
    resolved = inputs.resolve_request(Request(("DNA",), (MaskRequest("Nuclei"),)))
    with pytest.raises(dataclasses.FrozenInstanceError):
        resolved.selections = ()
    with pytest.raises(dataclasses.FrozenInstanceError):
        resolved.selections[0].channel_indices = (1,)
    with pytest.raises(dataclasses.FrozenInstanceError):
        resolved.provenance.request.channel_ids = ()
    assert pickle.loads(pickle.dumps(resolved)) == resolved
    refused = inputs.resolve_request(Request(("PHASE",)))
    assert pickle.loads(pickle.dumps(refused)) == refused
    with pytest.raises(KeyError):
        resolved.selections[0].index_of("ACTIN")


def test_resolving_writes_nothing(tmp_path):
    inputs, run = _open(tmp_path, "v08", masks=("Nuclei", "TumorRegion"))
    before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in run.output.rglob("*")}
    converted = MaskRequest("TumorRegion", binary_mask_policy=POLICY)
    inputs.resolve_request(Request(("DNA",), (converted,)))
    inputs.resolve_request(Request(("PHASE",), (MaskRequest("TumorRegion"),)))
    assert {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in run.output.rglob("*")} == before
    assert RunInputs.open(run.output).fovs() == inputs.fovs()


# --- every run the resolver sees is described (AR10) ----------------------------------------


def test_a_run_without_a_published_description_never_reaches_the_resolver(tmp_path):
    inputs, run = _open(tmp_path, "v01")
    (run.output / PUBLISHED_NAME).unlink()
    with pytest.raises(DescriptionRequiredError, match="A_DESCRIPTION_REQUIRED") as raised:
        RunInputs.open(run.output)
    assert raised.value.code == "A_DESCRIPTION_REQUIRED"


# --- the resolver imports no brick, CellPatcher or provider module -------------------------


@pytest.mark.parametrize("module", [request_module, resolve_module])
def test_the_resolver_modules_import_no_brick_cellpatcher_or_provider_module(module):
    source = Path(module.__file__).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif (
            isinstance(node, ast.ImportFrom) and node.level == 0
        ):  # relative ones are the package's
            imported.add(node.module or "")
    allowed = ("__future__", "collections", "dataclasses", "pathlib", "typing")
    for name in imported:
        assert not name.startswith(FORBIDDEN_IMPORTS), name
        assert name.startswith(allowed), name


def test_importing_the_resolver_loads_no_brick_cellpatcher_or_provider_module():
    # A fresh interpreter: the package's own relative imports must not pull any in either.
    code = (
        "import sys, morphobricks_preprocessing.run_inputs_resolve, "
        "morphobricks_preprocessing.run_inputs_request; "
        "print(sorted({n.split('.')[0] for n in sys.modules}))"
    )
    found = subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True, text=True
    ).stdout
    loaded = set(ast.literal_eval(found))
    # scipy is preprocessing's own dependency (the package loads it); everything else is barred.
    barred = set(FORBIDDEN_IMPORTS) - {"scipy"}
    assert not loaded & barred, loaded & barred


def test_the_resolver_runs_no_kernel_and_reads_no_pixel(tmp_path, monkeypatch):
    inputs, _ = _open(tmp_path, "v08", masks=("Nuclei", "TumorRegion"))
    import tifffile

    def fail(*args, **kwargs):
        raise AssertionError("the resolver read pixels")

    for cls in (tifffile.TiffFile, tifffile.TiffPage, tifffile.TiffFrame, tifffile.TiffPageSeries):
        monkeypatch.setattr(cls, "asarray", fail)
    monkeypatch.setattr(RunInputs, "read_plane", fail)
    monkeypatch.setattr(RunInputs, "read_mask", fail)
    resolved = inputs.resolve_request(
        Request(("DNA",), (MaskRequest("TumorRegion", binary_mask_policy=POLICY),))
    )
    assert not resolved.refused and len(resolved.selections) == 4


def test_a_fov_selection_carries_the_unchanged_key_beside_the_canonical_ids(tmp_path):
    inputs, _ = _open(tmp_path, "v33")
    resolved = inputs.resolve_request(Request(("DNA",)))
    assert not resolved.refused
    by_condition = {s.key[0]: s.condition_id for s in resolved.selections}
    assert by_condition == {"control": "ctrl", "DrugA": "DrugA"}  # the key keeps the folder
    assert all(isinstance(s, type(resolved.selections[0])) for s in resolved.selections)
    assert all(f.key == s.key for f, s in zip(inputs.fovs(), resolved.selections, strict=True))


def test_a_selection_carries_its_fovs_image_type(tmp_path):
    # PP-1: one source, the unit's declaration through FovIdentity, for Fov and FovSelection.
    inputs, _ = _open(tmp_path, "v28")
    capable = Request(("R", "G", "B"), capabilities=Capabilities(rgb_support=True))
    resolved = inputs.resolve_request(capable)
    by_path = {f.relative_path: f.image_type for f in inputs.fovs()}
    assert {s.relative_path: s.image_type for s in resolved.selections} == by_path
