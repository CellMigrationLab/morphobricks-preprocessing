import numpy as np
import pytest

from morphobricks_preprocessing import (
    AxisMapping,
    MetadataReport,
    Severity,
    TiffMetadata,
    check_mapping,
    check_metadata,
    inspect_images,
    prefill_experiment_mapping,
    prefill_mapping,
    reference_axes,
    review_axes,
)
from tiff_tree import write_file, write_tiff


def meta(path, shape, axes, dtype="uint16"):
    return TiffMetadata(path, True, tuple(shape), np.dtype(dtype), axes, 1, 1)


def codes(issues):
    return [issue.code for issue in issues]


def issue(issues, code):
    (found,) = [i for i in issues if i.code == code]
    return found


# AxisMapping --------------------------------------------------------------------------


def test_mapping_accepts_role_strings():
    mapping = AxisMapping("QZYX", "T-YX")
    assert mapping.roles == ("T", None, "Y", "X")
    assert mapping.ndim == 4
    assert mapping.plane_roles == ("T",)
    assert mapping.index_of("Y") == 2 and mapping.index_of("C") is None


def test_mapping_roles_are_upper_case():
    assert AxisMapping("ZYX", ("z", "y", "x")).roles == ("Z", "Y", "X")


@pytest.mark.parametrize(
    ("axes", "roles"), [("ZYX", ("Y", "X")), ("YX", ("Y", "X", "Z")), ("YX", ("Y", "W"))]
)
def test_mapping_rejects_bad_roles(axes, roles):
    with pytest.raises(ValueError):
        AxisMapping(axes, roles)


def test_with_role_returns_a_new_mapping():
    mapping = prefill_mapping("IYX")
    edited = mapping.with_role(0, "Z")
    assert mapping.roles == (None, "Y", "X")
    assert edited.roles == ("Z", "Y", "X") and edited.axes == "IYX"


def test_index_of_rejects_duplicates():
    with pytest.raises(ValueError, match="more than one axis"):
        AxisMapping("ZCYX", "ZZYX").index_of("Z")


def test_describe_is_plain_language():
    text = prefill_mapping("IYXS").describe()
    assert text == "I (sequence) -> no role, Y (height) -> Y, X (width) -> X, S (sample) -> C"


# Prefill ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("axes", "roles"),
    [
        ("YX", ("Y", "X")),
        ("ZYX", ("Z", "Y", "X")),
        ("CYX", ("C", "Y", "X")),
        ("TYX", ("T", "Y", "X")),
        ("ZCYX", ("Z", "C", "Y", "X")),
        ("TZCYX", ("T", "Z", "C", "Y", "X")),
        ("YXS", ("Y", "X", "C")),  # RGB samples become channels
        ("ZSYX", ("Z", "C", "Y", "X")),  # planar RGB
        ("CYXS", ("C", "Y", "X", None)),  # S cannot be C when there is a C axis
        ("IYX", (None, "Y", "X")),  # a plain page sequence is never guessed
        ("QYX", (None, "Y", "X")),
        ("QQYX", (None, None, "Y", "X")),
        ("MZYX", (None, "Z", "Y", "X")),
        ("EYX", (None, "Y", "X")),
    ],
)
def test_prefill_from_metadata_axes(axes, roles):
    assert prefill_mapping(axes) == AxisMapping(axes, roles)


def test_reference_axes_is_the_longest_arrangement():
    files = [meta("a.tif", (2, 3, 8, 8), "ZCYX"), meta("b.tif", (2, 8, 8), "ZYX")]
    assert reference_axes(files) == "ZCYX"


def test_reference_axes_none_for_conflicts_or_no_files():
    assert reference_axes([]) is None
    assert reference_axes([TiffMetadata("a.tif", readable=False)]) is None
    assert reference_axes([meta("a", (2, 8, 8), "ZYX"), meta("b", (2, 8, 8), "CYX")]) is None
    two_longest = [meta("a", (2, 3, 8, 8), "ZCYX"), meta("b", (3, 2, 8, 8), "CZYX")]
    assert reference_axes(two_longest) is None


def test_prefill_experiment_mapping(tmp_path):
    write_tiff(tmp_path / "a.tif", (2, 3, 6, 7), axes="ZCYX")
    write_tiff(tmp_path / "b.tif", (2, 6, 7), axes="ZYX")
    report = inspect_images(tmp_path, ["a.tif", "b.tif"])
    assert prefill_experiment_mapping(report) == prefill_mapping("ZCYX")


def test_prefill_experiment_mapping_none_for_conflicts(tmp_path):
    write_tiff(tmp_path / "a.tif", (3, 6, 7), axes="ZYX")
    write_tiff(tmp_path / "b.tif", (3, 6, 7), axes="CYX")
    assert prefill_experiment_mapping(inspect_images(tmp_path, ["a.tif", "b.tif"])) is None


# Mapping rules (contract §2) ----------------------------------------------------------


def test_valid_mapping_has_no_issues():
    files = [meta("a.tif", (2, 3, 8, 8), "ZCYX")]
    assert check_mapping(prefill_mapping("ZCYX"), files) == []


@pytest.mark.parametrize(("roles", "missing"), [("ZC-X", "Y"), ("ZCY-", "X"), ("ZC--", "X or Y")])
def test_missing_x_or_y_is_an_error(roles, missing):
    files = [meta("a.tif", (2, 3, 8, 8), "ZCYX")]
    found = issue(check_mapping(AxisMapping("ZCYX", roles), files), "missing_xy_axis")
    assert found.severity is Severity.ERROR
    assert f"no {missing} axis" in found.message
    assert found.paths == ("a.tif",)


@pytest.mark.parametrize("roles", ["ZZYX", "ZCXX", "ZCYY", "CCYX"])
def test_duplicate_roles_are_an_error(roles):
    found = issue(check_mapping(AxisMapping("ZCYX", roles)), "duplicate_axis_role")
    assert found.severity is Severity.ERROR
    assert roles[0] in found.message or roles[2] in found.message


def test_duplicate_x_reports_missing_y_too():
    assert codes(check_mapping(AxisMapping("ZCYX", "ZCXX"))) == [
        "missing_xy_axis",
        "duplicate_axis_role",
    ]


def test_non_singleton_axis_needs_a_role():
    files = [meta("a.tif", (5, 8, 8), "IYX")]
    found = issue(check_mapping(prefill_mapping("IYX"), files), "unassigned_axis")
    assert found.severity is Severity.ERROR
    assert "I (sequence)" in found.message
    assert "Z, C or T" in found.action


def test_singleton_axis_needs_no_role():
    files = [meta("a.tif", (1, 8, 8), "QYX"), meta("b.tif", (1, 8, 8), "QYX")]
    assert check_mapping(prefill_mapping("QYX"), files) == []


def test_axis_singleton_in_only_some_files_needs_a_role():
    files = [meta("a.tif", (1, 8, 8), "QYX"), meta("b.tif", (4, 8, 8), "QYX")]
    found = issue(check_mapping(prefill_mapping("QYX"), files), "unassigned_axis")
    assert found.paths == ("b.tif",)


def test_without_files_every_axis_counts_as_non_singleton():
    assert codes(check_mapping(prefill_mapping("QYX"))) == ["unassigned_axis"]


@pytest.mark.parametrize("axis", ["M", "R", "J", "K"])
def test_position_axis_with_several_positions_blocks(axis):
    files = [meta("a.tif", (3, 2, 8, 8), f"{axis}ZYX"), meta("b.tif", (1, 2, 8, 8), f"{axis}ZYX")]
    found = issue(check_mapping(prefill_mapping(f"{axis}ZYX"), files), "unsupported_axis")
    assert found.severity is Severity.ERROR
    assert "positions" in found.message
    assert found.paths == ("a.tif",)


def test_position_axis_cannot_get_a_role_even_if_singleton():
    files = [meta("a.tif", (1, 2, 8, 8), "MZYX")]
    assert check_mapping(prefill_mapping("MZYX"), files) == []
    found = issue(check_mapping(AxisMapping("MZYX", "TZYX"), files), "unsupported_axis")
    assert "cannot be used as Z, C or T" in found.message


def test_messages_have_no_tracebacks_and_suggest_actions():
    for found in check_mapping(AxisMapping("MQYX", "--Y-")):
        assert found.action
        assert "Traceback" not in str(found)


# Applying the mapping to files --------------------------------------------------------


def test_review_assigns_the_mapping_to_every_file():
    files = [meta("a.tif", (2, 3, 8, 8), "ZCYX"), meta("b.tif", (4, 3, 9, 9), "ZCYX")]
    mapping = prefill_mapping("ZCYX")
    review = review_axes(files, mapping)
    assert review.ok and review.issues == ()
    assert dict(review.file_mappings) == {"a.tif": mapping, "b.tif": mapping}


def test_review_skips_unreadable_files():
    files = [meta("a.tif", (8, 8), "YX"), TiffMetadata("bad.tif", readable=False)]
    review = review_axes(files, prefill_mapping("YX"))
    assert review.ok and list(review.file_mappings) == ["a.tif"]


def test_file_mappings_are_read_only():
    review = review_axes([meta("a.tif", (8, 8), "YX")], prefill_mapping("YX"))
    with pytest.raises(TypeError):
        review.file_mappings["b.tif"] = prefill_mapping("YX")


def test_missing_axis_blocks_until_confirmed():
    files = [meta("a.tif", (2, 3, 8, 8), "ZCYX"), meta("b.tif", (2, 8, 8), "ZYX")]
    review = review_axes(files, prefill_mapping("ZCYX"))
    found = issue(review.issues, "missing_axes")
    assert found.severity is Severity.ERROR
    assert found.paths == ("b.tif",)
    assert "confirm" in found.action
    assert "b.tif" not in review.file_mappings


def test_confirmed_missing_axis_is_read_as_singleton():
    files = [meta("a.tif", (2, 3, 8, 8), "ZTYX"), meta("b.tif", (2, 8, 8), "ZYX")]
    review = review_axes(files, prefill_mapping("ZTYX"), missing_as_singleton=True)
    assert review.ok
    b = review.file_mappings["b.tif"]
    assert b == AxisMapping("ZYX", "ZYX", singleton_roles=("T",))
    assert b.plane_roles == ("Z", "T")


def test_confirmed_missing_channel_axis_counts_as_one_channel():
    # Read as one channel, b.tif differs from the three channels of a.tif (N43).
    files = [meta("a.tif", (2, 3, 8, 8), "ZCYX"), meta("b.tif", (2, 8, 8), "ZYX")]
    review = review_axes(files, prefill_mapping("ZCYX"), missing_as_singleton=True)
    assert review.file_mappings["b.tif"].singleton_roles == ("C",)
    assert codes(review.issues) == ["different_channel_counts"]
    found = issue(review.issues, "different_channel_counts")
    assert found.severity is Severity.ERROR and found.paths == ("a.tif",)


def test_channel_count_follows_the_confirmed_channel_axis():
    # The header suggests one channel for an unlabelled axis; the confirmed role decides.
    files = [meta("a.tif", (2, 8, 8), "IYX"), meta("b.tif", (3, 8, 8), "IYX")]
    report = MetadataReport(tuple(files), tuple(check_metadata(files)))
    assert "different_channel_counts" not in codes(report.issues)
    as_time = review_axes(report, prefill_mapping("IYX").with_role(0, "T"))
    # A plain stack never names its axes: read with the confirmed roles, with a warning.
    assert as_time.ok and codes(as_time.issues) == ["axes_not_named"]
    as_channel = review_axes(report, prefill_mapping("IYX").with_role(0, "C"))
    assert codes(as_channel.combined_issues(report)) == [
        "different_channel_counts",
        "different_plane_counts",
        "axes_not_named",
    ]


def test_several_missing_axes_are_resolved():
    mapping = prefill_mapping("TZCYX")
    resolved = mapping.for_axes("ZYX")
    assert resolved == AxisMapping("ZYX", "ZYX", singleton_roles=("T", "C"))
    assert resolved.plane_roles == ("Z", "C", "T")


def test_missing_unassigned_singleton_adds_no_role():
    mapping = AxisMapping("QZYX", "-ZYX")
    assert mapping.for_axes("ZYX") == AxisMapping("ZYX", "ZYX")


def test_ambiguous_missing_axis_is_an_error():
    files = [meta("a.tif", (1, 1, 8, 8), "QQYX"), meta("b.tif", (1, 8, 8), "QYX")]
    mapping = prefill_mapping("QQYX")
    assert mapping.for_axes("QYX") is None
    review = review_axes(files, mapping, missing_as_singleton=True)
    found = issue(review.issues, "ambiguous_missing_axes")
    assert found.paths == ("b.tif",)
    assert "not clear" in found.message


def test_file_lacking_x_or_y_does_not_fit():
    mapping = AxisMapping("ZYX", "ZYX")
    assert mapping.for_axes("ZY") is None


def test_differently_arranged_file_is_an_inconsistent_mapping():
    files = [meta("a.tif", (2, 3, 8, 8), "ZCYX"), meta("b.tif", (3, 2, 8, 8), "CZYX")]
    review = review_axes(files, prefill_mapping("ZCYX"), missing_as_singleton=True)
    found = issue(review.issues, "inconsistent_mapping")
    assert found.severity is Severity.ERROR
    assert found.paths == ("b.tif",)
    assert list(review.file_mappings) == ["a.tif"]


def test_review_includes_the_mapping_rules():
    files = [meta("a.tif", (5, 8, 8), "IYX")]
    review = review_axes(files, prefill_mapping("IYX"))
    assert not review.ok and codes(review.issues) == ["unassigned_axis", "axes_not_named"]
    as_time = review_axes(files, prefill_mapping("IYX").with_role(0, "T"))
    assert as_time.ok and codes(as_time.issues) == ["axes_not_named"]


def test_review_from_metadata_report_replaces_missing_axes(tmp_path):
    write_tiff(tmp_path / "a.tif", (2, 3, 6, 7), axes="ZCYX")
    write_tiff(tmp_path / "b.tif", (2, 6, 7), axes="ZYX")
    write_file(tmp_path / "bad.tif", b"garbage")
    report = inspect_images(tmp_path, ["a.tif", "b.tif", "bad.tif"])
    # One image has 3 channels and the other 1: an error until the axes say otherwise.
    assert codes(report.issues) == [
        "unreadable_tiff",
        "missing_axes",
        "different_channel_counts",
    ]
    mapping = prefill_experiment_mapping(report)

    unconfirmed = review_axes(report, mapping)
    assert codes(unconfirmed.combined_issues(report)) == ["unreadable_tiff", "missing_axes"]

    confirmed = review_axes(report, mapping, missing_as_singleton=True)
    assert codes(confirmed.combined_issues(report)) == [
        "unreadable_tiff",
        "different_channel_counts",
    ]
    # The channel count is the confirmed roles' (C read as T gives one channel), but a named
    # C read as T contradicts the header (v0.4.0).
    as_time = review_axes(report, mapping.with_role(1, "T"), missing_as_singleton=True)
    assert codes(as_time.combined_issues(report)) == ["unreadable_tiff", "axes_contradict_header"]


def test_combined_issues_put_errors_first(tmp_path):
    write_tiff(tmp_path / "a.tif", (3, 6, 7), axes="QYX")
    write_tiff(tmp_path / "b.tif", (3, 8, 9), axes="QYX")
    report = inspect_images(tmp_path, ["a.tif", "b.tif"])
    review = review_axes(report, prefill_mapping("QYX"))
    assert codes(review.combined_issues(report)) == [
        "unassigned_axis",
        "different_sizes",
        "axes_not_named",
    ]


def test_roles_string_round_trips():
    mapping = AxisMapping("QZYX", "T-YX")
    assert mapping.roles_string == "T-YX"
    assert AxisMapping(mapping.axes, mapping.roles_string) == mapping


def test_axis_length_ranges():
    from morphobricks_preprocessing import axis_length_ranges

    files = [
        meta("a.tif", (2, 3, 8, 8), "ZCYX"),
        meta("b.tif", (5, 9, 9), "ZYX"),
        meta("c.tif", (3, 2, 8, 8), "CZYX"),
        TiffMetadata("bad.tif", readable=False),
    ]
    mapping = prefill_mapping("ZCYX")
    assert axis_length_ranges(mapping, files) == [(2, 5), (1, 3), (8, 9), (8, 9)]
    assert axis_length_ranges(mapping, []) == [(0, 0)] * 4
