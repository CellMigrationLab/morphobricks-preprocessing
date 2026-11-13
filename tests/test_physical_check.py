"""The physical ``R_*`` checks on Spec fixtures: conformance P01-P03, P06, P10, P12, P13, P23, P26.

Experiment-v1 section 8. The folder is built from a vendored valid fixture (one small image
per unit position, header names taken from the fixture's confirmed names), then one image or
one declaration is changed. The owner's answers: Q3 (no batch confirmation of unnamed
layouts), Q5 (no default mask representation: nothing here reads or infers one), Q7
(time-lapses are allowed; homogeneity compares axis semantics, not T counts).
"""

from dataclasses import replace

import numpy as np
import pytest
import tifffile

from description_tree import two_unnamed_units, write_fluorophores
from morphobricks_preprocessing.axes import AxisMapping, AxisReview
from morphobricks_preprocessing.description import Description, description_from_bytes
from morphobricks_preprocessing.discovery import discover_experiment
from morphobricks_preprocessing.metadata import OME_ZARR, inspect_images
from morphobricks_preprocessing.physical_check import (
    CHANNEL_COUNT_MISMATCH,
    HEADER_CONTRADICTION,
    IMAGE_TYPE_CONTRADICTS_HEADER,
    MASK_TYPE_NOT_DECLARED,
    PHYSICAL_CODES,
    UNIT_NOT_DECLARED,
    UNIT_NOT_FOUND,
    UNIT_NOT_HOMOGENEOUS,
    UnitKey,
    check_physical,
    layout_groups,
)
from morphobricks_preprocessing.preview import channel_strip
from spec_pin import fixtures

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}
DECOMPOSED = "Café"
COMPOSED = "Café"


def model(name):
    return description_from_bytes(VALID[name].data)


def write_plane(path, names, *, rgb=False, shape=None, axes=None):
    """Write one small image: an OME file with channel ``names`` (None: no names), or RGB."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if rgb:
        tifffile.imwrite(path, np.zeros((6, 7, 3), np.uint8), photometric="rgb")
    elif names is None:
        count = shape or 2
        tifffile.imwrite(
            path,
            np.zeros((count, 6, 7), np.uint16),
            photometric="minisblack",
            metadata={"axes": "CYX"},
        )
    else:
        tifffile.imwrite(
            path,
            np.zeros((*(shape or (len(names),)), 6, 7), np.uint16),
            ome=True,
            photometric="minisblack",
            metadata={"axes": axes or "CYX", "Channel": {"Name": list(names)}},
        )
    return path


def confirmed_names(description: Description, unit):
    """The first confirmed header name of each channel of the unit (None if it has none)."""
    names = [(description.channels[c].header_names or (None,))[0] for c in unit.channels]
    return None if None in names else names


def build(root, description, *, images=2):
    """Write every unit of the description as a folder of ``images`` conforming images."""
    for repeat in description.repeats.values():
        for condition_id, member in repeat.members.items():
            folder = description.conditions[condition_id].source_folder
            for unit in member.technical_repeats.values():
                names = confirmed_names(description, unit)
                parts = [
                    p for p in (folder, member.source_repeat, unit.source_folder) if p != "single"
                ]
                for index in range(1, images + 1):
                    path = root.joinpath(*parts, f"FOV{index:03d}.ome.tif")
                    write_plane(path, names, rgb=unit.image_type == "rgb")
    return root


def check(root, description, *, excluded=(), **kwargs):
    discovery = discover_experiment(root)
    files = inspect_images(root, discovery.images).files
    return check_physical(description, discovery, files, excluded=excluded, **kwargs)


def issues_of(report, code):
    return [i for i in report.issues if i.code == code]


def unit_dir(root, *parts):
    return root.joinpath(*parts)


@pytest.fixture
def v01(tmp_path):
    description = model("v01")
    return build(tmp_path / "exp", description), description


def test_a_folder_that_matches_its_description_passes(v01):
    root, description = v01
    report = check(root, description)
    assert report.ok and not report.unchecked
    assert report.issues == ()
    layout = report.layouts[UnitKey("CTRL", "rep1", "single")]
    assert (layout.channels, layout.names, layout.rgb) == (2, ("DAPI", "Phalloidin-488"), False)
    assert report.image_type_verified[UnitKey("CTRL", "rep1", "single")] is True


def test_the_codes_are_the_ones_of_section_8_and_only_those(v01):
    root, description = v01
    assert PHYSICAL_CODES == {
        "R_UNIT_NOT_FOUND",
        "R_UNIT_NOT_DECLARED",
        "R_CHANNEL_COUNT_MISMATCH",
        "R_UNIT_NOT_HOMOGENEOUS",
        "R_HEADER_CONTRADICTION",
        "R_IMAGE_TYPE_CONTRADICTS_HEADER",
        "R_RGB_OVERRIDE_NOT_THREE_SAMPLES",  # amendment RH1 (tests/test_rgb_header_override.py)
        "R_MASK_TYPE_NOT_DECLARED",
    }


# P01, P02 ------------------------------------------------------------------------------


def test_p01_an_image_with_another_channel_order_is_not_homogeneous(v01):
    root, description = v01
    write_plane(root / "CTRL" / "rep1" / "FOV003.ome.tif", ["Phalloidin-488", "DAPI"])
    report = check(root, description)
    found = issues_of(report, UNIT_NOT_HOMOGENEOUS)
    assert report.codes == {UNIT_NOT_HOMOGENEOUS}  # replaces the header contradiction
    assert {(i.fov, i.path, i.c_index) for i in found} == {
        ("FOV003", "CTRL/rep1/FOV003.ome.tif", 0),
        ("FOV003", "CTRL/rep1/FOV003.ome.tif", 1),
    }
    assert UnitKey("CTRL", "rep1", "single") not in report.layouts  # the unit is blocked


def test_p02_an_image_with_one_channel_is_not_homogeneous_not_a_count_mismatch(v01):
    root, description = v01
    write_plane(root / "CTRL" / "rep1" / "FOV004.ome.tif", ["DAPI"])
    report = check(root, description)
    assert report.codes == {UNIT_NOT_HOMOGENEOUS}
    assert {i.fov for i in report.issues} == {"FOV004"}


def test_a_whole_unit_with_the_wrong_count_is_a_count_mismatch(v01):
    root, description = v01
    for path in (root / "CTRL" / "rep1").glob("*.tif"):
        write_plane(path, ["DAPI", "Phalloidin-488", "Extra"])
    report = check(root, description)
    assert report.codes == {CHANNEL_COUNT_MISMATCH}
    assert {i.unit for i in report.issues} == {UnitKey("CTRL", "rep1", "single")}


# P03, P26 ------------------------------------------------------------------------------


def rewrite_unit(folder, names):
    for path in folder.glob("*.tif"):
        write_plane(path, names)


@pytest.mark.parametrize("name", ["hoechst 33342", "Hoechst 33342"])
def test_p03_no_case_folding_and_no_similarity(tmp_path, name):
    description = model("v11")
    root = build(tmp_path / "exp", description)
    rewrite_unit(root / "CTRL" / "rep1" / "acquisition_1", [name, "ACTIN"])
    report = check(root, description)
    if name == "Hoechst 33342":
        assert report.ok
    else:
        found = issues_of(report, HEADER_CONTRADICTION)
        assert {(i.c_index, i.unit) for i in found} == {
            (0, UnitKey("CTRL", "rep1", "acquisition_1"))
        }
        assert report.codes == {HEADER_CONTRADICTION}


def test_p26_outer_whitespace_is_not_trimmed_against_v11_but_a_confirmed_alias_passes_v31(tmp_path):
    for fixture, expected in (("v11", {HEADER_CONTRADICTION}), ("v31", set())):
        description = model(fixture)
        root = build(tmp_path / fixture, description)
        for folder in (root / "CTRL").rglob("rep1"):
            rewrite_unit(folder, [" DAPI", "ACTIN" if fixture == "v11" else f"{DECOMPOSED} 488"])
            for sub in folder.iterdir():
                if sub.is_dir():
                    rewrite_unit(sub, [" DAPI", "ACTIN"])
        assert check(root, description).codes == expected


@pytest.mark.parametrize(("header", "entry"), [(DECOMPOSED, COMPOSED), (COMPOSED, DECOMPOSED)])
def test_p26_header_names_match_after_nfc_of_both_sides(tmp_path, header, entry):
    description = model("v31")
    channels = dict(description.channels)
    channels["ACTIN"] = replace(channels["ACTIN"], header_names=(f"{entry} 488",))
    description = replace(description, channels=channels)
    root = build(tmp_path / "exp", description)
    rewrite_unit(root / "CTRL" / "rep1", ["DAPI", f"{header} 488"])
    report = check(root, description)
    assert report.ok
    assert report.layouts[UnitKey("CTRL", "rep1", "single")].names[1] == f"{header} 488"  # as read


def test_an_image_with_names_when_the_channel_has_none_contradicts(tmp_path):
    description = model("v12")
    root = build(tmp_path / "exp", description)
    assert check(root, description).ok  # unnamed images need no evidence
    first = next(iter(sorted(root.rglob("*.tif"))))
    names = ["DAPI", "Phalloidin"]
    for path in first.parent.glob("*.tif"):
        write_plane(path, names)
    assert check(root, description).codes == {HEADER_CONTRADICTION}


# P06 -------------------------------------------------------------------------------------


def _p06_tree(root, *, swapped):
    """CTRL/rep1 and DrugA/rep1, four unnamed images each; DrugA's fluorophores maybe swapped."""
    for condition in ("CTRL", "DrugA"):
        for index in range(4):
            path = root / condition / "rep1" / f"FOV{index}.tif"
            write_fluorophores(path, swapped=swapped and condition == "DrugA")
    return root


def test_p06_swapped_fluorophores_in_unnamed_units_pass_and_each_unit_needs_its_own_look(
    tmp_path,
):
    """P06: no refusal (a documented limit); what guards it is the preview and confirmation."""
    description = description_from_bytes(two_unnamed_units(VALID["v12"].data))
    same = check(_p06_tree(tmp_path / "same", swapped=False), description)
    root = _p06_tree(tmp_path / "swapped", swapped=True)
    report = check(root, description)
    assert report.ok and report.issues == () and report.unchecked == ()
    assert report == same  # the pixels are never compared: nothing tells the two apart
    ctrl, drug = UnitKey("CTRL", "rep1", "single"), UnitKey("DrugA", "rep1", "single")
    assert report.layouts[ctrl] == report.layouts[drug]
    assert (report.layouts[ctrl].channels, report.layouts[ctrl].names) == (2, ())
    (group,) = layout_groups(report.layouts)
    assert group.units == (ctrl, drug) and not group.batch_confirmable  # Q3: one look per unit
    mapping = AxisMapping("CYX", "CYX")
    shown = {}
    for condition in ("CTRL", "DrugA"):
        relative = f"{condition}/rep1/FOV0.tif"
        strip = channel_strip(
            root / relative, relative, AxisReview(mapping, {relative: mapping}, ())
        )
        assert [p.label for p in strip.panels] == ["C0", "C1"]  # no name is made up
        shown[condition] = [int(p.plane.pixels.max()) for p in strip.panels]
    assert shown == {"CTRL": [200, 60], "DrugA": [60, 200]}  # the preview shows the swap


# P10, P12 ------------------------------------------------------------------------------


def test_p10_an_undeclared_mask_type_is_refused_and_a_declared_empty_one_is_valid(tmp_path):
    description = model("v07")
    root = build(tmp_path / "exp", description)
    declared = sorted({m.source_type for m in description.masks.values()})
    assert declared
    report = check(root, description, mask_types=[*declared, "membrane"])
    (found,) = issues_of(report, MASK_TYPE_NOT_DECLARED)
    assert found.detail == "membrane" and found.unit is None
    assert check(root, description, mask_types=[]).ok  # a declared mask with no files is valid
    assert check(root, description, mask_types=declared).ok
    assert check(root, description).ok  # masks not scanned: not examined, never a default


def test_p12_a_declared_unit_with_every_image_excluded_is_not_found(v01):
    root, description = v01
    images = [p.relative_to(root).as_posix() for p in (root / "DrugA" / "rep2").glob("*.tif")]
    report = check(root, description, excluded=images)
    assert report.codes == {UNIT_NOT_FOUND}
    assert issues_of(report, UNIT_NOT_FOUND)[0].unit == UnitKey("DrugA", "rep2", "single")


def test_a_discovered_unit_that_is_not_in_the_file_is_not_declared(v01):
    root, description = v01
    write_plane(root / "DrugA" / "rep3" / "FOV001.ome.tif", ["DAPI", "Phalloidin-488"])
    report = check(root, description)
    assert report.codes == {UNIT_NOT_DECLARED}
    assert issues_of(report, UNIT_NOT_DECLARED)[0].unit == UnitKey("DrugA", "rep3", "single")


# P13, P23 ------------------------------------------------------------------------------


def test_p13_rgb_units_declared_rgb_match_the_header_and_are_header_verified(tmp_path):
    description = model("v28")
    root = build(tmp_path / "exp", description)
    report = check(root, description)
    assert report.ok
    assert all(report.image_type_verified.values()) and report.image_type_verified
    layout = report.layouts[UnitKey("CTRL", "rep1", "single")]
    assert (layout.channels, layout.rgb) == (3, True)


def test_p23_a_unit_declared_rgb_whose_images_are_two_channel_grayscale(tmp_path):
    description = model("v28")
    root = build(tmp_path / "exp", description)
    for path in (root / "CTRL" / "rep1").glob("*.tif"):
        write_plane(path, None)
    report = check(root, description)
    assert report.codes == {IMAGE_TYPE_CONTRADICTS_HEADER}
    assert {i.unit for i in report.issues} == {UnitKey("CTRL", "rep1", "single")}


def test_p23_an_rgb_tiff_in_a_unit_that_does_not_declare_it(v01):
    root, description = v01
    for path in (root / "CTRL" / "rep1").glob("*.tif"):
        write_plane(path, None, rgb=True)
    assert check(root, description).codes == {IMAGE_TYPE_CONTRADICTS_HEADER}


def test_p23_pixels_that_look_like_rgb_are_never_examined(tmp_path):
    description = model("v28")
    description = replace(
        description,
        repeats={
            key: replace(
                repeat,
                members={
                    c: replace(
                        m,
                        technical_repeats={
                            k: replace(u, image_type=None, channels=("R", "G", "B"))
                            for k, u in m.technical_repeats.items()
                        },
                    )
                    for c, m in repeat.members.items()
                },
            )
            for key, repeat in description.repeats.items()
        },
    )
    root = tmp_path / "exp"
    for path in ("CTRL/rep1/a.tif", "DrugA/rep1/a.tif", "CTRL/rep2/a.tif", "DrugA/rep2/a.tif"):
        data = np.zeros((3, 6, 7), np.uint8)
        data[0], data[1], data[2] = 255, 128, 0  # looks like colour
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(root / path, data, photometric="minisblack", metadata={"axes": "CYX"})
    assert check(root, description).ok


def test_a_format_without_a_colour_interpretation_is_recorded_as_not_header_verified(tmp_path):
    description = model("v28")
    root = build(tmp_path / "exp", description)
    discovery = discover_experiment(root)
    files = [
        replace(f, kind=OME_ZARR, rgb_samples=False)
        for f in inspect_images(root, discovery.images).files
    ]
    report = check_physical(description, discovery, files)
    assert IMAGE_TYPE_CONTRADICTS_HEADER not in report.codes  # it cannot contradict
    assert not any(report.image_type_verified.values())


# Q7, unchecked -------------------------------------------------------------------------


def test_q7_time_lapses_with_different_numbers_of_time_points_are_homogeneous(tmp_path):
    description = model("v11")
    root = build(tmp_path / "exp", description)
    folder = root / "CTRL" / "rep1" / "acquisition_1"
    names = ["DAPI", "ACTIN"]
    write_plane(folder / "FOV001.ome.tif", names, shape=(3, 2), axes="TCYX")
    write_plane(folder / "FOV002.ome.tif", names, shape=(5, 2), axes="TCYX")
    write_plane(folder / "FOV003.ome.tif", names)  # one time point, no T axis
    report = check(root, description)
    assert UNIT_NOT_HOMOGENEOUS not in report.codes
    assert report.ok


def test_an_unreadable_image_and_an_unnamed_axis_are_listed_not_passed(tmp_path):
    description = model("v11")
    root = build(tmp_path / "exp", description)
    folder = root / "CTRL" / "rep1" / "acquisition_1"
    for path in folder.glob("*.tif"):
        path.unlink()
    (folder / "FOV009.tif").write_bytes(b"not an image")
    tifffile.imwrite(
        folder / "FOV010.tif", np.zeros((2, 6, 7), np.uint16), photometric="minisblack"
    )
    report = check(root, description)
    assert "CTRL/rep1/acquisition_1/FOV009.tif" in report.unchecked
    assert "CTRL/rep1/acquisition_1/FOV010.tif" in report.unchecked  # plain stack: no roles yet
    roles = {"CTRL/rep1/acquisition_1/FOV010.tif": ("C", "Y", "X")}
    with_roles = check(root, description, roles=roles)
    assert "CTRL/rep1/acquisition_1/FOV010.tif" not in with_roles.unchecked


# Q3 -------------------------------------------------------------------------------------


def test_q3_unnamed_layouts_are_not_batch_confirmable(tmp_path):
    description = model("v11")
    root = build(tmp_path / "exp", description)  # two named units, same names
    report = check(root, description)
    (group,) = layout_groups(report.layouts)
    assert len(group.units) == 2 and group.batch_confirmable
    for path in (root / "CTRL" / "rep1" / "acquisition_2").glob("*.tif"):
        write_plane(path, None)
    other = check(root, description)
    groups = layout_groups(other.layouts)
    assert [g.batch_confirmable for g in groups] == [False, True] or [
        g.batch_confirmable for g in groups
    ] == [True, False]
    unnamed = next(g for g in groups if not g.batch_confirmable)
    assert unnamed.units == (UnitKey("CTRL", "rep1", "acquisition_2"),)
    assert not unnamed.layout.named


def test_header_names_group_exactly_as_read(tmp_path):
    description = model("v11")
    root = build(tmp_path / "exp", description)
    rewrite_unit(root / "CTRL" / "rep1" / "acquisition_2", ["Hoechst 33342", "ACTIN"])
    groups = layout_groups(check(root, description).layouts)
    assert len(groups) == 2 and all(g.batch_confirmable for g in groups)
