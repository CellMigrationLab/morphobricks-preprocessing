"""``make_clean_run(description=...)``: a described run for the bricks' Stage 2 tests.

Stage 2 design PR 1: each Spec fixture the bricks test against (``v03 v08 v13 v28 v30 v33
v34``) builds, opens with ``RunInputs``, and ``fovs()`` gives the fixture's canonical IDs
(condition, repeat group, technical repeat ID), channel IDs and image count. The existing
behaviour of ``make_clean_run`` is unchanged (its other tests are in ``test_run_inputs.py``).
"""

from __future__ import annotations

from collections import Counter

import pytest

from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_write import canonical_bytes
from morphobricks_preprocessing.run_inputs import RunInputs
from morphobricks_preprocessing.testing import CleanRun, make_clean_run, make_described_run
from spec_pin import fixtures

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}
NAMES = ["v03", "v08", "v13", "v28", "v30", "v33", "v34"]


def _expected(description):
    """Return ``{(condition ID, repeat group, technical repeat ID, channel IDs): images}``."""
    found: Counter = Counter()
    for repeat_id, repeat in description.repeats.items():
        for condition_id, member in repeat.members.items():
            # image_count is the member's total: earlier units (sorted ID) take the remainder.
            base, extra = divmod(member.image_count, len(member.technical_repeats))
            for position, unit_id in enumerate(sorted(member.technical_repeats)):
                unit = member.technical_repeats[unit_id]
                found[(condition_id, repeat_id, unit_id, unit.channels)] += base + (
                    position < extra
                )
    return found


@pytest.mark.parametrize("name", NAMES)
def test_each_fixture_builds_opens_and_gives_its_canonical_ids(tmp_path, name):
    description = description_from_bytes(VALID[name].data)
    run = make_clean_run(tmp_path / "run", description=VALID[name].data)
    assert isinstance(run, CleanRun) and run.result.written
    assert run.description == description
    assert (run.output / "morphobricks.toml").read_bytes() == VALID[name].data
    inputs = RunInputs.open(run.output)
    assert inputs.description() == description
    fovs = inputs.fovs()
    got = Counter(
        (f.condition_id, f.repeat_group, f.technical_repeat_id, f.channel_ids) for f in fovs
    )
    assert got == _expected(description)
    assert len({f.key for f in fovs}) == len(fovs)


def test_v34_member_image_count_is_the_total_over_its_technical_repeats(tmp_path):
    description = description_from_bytes(VALID["v34"].data)
    run = make_clean_run(tmp_path / "run", description=VALID["v34"].data)
    fovs = RunInputs.open(run.output).fovs()
    per_member = Counter((f.condition_id, f.repeat_group) for f in fovs)
    per_unit = Counter((f.condition_id, f.repeat_group, f.technical_repeat_id) for f in fovs)
    # Counts read straight from the parsed description (v34: CTRL/rep1 12, DrugA/rep1 11).
    assert description.repeats["rep1"].members["CTRL"].image_count == 12
    assert description.repeats["rep1"].members["DrugA"].image_count == 11
    assert per_member[("CTRL", "rep1")] == 12
    assert per_member[("DrugA", "rep1")] == 11
    assert per_unit[("CTRL", "rep1", "acq1")] == 6 and per_unit[("CTRL", "rep1", "acq2")] == 6
    assert per_unit[("DrugA", "rep1", "acq1")] == 6 and per_unit[("DrugA", "rep1", "acq2")] == 5
    for repeat_id, repeat in description.repeats.items():
        for condition_id, member in repeat.members.items():
            assert per_member[(condition_id, repeat_id)] == member.image_count
    assert {f.technical_repeat_id for f in fovs if f.repeat_group == "rep1"} == {"acq1", "acq2"}
    assert sum(per_member.values()) == len(fovs) == 43


def test_a_member_with_fewer_images_than_technical_repeats_is_refused(tmp_path):
    data = VALID["v34"].data.decode().replace("image_count = 11", "image_count = 1")
    with pytest.raises(ValueError, match=r"'DrugA'.*image_count 1 for 2 technical repeats"):
        make_described_run(tmp_path / "run", data)
    assert not (tmp_path / "run" / "experiment").exists()


def test_a_description_object_is_published_canonically(tmp_path):
    description = description_from_bytes(VALID["v13"].data)
    run = make_clean_run(tmp_path / "run", description=description)
    assert (run.output / "morphobricks.toml").read_bytes() == canonical_bytes(description)
    assert len(RunInputs.open(run.output).fovs()) == sum(_expected(description).values())


def test_v28_is_written_as_rgb_images(tmp_path):
    run = make_clean_run(tmp_path / "run", description=VALID["v28"].data)
    fov = RunInputs.open(run.output).fovs()[0]
    assert fov.channel_ids == ("R", "G", "B") and fov.axes == "YXS"


def test_v08_masks_are_written_and_checked_by_the_mask_step(tmp_path):
    run = make_clean_run(
        tmp_path / "run", description=VALID["v08"].data, mask_types=("Nuclei", "TumorRegion")
    )
    inputs = RunInputs.open(run.output)
    fov = inputs.fovs()[0]
    assert run.masks is not None and (run.masks / "nuclei").is_dir()
    assert inputs.mask_check_status("Nuclei", fov) == "checked"
    assert inputs.mask_check_status("TumorRegion", fov) == "checked"
    assert inputs.read_mask(fov, "nuclei").max() == 2
    assert set(inputs.read_mask(fov, "tumor_region").ravel().tolist()) == {0, 1}


def test_the_mask_step_can_be_left_out(tmp_path):
    run = make_described_run(
        tmp_path / "run", VALID["v08"].data, masks=("TumorRegion",), mask_step=False
    )
    inputs = RunInputs.open(run.output)
    assert inputs.mask_check_status("TumorRegion", inputs.fovs()[0]) == "not yet checked"


def test_without_masks_there_is_no_masks_folder(tmp_path):
    run = make_clean_run(tmp_path / "run", description=VALID["v13"].data)
    assert run.masks is None and not (run.experiment.parent / "masks").exists()


@pytest.mark.parametrize(
    ("change", "named"),
    [
        ({"axes": "CZYX"}, "axes"),
        ({"shape": (3, 8, 8)}, "shape"),
        ({"pixel_size": None}, "pixel_size"),
        ({"design": True}, "design"),
        ({"seed": 1}, "seed"),
    ],
)
def test_an_argument_the_description_replaces_is_refused(tmp_path, change, named):
    with pytest.raises(ValueError, match=named):
        make_clean_run(tmp_path / "run", description=VALID["v13"].data, **change)
    assert not (tmp_path / "run").exists()


def test_an_undeclared_mask_is_refused_before_anything_is_written(tmp_path):
    with pytest.raises(ValueError, match="'Cells'"):
        make_clean_run(tmp_path / "run", description=VALID["v08"].data, mask_types=("Cells",))
    assert not (tmp_path / "run" / "experiment").exists()


def test_a_run_mixing_rgb_and_fluorescence_units_is_refused(tmp_path):
    mixed = next(item for item in fixtures("valid") if item.name.startswith("v29_"))
    with pytest.raises(ValueError, match="mix rgb and fluorescence"):
        make_clean_run(tmp_path / "run", description=mixed.data)


def test_an_invalid_description_is_refused_with_the_readers_error(tmp_path):
    with pytest.raises(Exception, match="schema"):
        make_clean_run(tmp_path / "run", description=b'schema = "wrong"\n')


@pytest.mark.filterwarnings("ignore:No experiment description")
def test_make_clean_run_without_a_description_is_unchanged(tmp_path):
    run = make_clean_run(tmp_path / "run", mask_types=("Cells",), design=True)
    assert run.description is None and run.masks is not None
    assert not (run.output / "morphobricks.toml").exists()
    fovs = RunInputs.open(run.output).fovs()
    assert {f.key[:3] for f in fovs} == {("ctrl", "rep1", "single"), ("drug", "rep1", "single")}
    assert all(f.condition_id is None for f in fovs)
