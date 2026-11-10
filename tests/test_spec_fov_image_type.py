"""PP-1: each position's declared image type and the partial-mask run helper (Stage 2).

- ``Fov.image_type`` is the unit's declaration, ``rgb`` or ``channels`` (experiment-v1 §4.4b),
  from the published description only, through :class:`.fov_identity.FovIdentity` (the one
  source; the resolver's ``FovSelection.image_type`` is the same value). ``v28`` is ``rgb``
  on every position, ``v13`` and ``v01`` are ``channels``. A file holding an RGB and a channels
  unit (``v29``) is valid but refused by the Stage 1-2 runtime
  (``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED``), so the two types are shown in one description
  through the identity and in one run each through ``RunInputs``.
- There is no run-wide image type on ``RunInputs`` any more.
- ``make_described_run(mask_images=...)`` writes masks for a declared subset of the images:
  ``v09`` with ``Nuclei`` masks on 2 of 6 resolves to "present in 2 of 6" and ``rep1``
  partial, exactly as the tree PP-0's test built by hand.
"""

from __future__ import annotations

import warnings

import pytest

from morphobricks_preprocessing import MaskRequest, Request, RunInputs, run_experiment
from morphobricks_preprocessing.fov_identity import fov_identity
from morphobricks_preprocessing.run.mask_step import describe_masks, prepare_mask_step
from morphobricks_preprocessing.testing import described_images, make_described_run
from morphobricks_preprocessing.testing_described import (
    PUBLISHED_NAME,
    _mapping,
    description_of,
    write_described_tree,
)
from spec_pin import fixtures

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}


def _small(name):
    """Return a fixture's bytes with 3 images per member instead of 500 (``v09``)."""
    return VALID[name].data.replace(b"image_count = 500", b"image_count = 3")


def _open(tmp_path, data, **kwargs):
    run = make_described_run(tmp_path / "run", data, **kwargs)
    return RunInputs.open(run.output)


# --- Fov.image_type -------------------------------------------------------------------------


def test_v28_every_fov_is_rgb(tmp_path):
    fovs = _open(tmp_path, VALID["v28"].data).fovs()
    assert len(fovs) == 32
    assert {f.image_type for f in fovs} == {"rgb"}
    assert {f.channel_ids for f in fovs} == {("R", "G", "B")}


@pytest.mark.parametrize("name", ["v13", "v01"])
def test_a_unit_without_a_declared_type_is_channels_on_every_fov(tmp_path, name):
    fovs = _open(tmp_path, VALID[name].data).fovs()
    assert fovs and {f.image_type for f in fovs} == {"channels"}


def test_an_rgb_and_a_channels_unit_keep_their_own_types(tmp_path):
    # v29 (one repeat, an RGB unit beside a two-channel unit) is valid; its identities give
    # each unit its own declaration.
    description, _ = description_of(VALID["v29"].data)
    types = {
        unit_id: fov_identity(
            description,
            (
                description.conditions[condition].source_folder,
                member.source_repeat,
                unit.source_folder,
            ),
        ).image_type
        for entry in description.repeats.values()
        for condition, member in entry.members.items()
        for unit_id, unit in member.technical_repeats.items()
    }
    assert sorted(types.values()) == ["channels", "rgb"]
    # As runs: the runtime refuses the mixed layout, so one run of each type.
    rgb = _open(tmp_path / "rgb", VALID["v28"].data).fovs()
    channels = _open(tmp_path / "channels", VALID["v01"].data).fovs()
    assert {f.image_type for f in rgb} == {"rgb"}
    assert {f.image_type for f in channels} == {"channels"}


def test_run_inputs_has_no_run_wide_image_type(tmp_path):
    inputs = _open(tmp_path, VALID["v28"].data)
    assert not hasattr(inputs, "image_type")
    # The run-level record of the command line's review stays a manifest field only.
    assert inputs.manifest.image_type is None


# --- make_described_run(mask_images=...) ----------------------------------------------------


def test_described_images_are_the_images_the_helper_writes(tmp_path):
    data = _small("v09")
    images = described_images(description_of(data)[0])
    run = make_described_run(tmp_path / "run", data)
    assert images == tuple(f.relative_path for f in RunInputs.open(run.output).fovs())
    assert len(images) == 6


def test_v09_masks_on_2_of_6_resolve_to_partial_coverage(tmp_path):
    data = _small("v09")
    images = described_images(description_of(data)[0])
    run = make_described_run(tmp_path / "run", data, masks=("Nuclei",), mask_images=images[:2])
    assert sorted(
        p.relative_to(run.masks / "nuclei").as_posix() for p in run.masks.rglob("*.tif")
    ) == list(images[:2])
    resolved = RunInputs.open(run.output).resolve_request(
        Request(("DNA",), (MaskRequest("Nuclei"),))
    )
    assert [(r.code, r.subject) for r in resolved.refusals] == [("A_SCOPE_INCOMPLETE", "Nuclei")]
    assert "present in 2 of 6 acquisitions" in resolved.refusals[0].message
    assert resolved.refusals[0].files == images[2:]
    found = [(c.subject, c.repeat_group, c.status, c.present, c.total) for c in resolved.coverage]
    assert found == [("DNA", "rep1", "full", 6, 6), ("Nuclei", "rep1", "partial", 2, 6)]


def _hand_built(tmp_path, data, keep):
    """PP-0's tree: every mask written, then all but the first ``keep`` deleted."""
    description, raw = description_of(data)
    root = tmp_path / "hand"
    experiment, output, masks = root / "experiment", root / "output", root / "masks"
    images = write_described_tree(experiment, masks, description, ("Nuclei",))
    for rel in images[keep:]:
        (masks / "nuclei" / rel).unlink()
    output.mkdir(parents=True)
    (output / PUBLISHED_NAME).write_bytes(raw)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = run_experiment(
            experiment, _mapping(description), output_folder=output, masks_folder=masks
        )
    assert result.written
    assert describe_masks(prepare_mask_step(None, output)).issues == ()
    return RunInputs.open(output)


def test_the_helpers_v09_run_equals_the_hand_built_one(tmp_path):
    data = _small("v09")
    images = described_images(description_of(data)[0])
    hand = _hand_built(tmp_path, data, keep=2)
    helper = _open(tmp_path, data, masks=("Nuclei",), mask_images=images[:2])
    request = Request(("DNA",), (MaskRequest("Nuclei"),))
    assert helper.resolve_request(request) == hand.resolve_request(request)
    assert helper.fovs() == hand.fovs()
    pairs = [
        sorted((m["image"], m["type"], m["path"]) for m in inputs.manifest.masks["files"])
        for inputs in (helper, hand)
    ]
    assert pairs[0] == pairs[1] and len(pairs[0]) == 2


def test_a_declared_mask_without_files_is_present_in_0_of_6(tmp_path):
    # Masks on none of the images: the run without masks (mask_images=() is refused below).
    inputs = _open(tmp_path, _small("v09"))
    resolved = inputs.resolve_request(Request(("DNA",), (MaskRequest("Nuclei"),)))
    assert "present in 0 of 6 acquisitions" in resolved.refusals[0].message
    assert {c.status for c in resolved.coverage if c.subject == "Nuclei"} == {"unavailable"}


@pytest.mark.parametrize(
    ("masks", "mask_images", "match"),
    [
        (("Nuclei",), ("CTRL/rep1/img9.tif",), "not images of the described tree"),
        (("Nuclei",), "CTRL/rep1/img0.tif", "a collection of relative paths"),
        ((), ("CTRL/rep1/img0.tif",), "needs at least one mask ID"),
        (("Nuclei",), (), "names no image"),
        (("Cells",), None, "not declared by the description"),
    ],
)
def test_a_subset_outside_the_tree_is_refused_before_anything_is_written(
    tmp_path, masks, mask_images, match
):
    with pytest.raises(ValueError, match=match):
        make_described_run(tmp_path / "run", _small("v09"), masks=masks, mask_images=mask_images)
    assert not (tmp_path / "run").exists() or not any((tmp_path / "run").rglob("*.tif"))
