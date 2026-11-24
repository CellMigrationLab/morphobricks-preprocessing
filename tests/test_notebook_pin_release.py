"""The Cellpose pin's freshness check runs only in release runs (owner, 24 November 2026).

Pull requests no longer re-pin the notebooks: ``check_pin_holds_this_code`` skips unless
``MORPHOBRICKS_RELEASE_RUN=1`` (``release_run.py``), and in a release run it checks the pin on
the small Git repositories of ``test_notebook_cellpose_pin.py``, where a missing pinned commit
fails instead of skipping. The structural pin checks run in every run.
"""

import importlib
from pathlib import Path

import pytest

import test_notebook_cellpose_pin as pin_tests
from notebook_pin import check_pin_holds_this_code, history_missing
from release_run import RELEASE_ONLY, SWITCH, release_run
from test_notebook_cellpose_pin import NOTEBOOK, PKG, _commit

repo = pin_tests.repo
"""The fixture: a Git repository whose first commit holds the small package."""

ROOT = Path(__file__).parents[1]
CHANGED = {f"{PKG}/model.py": "def load():\n    return 'other weights'\n"}
"""A change in code the notebook runs: its pin must move."""


@pytest.fixture
def ci_off(monkeypatch):
    """Neither CI nor a release run, unless a test sets the switch."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv(SWITCH, raising=False)
    return monkeypatch


@pytest.mark.parametrize("value", [None, "", "0"])
def test_the_freshness_check_skips_outside_a_release_run(repo, ci_off, value):
    if value is not None:
        ci_off.setenv(SWITCH, value)
    _commit(repo.path, CHANGED)  # a stale pin: it would fail in a release run
    with pytest.raises(pytest.skip.Exception, match="release-only: the notebooks are pinned"):
        check_pin_holds_this_code(repo.path, repo.pin, NOTEBOOK)


def test_the_freshness_check_skips_outside_a_release_run_in_ci_too(repo, ci_off):
    ci_off.setenv("CI", "true")
    _commit(repo.path, CHANGED)
    with pytest.raises(pytest.skip.Exception, match="release-only"):
        check_pin_holds_this_code(repo.path, repo.pin, NOTEBOOK)


def test_in_a_release_run_a_stale_pin_fails_and_a_fresh_one_passes(repo, ci_off):
    ci_off.setenv(SWITCH, "1")
    check_pin_holds_this_code(repo.path, repo.pin, NOTEBOOK)  # HEAD is the pin
    _commit(repo.path, {f"{PKG}/unused.py": "def other():\n    return 3\n"})
    check_pin_holds_this_code(repo.path, repo.pin, NOTEBOOK)  # code the notebook does not run
    _commit(repo.path, CHANGED)
    with pytest.raises(AssertionError, match=r"move the pin \(\['src/.*/model.py'\]\)"):
        check_pin_holds_this_code(repo.path, repo.pin, NOTEBOOK)


@pytest.mark.parametrize("ci", ["", "true"])
def test_in_a_release_run_a_missing_pinned_commit_fails(repo, ci_off, ci):
    ci_off.setenv(SWITCH, "1")
    ci_off.setenv("CI", ci)
    missing = "1" * 40
    with pytest.raises(pytest.fail.Exception, match="not in this checkout.*full history"):
        check_pin_holds_this_code(repo.path, missing, NOTEBOOK)


def test_in_a_release_run_no_git_repository_fails(tmp_path, ci_off):
    ci_off.setenv(SWITCH, "1")
    with pytest.raises(pytest.fail.Exception, match="not in this checkout"):
        check_pin_holds_this_code(tmp_path, "1" * 40, NOTEBOOK)


def test_a_missing_history_fails_in_a_release_run_without_ci(ci_off):
    ci_off.setenv(SWITCH, "1")
    with pytest.raises(pytest.fail.Exception, match="not in this checkout"):
        history_missing("the pinned commit is not in this checkout")


@pytest.mark.parametrize("value", ["true", "yes", "2", " 1"])
def test_a_switch_value_other_than_0_or_1_is_refused(ci_off, value):
    ci_off.setenv(SWITCH, value)
    with pytest.raises(pytest.fail.Exception, match=f"{SWITCH} must be 1"):
        release_run()


def test_the_skip_reason_says_the_notebooks_are_pinned_in_the_release_pr():
    assert RELEASE_ONLY.startswith("release-only: the notebooks are pinned in the release PR")


@pytest.mark.parametrize(("flag", "expected"), [([], None), (["--release"], "1")])
def test_tools_check_sets_the_switch_only_with_release(monkeypatch, flag, expected):
    monkeypatch.delenv(SWITCH, raising=False)
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    check = importlib.import_module("check")
    assert check.RELEASE_SWITCH == SWITCH
    assert check.pytest_environment(flag).get(SWITCH) == expected


def test_the_release_ci_job_checks_the_freshness_test_ran():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert f"{SWITCH}:" in workflow
    assert "test_release_only_the_preprocessing_pin_is_a_commit_with_this_code" in workflow
    assert "fetch-depth: 0" in workflow
