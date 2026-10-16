import pytest

from morphobricks_preprocessing import Severity, ValidationIssue, has_errors
from morphobricks_preprocessing.validation import error, plural, warning


def test_builders_sort_paths_and_set_severity():
    err = error("code_a", "Something failed.", ["b", "a"], "Fix it.")
    warn = warning("code_b", "Something odd.", ("z", "y"))
    assert err == ValidationIssue(
        Severity.ERROR, "code_a", "Something failed.", ("a", "b"), "Fix it."
    )
    assert err.is_error and not warn.is_error
    assert warn.paths == ("y", "z") and warn.action == ""


def test_has_errors():
    warn = warning("w", "Odd.")
    assert not has_errors([])
    assert not has_errors([warn])
    assert has_errors([warn, error("e", "Bad.")])


def test_issues_are_immutable():
    issue = warning("w", "Odd.")
    with pytest.raises(AttributeError):
        issue.message = "changed"


def test_str_is_plain_text():
    issue = error("e", "Two files failed.", ["x/a.tif", "x/b.tif"], "Re-export them.")
    assert str(issue) == (
        "Error: Two files failed.\n  - x/a.tif\n  - x/b.tif\n  Suggested action: Re-export them."
    )
    assert str(warning("w", "Odd.")) == "Warning: Odd."


def test_severity_values():
    assert Severity("error") is Severity.ERROR
    assert Severity.WARNING.value == "warning"


@pytest.mark.parametrize(
    ("count", "expected"),
    [(0, "0 files"), (1, "1 file"), (2, "2 files"), (1200, "1,200 files")],
)
def test_plural(count, expected):
    assert plural(count, "file") == expected
    assert plural(count, "is", "are").endswith("is" if count == 1 else "are")
