"""With several layouts the layout refusal comes first and the axis advice follows (issue #245).

The Spec refuses an RGB unit next to a fluorescence unit (``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED``,
fixture v29). The axis review's ``inconsistent_mapping`` is a consequence of the layouts, so its
advice to export the images the same way must not lead. Nothing refused before is accepted: the
codes of the refusals are exactly those of before.
"""

from itertools import combinations

import pytest

from description_tree import build_experiment
from morphobricks_preprocessing import prefill_mapping, preflight, run_experiment
from morphobricks_preprocessing.axis_review import review_axes
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_candidate import check_candidate
from morphobricks_preprocessing.image_consistency import arrangement_issues
from morphobricks_preprocessing.notebook_check_view import checks_html
from morphobricks_preprocessing.unit_consistency import unit_checked
from test_described_unit_checks import (
    LAYOUT,
    VALID,
    _checked_notebook,
    mixed_experiment,
    publish,
)
from test_metadata import meta


def test_the_layout_refusal_comes_first_in_the_run(tmp_path):
    root, out = mixed_experiment(tmp_path)
    publish(out)
    result = run_experiment(root, prefill_mapping("CYX"), output_folder=out)
    assert not result.written
    assert [i.code for i in result.issues][0] == LAYOUT
    assert {i.code for i in result.issues} == {LAYOUT, "inconsistent_mapping"}


def test_the_axis_issue_stays_an_error_without_the_advice_to_export_again(tmp_path):
    root, out = mixed_experiment(tmp_path)
    publish(out)
    result = run_experiment(root, prefill_mapping("CYX"), output_folder=out)
    (axis,) = [i for i in result.issues if i.code == "inconsistent_mapping"]
    assert axis.is_error
    assert "Export the images again" not in axis.action
    assert LAYOUT in axis.action and "one layout per experiment" in axis.action


def test_a_run_of_one_layout_keeps_the_advice_of_the_axis_review(tmp_path):
    description = description_from_bytes(VALID["v01"].data)
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir()
    (out / "morphobricks.toml").write_bytes(VALID["v01"].data)
    build_experiment(root, description, images=2)
    result = run_experiment(root, prefill_mapping("YXC"), output_folder=out)
    (axis,) = [i for i in result.issues if i.code == "inconsistent_mapping"]
    assert "Export the images again" in axis.action
    assert LAYOUT not in {i.code for i in result.issues}


@pytest.mark.parametrize("confirmed", [False, True])
def test_the_check_and_save_list_leads_with_the_layout_refusal(tmp_path, confirmed):
    root, out = mixed_experiment(tmp_path)
    pre = unit_checked(preflight(root, out))
    roles = review_axes(pre.metadata, prefill_mapping("CYX")).file_mappings if confirmed else None
    check = check_candidate(description_from_bytes(VALID["v29"].data), pre, roles, out)
    assert check.run_refusals[0].code == LAYOUT
    assert check.heterogeneous is check.run_refusals[0]
    text = checks_html(check)
    assert "2 channel layouts" in text
    assert "inconsistent_mapping" not in text


def test_the_axis_step_says_the_layouts_are_why_no_roles_are_suggested(tmp_path):
    root, out = mixed_experiment(tmp_path)
    publish(out)
    nb = _checked_notebook(root, out, describe=True)
    assert nb.mapping is None
    text = nb._axis_waiting()
    assert "the units declare different layouts" in text
    assert "fluorescence and RGB" in text
    assert "one layout per experiment" in text
    assert "dimensions are arranged differently" not in text


def test_the_axis_step_keeps_its_general_message_without_a_description(tmp_path):
    root, out = mixed_experiment(tmp_path)
    nb = _checked_notebook(root, out, describe=True)
    assert nb.mapping is None
    text = nb._axis_waiting()
    assert "No axis roles can be suggested." in text
    assert "different layouts" not in text


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
