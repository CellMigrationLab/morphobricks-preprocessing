"""Cell 1 of the Preprocessing notebook reuses only the installed commit it pins (issue #371).

An installed package counts as the release only when pip's PEP 610 record (``direct_url.json``)
names ``REPOSITORY`` at ``RELEASE_REF``, never on its version alone; an editable install is a
local checkout, used only when it is the clean ``RELEASE_REF`` commit. The Cellpose notebook
reads the commit with the same functions (one text in both cells, tested here).
"""

import ast
import json
import subprocess
from pathlib import Path

import pytest

from installed_record import editable_record, git_record
from notebook_pin import code_sources
from test_notebook_setup import INSTALL, RELEASE_REF, REPOSITORY, checkout_at
from test_notebook_setup import setup_environment as cell_1  # noqa: F401

NOTEBOOKS = Path(__file__).parents[1] / "notebooks"
INSTALLED_COMMIT = ("_direct_url", "_installed_commit")
"""The install cells' functions that read the installed commit: one text in both notebooks."""


def _functions(text, names):
    """Return the source of each top-level function ``names`` of ``text`` (None when absent)."""
    tree = ast.parse(text)
    found = {
        n.name: ast.get_source_segment(text, n) for n in tree.body if isinstance(n, ast.FunctionDef)
    }
    return [found.get(name) for name in names]


def test_both_notebooks_read_the_installed_commit_with_one_text():
    preprocessing, cellpose = (
        _functions(code_sources(NOTEBOOKS / f"MorphoBricks_{name}.ipynb")[0], INSTALLED_COMMIT)
        for name in ("Preprocessing", "Cellpose")
    )
    assert None not in preprocessing
    assert cellpose == preprocessing


def test_setup_reuses_the_matching_installed_release(cell_1, capsys):  # noqa: F811
    state = cell_1
    state.start()
    assert state.installs == []
    assert state.imports == [
        "morphobricks_preprocessing.notebook_ui",  # cell 1 checks the interface loads
        "morphobricks_preprocessing",
        "morphobricks_preprocessing.notebook_ui",  # cell 3 shows it
    ]
    out = capsys.readouterr().out
    assert "Ready: preprocessing 0.8.0" in out
    assert "ipywidgets 7.7.1" in out


def test_setup_replaces_a_mismatched_installed_release(cell_1):  # noqa: F811
    state = cell_1
    state.versions["morphobricks-preprocessing"] = "0.3.4"
    state.direct_url = git_record("2" * 40, REPOSITORY)
    state.start()
    assert len(state.installs) == 1
    assert state.installs[0][-1].endswith("@" + state.namespace["RELEASE_REF"])


def test_the_banner_shows_the_installed_commit(cell_1, capsys):  # noqa: F811
    state = cell_1
    state.start(INSTALL)
    assert f"Ready: preprocessing 0.8.0 (commit {RELEASE_REF});" in capsys.readouterr().out
    assert state.namespace["SETUP_RECORD"]["preprocessing_commit"] == RELEASE_REF
    assert state.namespace["SETUP_RECORD"]["source"] == "installed package"


# Issue #371: the same version built from another commit is not the release. Each record pip
# can leave that does not name REPOSITORY at RELEASE_REF means installing RELEASE_REF.
NOT_THE_RELEASE = {
    "same version, another commit": git_record("2" * 40, REPOSITORY),
    "another repository at the commit": git_record(
        RELEASE_REF, "https://github.com/someone/fork.git"
    ),
    "no direct_url.json": None,
    "empty direct_url.json": "",
    "unreadable direct_url.json": "{not json",
    "direct_url.json not an object": "[]",
    "direct_url.json cannot be read": OSError("permission denied"),
    "a local folder, not editable": json.dumps({"url": "file:///x", "dir_info": {}}),
    "an archive": json.dumps({"url": "https://example.org/p.zip", "archive_info": {}}),
    "a commit, but not by git": json.dumps(
        {"url": REPOSITORY, "vcs_info": {"vcs": "hg", "commit_id": RELEASE_REF}}
    ),
}


@pytest.mark.parametrize("record", NOT_THE_RELEASE.values(), ids=NOT_THE_RELEASE)
def test_setup_installs_the_release_over_another_commit(cell_1, record, capsys):  # noqa: F811
    state = cell_1
    state.direct_url = record  # the installed version is 0.8.0, as the notebook's
    state.start(INSTALL)
    (command,) = state.installs
    assert command[-1].endswith(f"@ git+{REPOSITORY}@{RELEASE_REF}")
    out = capsys.readouterr().out
    assert f"morphobricks-preprocessing v0.8.0 ({RELEASE_REF[:12]})" in out
    assert f"(commit {RELEASE_REF})" in out  # what pip installed, read again after it
    assert state.namespace["SETUP_RECORD"]["preprocessing_commit"] == RELEASE_REF


def test_setup_stops_when_the_install_leaves_another_commit(cell_1, monkeypatch):  # noqa: F811
    state = cell_1
    state.direct_url = git_record("2" * 40, REPOSITORY)
    monkeypatch.setattr(subprocess, "run", lambda command, check: state.installs.append(command))
    with pytest.raises(RuntimeError, match=f"not v0.8.0 \\({RELEASE_REF}\\): pip recorded 2222"):
        state.start(INSTALL)
    assert len(state.installs) == 1
    assert state.namespace["SETUP_RECORD"] == {}


def test_setup_uses_a_clean_editable_release_checkout(cell_1, tmp_path, capsys):  # noqa: F811
    state = cell_1
    checkout = checkout_at(tmp_path / "elsewhere")  # not the working folder or its parents
    state.direct_url = editable_record(checkout)
    state.start(INSTALL)
    assert state.installs == []
    out = capsys.readouterr().out
    assert f"local checkout {checkout}" in out
    assert f"(commit {RELEASE_REF})" in out
    assert state.git.calls[0] == ("rev-parse", "--show-toplevel")


@pytest.mark.parametrize(
    ("commit", "dirty", "difference"),
    [
        (RELEASE_REF, True, "it has uncommitted changes"),
        ("2" * 40, False, "its commit is 2222"),
        (None, False, "its commit is unknown"),
    ],
)
def test_setup_refuses_an_editable_checkout_that_is_not_the_clean_release(
    cell_1,  # noqa: F811
    tmp_path,
    commit,
    dirty,
    difference,
):
    # Refused, never replaced: an editable install is a developer's own checkout.
    state = cell_1
    checkout = checkout_at(tmp_path / "elsewhere")
    state.direct_url = editable_record(checkout)
    state.git.commit, state.git.dirty = commit, dirty
    with pytest.raises(RuntimeError, match="is not the release this notebook pins") as stop:
        state.start(INSTALL)
    assert difference in str(stop.value)
    assert "DEVELOPMENT_OVERRIDE" in str(stop.value)
    assert state.installs == []
