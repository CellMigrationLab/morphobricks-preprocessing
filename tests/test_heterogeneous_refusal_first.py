"""With units of different image types the run reports the layout refusal alone (#245, #321).

The Spec refuses an RGB unit next to a fluorescence unit (``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED``,
fixture v29), decided from the description "before any image is read" (Spec PR #49, P04(b)).
The run's checks end there (issue #321): no header check runs after it, so the axis review's
``inconsistent_mapping`` (a consequence of the layouts, issue #245) no longer follows it. Nothing
refused before is accepted: the run still stops and writes no table.
"""

import json
from itertools import combinations

import pytest

from described_tree import run_described
from description_tree import build_experiment
from morphobricks_preprocessing import prefill_mapping, preflight
from morphobricks_preprocessing.axis_review import review_axes
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_candidate import check_candidate
from morphobricks_preprocessing.discovery_report import REPORT_FILENAME
from morphobricks_preprocessing.image_consistency import arrangement_issues
from morphobricks_preprocessing.notebook_check_view import checks_html
from morphobricks_preprocessing.unit_consistency import unit_checked
from run_files import only_the_report
from test_described_unit_checks import (
    LAYOUT,
    VALID,
    _checked_notebook,
    mixed_experiment,
    publish,
)
from test_metadata import meta


def test_v29_reports_exactly_the_layout_refusal(tmp_path):
    # Issue #321: on old code an inconsistent_mapping error from the header checks followed it.
    root, out = mixed_experiment(tmp_path)
    publish(out)
    result = run_described(root, prefill_mapping("CYX"), output_folder=out)
    assert not result.written
    assert [i.code for i in result.issues] == [LAYOUT]
    report = json.loads((out / REPORT_FILENAME).read_text(encoding="utf-8"))
    assert report["status"] == "stopped"
    assert [i["code"] for i in report["issues"]] == [LAYOUT]
    assert only_the_report(out)


def test_v29_reports_exactly_the_layout_refusal_without_axis_roles(tmp_path):
    root, out = mixed_experiment(tmp_path)
    publish(out)
    result = run_described(root, None, output_folder=out)
    assert [i.code for i in result.issues] == [LAYOUT]  # not axes_not_confirmed either


def test_v29_reports_nothing_else_of_preflight_discovery_masks_or_exclusions(tmp_path):
    """Issue #334: with units of different image types the layout refusal is the only issue.

    Preflight still reads every header (``bind_headers=True``), but what it finds is not
    reported: an unreadable ``.tif`` (``unreadable_tiff``), a masks folder that does not exist
    (``masks_folder_not_found``) and an excluded path that does not exist (the warning
    ``excluded_path_not_found``) all go unreported beside ``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED``.
    """
    root, out = mixed_experiment(tmp_path)
    (root / "CTRL/rep1/fluorescence/broken.tif").write_bytes(b"not a tiff")
    absent_masks, gone = tmp_path / "no_masks", "CTRL/rep1/gone.tif"
    # The same inputs give those three issues to the preflight the run starts with.
    found = {i.code for i in preflight(root, out, exclude=[gone], masks_folder=absent_masks).issues}
    assert {"unreadable_tiff", "masks_folder_not_found", "excluded_path_not_found"} <= found
    publish(out)
    result = run_described(
        root,
        prefill_mapping("CYX"),
        output_folder=out,
        masks_folder=absent_masks,
        exclude=[gone],
    )
    assert not result.written
    (only,) = result.issues
    assert only.code == LAYOUT
    report = json.loads((out / REPORT_FILENAME).read_text(encoding="utf-8"))
    assert report["status"] == "stopped"
    assert [i["code"] for i in report["issues"]] == [LAYOUT]
    assert only_the_report(out)


def test_a_run_of_one_layout_keeps_the_advice_of_the_axis_review(tmp_path):
    description = description_from_bytes(VALID["v01"].data)
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir()
    (out / "morphobricks.toml").write_bytes(VALID["v01"].data)
    build_experiment(root, description, images=2)
    result = run_described(root, prefill_mapping("YXC"), output_folder=out)
    (axis,) = [i for i in result.issues if i.code == "inconsistent_mapping"]
    assert "Export the images again" in axis.action
    assert LAYOUT not in {i.code for i in result.issues}


@pytest.mark.parametrize("confirmed", [False, True])
def test_the_check_and_save_list_leads_with_the_layout_refusal(tmp_path, confirmed):
    root, out = mixed_experiment(tmp_path)
    pre = unit_checked(preflight(root, out))
    roles = review_axes(pre.metadata, prefill_mapping("CYX")).file_mappings if confirmed else None
    check = check_candidate(description_from_bytes(VALID["v29"].data), pre, roles)
    assert check.run_refusals[0].code == LAYOUT
    assert check.heterogeneous is check.run_refusals[0]
    text = checks_html(check)
    assert "2 channel layouts" in text
    assert "inconsistent_mapping" not in text


def test_the_axis_step_says_the_layouts_are_why_no_roles_are_suggested(tmp_path):
    root, out = mixed_experiment(tmp_path)
    publish(out)
    nb = _checked_notebook(root, out)
    assert nb.mapping is None
    text = nb._axis_waiting()
    assert "the units declare different image types" in text
    assert "fluorescence and RGB" in text
    assert "one image type per experiment" in text
    assert "dimensions are arranged differently" not in text


def test_the_axis_step_keeps_its_general_message_without_a_description(tmp_path):
    root, out = mixed_experiment(tmp_path)
    nb = _checked_notebook(root, out)
    assert nb.mapping is None
    text = nb._axis_waiting()
    assert "No axis roles can be suggested." in text
    assert "different image types" not in text


BASE = ["TCZYX", "TCYX", "CTYX", "TZYX", "CYX", "TYX", "ZYX", "YX"]


def _dropped(short, long):
    rest = iter(long)
    return all(axis in rest for axis in short)


def _found(*axes):
    files = [meta(f"{n}.tif", axes=a) for n, a in enumerate(axes)]
    return {i.code for i in arrangement_issues(files)}


def test_missing_axes_means_one_longest_arrangement_and_the_rest_with_axes_dropped():
    """Pin the ``missing_axes`` rule against an independent statement of it (issue #243 review).

    The clause ``sum(len(a) == len(longest) ...) == 1`` is equivalent to ``>= 1``: two different
    arrangements of the longest length are never both subsequences of it, so the ``all(...)``
    before it already refuses them. Mutating it to ``>= 1`` therefore changes no behaviour and no
    test can kill it. What this test fixes is the behaviour, for every pair and triple of
    arrangements: ``missing_axes`` only when every image is the longest with axes dropped, else
    ``inconsistent_dimensions``, and nothing for one arrangement.
    """
    for group in [*combinations(BASE, 2), *combinations(BASE, 3)]:
        longest = max(group, key=len)
        dropped = all(_dropped(axes, longest) for axes in group)
        assert _found(*group) == {"missing_axes" if dropped else "inconsistent_dimensions"}, group
    assert _found("CYX", "CYX") == set()


def test_layout_kinds_names_two_channel_lists_of_one_image_type_in_words():
    """Two fluorescence layouts differ by channels, not by type: say so, not "fluorescence"."""
    from morphobricks_preprocessing.description_layouts import DeclaredLayout, layout_kinds

    both = (
        DeclaredLayout(("DNA",), "fluorescence", ()),
        DeclaredLayout(("DNA", "ACTIN"), None, ()),
    )
    assert layout_kinds(both) == "fluorescence, with different channel lists"
    mixed = (both[0], DeclaredLayout(("R", "G", "B"), "rgb", ()))
    assert layout_kinds(mixed) == "fluorescence and RGB"
