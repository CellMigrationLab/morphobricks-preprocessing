"""The Cellpose notebook's pin check covers exactly the code the notebook uses (issue #421).

``test_notebook_cellpose.py`` asks :func:`notebook_pin.pin_differences` whether anything the
notebook runs changed between its ``RELEASE_REF`` and ``HEAD``. Here the check runs on small
Git repositories made for each test: a package laid out like this one, a commit ``A`` (the
pin) and a commit ``B`` (``HEAD``) that changes one thing.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from notebook_pin import (
    PinScopeError,
    code_sources,
    history_missing,
    install_setting,
    notebook_modules,
    pin_differences,
    tree_blobs,
    used_files,
)

ROOT = Path(__file__).parents[1]
PKG = "src/morphobricks_preprocessing"
PYPROJECT = """\
[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[project]
name = "morphobricks-preprocessing"
version = "0.8.0"
dependencies = ["numpy>=2.0"]

[project.optional-dependencies]
notebook = ["ipywidgets>=7.7,<9"]

[tool.hatch.build.targets.wheel]
packages = ["src/morphobricks_preprocessing"]

[tool.ruff]
line-length = 100

[tool.morphobricks.code-rules.layers]
"morphobricks_preprocessing.notebook_cellpose" = "ui"
"""
TREE = {
    "pyproject.toml": PYPROJECT,
    ".gitignore": "__pycache__/\n",
    f"{PKG}/__init__.py": "from .core import shared\n",
    f"{PKG}/core.py": "def shared():\n    return 1\n",
    f"{PKG}/notebook_cellpose.py": (
        "from .segment import segment\n\n\n"
        "class CellposeWorkflow:\n    def run(self):\n        return segment()\n"
    ),
    # Imported inside a function, as segmentation_cellpose imports its model module.
    f"{PKG}/segment.py": "def segment():\n    from .model import load\n\n    return load()\n",
    f"{PKG}/model.py": "def load():\n    return 'weights'\n",
    f"{PKG}/unused.py": "def other():\n    return 2\n",
    f"{PKG}/cli.py": "from .unused import other\n",
}
INSTALL_CELL = """\
import subprocess
import sys

RELEASE_REF = "0" * 40
WANTED = f"morphobricks-preprocessing[notebook] @ git+REPOSITORY@{RELEASE_REF}"
subprocess.run([sys.executable, "-m", "pip", "install", WANTED], check=True)
"""
OPEN_CELL = """\
from morphobricks_preprocessing.notebook_cellpose import CellposeWorkflow

workflow = CellposeWorkflow()
"""
NOTEBOOK = [INSTALL_CELL, OPEN_CELL, "workflow.run()\n"]


def _git(repo, *arguments):
    identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid"]
    command = ["git", "-C", str(repo), *identity, "-c", "commit.gpgsign=false", *arguments]
    return subprocess.run(command, capture_output=True, check=True, text=True).stdout.strip()


def _write(repo, files):
    for name, text in files.items():
        path = repo / name
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def _commit(repo, files):
    _write(repo, files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "change")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    """A Git repository (``repo.path``) whose first commit (``repo.pin``) holds ``TREE``."""
    _git(tmp_path, "init", "-q")
    return SimpleNamespace(path=tmp_path, pin=_commit(tmp_path, TREE))


def _whole_tree_rule(repo, ref, head):
    """The rule before #421: any change under src/ or in pyproject.toml needs a new pin."""
    command = ["git", "-C", str(repo), "diff", "--quiet", ref, head, "--", "src", "pyproject.toml"]
    return subprocess.run(command, capture_output=True).returncode


# (1) a change the notebook does not run needs no new pin -----------------------------------


def test_a_change_in_modules_the_notebook_does_not_import_needs_no_new_pin(repo):
    head = _commit(
        repo.path,
        {
            f"{PKG}/unused.py": "def other():\n    return 3\n",
            f"{PKG}/cli.py": "from .unused import other\n\nMAIN = other\n",
            f"{PKG}/new_ui.py": "VALUE = 1\n",
            # The layers table of the code rules and Ruff: no build and no package code reads them.
            "pyproject.toml": PYPROJECT.replace("line-length = 100", "line-length = 99")
            + '"morphobricks_preprocessing.new_ui" = "ui"\n',
        },
    )
    assert _whole_tree_rule(repo.path, repo.pin, head) == 1  # the old rule asked for a new pin
    assert pin_differences(repo.path, repo.pin, head, NOTEBOOK) == []


# (2) a change in what the notebook runs or installs still needs a new pin --------------------


CHANGES = {
    "the class the notebook calls": (
        {f"{PKG}/notebook_cellpose.py": TREE[f"{PKG}/notebook_cellpose.py"] + "\nEXTRA = 1\n"},
        [f"{PKG}/notebook_cellpose.py"],
    ),
    "a function it calls, imported inside a function two modules down": (
        {f"{PKG}/model.py": "def load():\n    return 'other weights'\n"},
        [f"{PKG}/model.py"],
    ),
    "the package's __init__, which every import runs": (
        {f"{PKG}/__init__.py": "from .core import shared\n\nREADY = True\n"},
        [f"{PKG}/__init__.py"],
    ),
    "a module the __init__ imports": (
        {f"{PKG}/core.py": "def shared():\n    return 2\n"},
        [f"{PKG}/core.py"],
    ),
    "a module a used module starts importing": (
        {
            f"{PKG}/segment.py": "from .unused import other\n\n" + TREE[f"{PKG}/segment.py"],
            f"{PKG}/unused.py": "def other():\n    return 3\n",
        },
        [f"{PKG}/segment.py", f"{PKG}/unused.py"],
    ),
    "a used module removed": (
        {f"{PKG}/model.py": None},
        [f"{PKG}/model.py"],
    ),
    "a data file of the package": ({f"{PKG}/table.json": "{}\n"}, [f"{PKG}/table.json"]),
    "a dependency": (
        {"pyproject.toml": PYPROJECT.replace("numpy>=2.0", "numpy>=2.1")},
        ["pyproject.toml"],
    ),
    "an extra the notebook installs": (
        {"pyproject.toml": PYPROJECT.replace("<9", "<10")},
        ["pyproject.toml"],
    ),
    "the version the install cell records": (
        {"pyproject.toml": PYPROJECT.replace("0.8.0", "0.8.1")},
        ["pyproject.toml"],
    ),
    "the build backend's own table": (
        {"pyproject.toml": PYPROJECT + "\n[tool.hatch.metadata]\nallow-direct-references = true\n"},
        ["pyproject.toml"],
    ),
    "the files the build leaves out": ({".gitignore": "*.json\n"}, [".gitignore"]),
    "a hatch.toml added": ({"hatch.toml": "[build]\n"}, ["hatch.toml"]),
}


@pytest.mark.parametrize("change", CHANGES)
def test_a_change_in_what_the_notebook_runs_or_installs_needs_a_new_pin(repo, change):
    files, changed = CHANGES[change]
    head = _commit(repo.path, files)
    assert pin_differences(repo.path, repo.pin, head, NOTEBOOK) == changed


def test_an_unknown_build_backend_compares_all_of_pyproject(repo):
    unknown = PYPROJECT.replace("hatchling.build", "other.build")
    pin = _commit(repo.path, {"pyproject.toml": unknown})
    head = _commit(repo.path, {"pyproject.toml": unknown.replace("= 100", "= 99")})
    assert pin_differences(repo.path, pin, head, NOTEBOOK) == ["pyproject.toml"]


# (3) what the notebook uses is read from its cells ------------------------------------------


def test_a_module_the_notebook_starts_importing_is_compared(repo):
    head = _commit(repo.path, {f"{PKG}/unused.py": "def other():\n    return 3\n"})
    assert pin_differences(repo.path, repo.pin, head, NOTEBOOK) == []
    # The same commits, with a cell that now also imports the command line module.
    calls_cli = [*NOTEBOOK, "from morphobricks_preprocessing.cli import other\n\nother()\n"]
    assert pin_differences(repo.path, repo.pin, head, calls_cli) == [f"{PKG}/unused.py"]


def test_a_module_that_exists_only_at_head_is_compared(repo):
    head = _commit(
        repo.path,
        {
            f"{PKG}/fresh.py": "VALUE = 1\n",
            f"{PKG}/notebook_cellpose.py": "from .fresh import VALUE\n"
            + TREE[f"{PKG}/notebook_cellpose.py"],
        },
    )
    assert pin_differences(repo.path, repo.pin, head, NOTEBOOK) == [
        f"{PKG}/fresh.py",
        f"{PKG}/notebook_cellpose.py",
    ]


UNSEEN = {
    "a module loaded by name": (
        "import importlib\n\nimportlib.import_module('morphobricks_preprocessing.cli')\n"
    ),
    "__import__": "name = 'cli'\n__import__(name)\n",
    "code run from text": "exec(open('cell.py').read())\n",
    "the package named in a string": "import sys\n\nsys.modules['morphobricks_preprocessing']\n",
    "a program run by subprocess": (
        "import subprocess\n\nsubprocess.run(['morphobricks-preprocessing', 'run'])\n"
    ),
    "a shell command": "import os\n\nos.system('morphobricks-preprocessing run')\n",
    "a relative import": "from . import notebook_cellpose\n",
}


@pytest.mark.parametrize("use", UNSEEN)
def test_a_use_the_check_cannot_follow_is_refused(repo, use):
    with pytest.raises(PinScopeError):
        pin_differences(repo.path, repo.pin, repo.pin, [*NOTEBOOK, UNSEEN[use]])


def test_a_notebook_that_imports_nothing_of_the_package_is_refused(repo):
    with pytest.raises(PinScopeError, match="no cell imports"):
        pin_differences(repo.path, repo.pin, repo.pin, [INSTALL_CELL])


def test_package_code_loading_a_module_by_a_computed_name_is_refused(repo):
    loader = "import importlib\n\n\ndef load(name):\n    return importlib.import_module(name)\n"
    head = _commit(repo.path, {f"{PKG}/model.py": loader})
    with pytest.raises(PinScopeError, match="computed name"):
        pin_differences(repo.path, repo.pin, head, NOTEBOOK)


# the real notebook --------------------------------------------------------------------------


def test_the_real_notebook_runs_the_cellpose_code_and_not_the_preprocessing_app():
    sources = code_sources(ROOT / "notebooks/MorphoBricks_Cellpose.ipynb")
    assert "morphobricks_preprocessing.notebook_cellpose" in notebook_modules(sources)
    assert install_setting(sources[0], "CELLPOSE_VERSION") == "4.2.1.1"
    try:
        blobs = tree_blobs(ROOT, "HEAD")
    except (OSError, subprocess.CalledProcessError):
        history_missing("this checkout is not a Git repository")
    used = used_files(ROOT, blobs, notebook_modules(sources))
    for module in ("__init__", "notebook_cellpose", "segmentation_cellpose", "run_inputs"):
        assert f"{PKG}/{module}.py" in used
    assert f"{PKG}/segmentation_cellpose_model.py" in used  # imported inside a function
    for module in ("notebook_ui", "cli", "notebook_stepper"):  # the other notebook, the CLI
        assert f"{PKG}/{module}.py" not in used


@pytest.mark.parametrize("ci", ["", "true"])
def test_a_missing_history_skips_locally_and_fails_in_ci(monkeypatch, ci):
    monkeypatch.setenv("CI", ci)
    outcome = pytest.fail.Exception if ci else pytest.skip.Exception
    with pytest.raises(outcome, match="not in this checkout"):
        history_missing("the pinned commit is not in this checkout")
