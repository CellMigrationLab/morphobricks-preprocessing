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
}


@pytest.mark.parametrize("change", CHANGES)
def test_a_change_in_what_the_notebook_runs_or_installs_needs_a_new_pin(repo, change):
    files, changed = CHANGES[change]
    head = _commit(repo.path, files)
    assert pin_differences(repo.path, repo.pin, head, NOTEBOOK) == changed


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
    "code run from text by builtins": "import builtins\n\nbuiltins.exec('x = 1')\n",
    "a file loaded by its location": (
        "from importlib import util\n\nutil.spec_from_file_location('m', 'm.py')\n"
    ),
    "the package named in a string": "import sys\n\nsys.modules['morphobricks_preprocessing']\n",
    "a program run by subprocess": (
        "import subprocess\n\nsubprocess.run(['morphobricks-preprocessing', 'run'])\n"
    ),
    "a shell command": "import os\n\nos.system('morphobricks-preprocessing run')\n",
    "a relative import": "from . import notebook_cellpose\n",
    "a shell line": "!pip install morphobricks-preprocessing\n",
    "a line magic": "%pip install morphobricks-preprocessing\n",
}


@pytest.mark.parametrize("use", UNSEEN)
def test_a_use_the_check_cannot_follow_is_refused(repo, use):
    with pytest.raises(PinScopeError):  # never a bare SyntaxError for an IPython line
        pin_differences(repo.path, repo.pin, repo.pin, [*NOTEBOOK, UNSEEN[use]])


def test_a_notebook_that_imports_nothing_of_the_package_is_refused(repo):
    with pytest.raises(PinScopeError, match="no cell imports"):
        pin_differences(repo.path, repo.pin, repo.pin, [INSTALL_CELL])


def test_package_code_loading_a_module_by_a_computed_name_is_refused(repo):
    loader = "import importlib\n\n\ndef load(name):\n    return importlib.import_module(name)\n"
    head = _commit(repo.path, {f"{PKG}/model.py": loader})
    with pytest.raises(PinScopeError, match="computed name"):
        pin_differences(repo.path, repo.pin, head, NOTEBOOK)


# (4) a use the review found the check could not follow (each returned [] at c6552c9) ----------

CLI_CHANGED = {f"{PKG}/unused.py": "def other():\n    return 3\n"}
"""A change only the command line module runs (``cli`` imports ``unused``)."""
STARTS_CLI = "['morphobricks-preprocessing', 'run']"
RUNS_UNUSED = """\
import builtins
import runpy
from importlib import util
from pathlib import Path

HERE = Path(__file__)


def load():
    return {}
"""
FORCE_INCLUDE = '\n[{}.force-include]\n"extra/helper.py" = "morphobricks_preprocessing/helper.py"\n'
WITH_PLUGIN = (
    PYPROJECT.replace('["hatchling>=1.27"]', '["hatchling>=1.27", "hatch-plugin==1.0"]')
    + "\n[tool.plugin]\nlevel = 1\n"
)
PIP = (
    "import subprocess\nimport sys\n\n"
    + "subprocess.run([sys.executable, '-m', 'pip', 'install', {}])\n"
)
FORCE = "'--force-reinstall', '--no-deps'"
REPLACE = (
    "from importlib.metadata import distribution\n\n\n"
    "def replace(name, target):\n"
    "    distribution(name)\n"
    "    subprocess.run([sys.executable, '-m', 'pip', 'install', " + FORCE + ", {name}])\n\n\n"
)
"""The real install cell's forced reinstall: a function given the bare distribution name, to
read it back from the metadata, and the pinned requirement, to install; ``{name}`` is the
parameter it passes to pip."""
REFUSED = {
    # A command the cell starts by a name the COMMANDS check did not look at.
    "a command by an aliased subprocess": (
        {},
        CLI_CHANGED,
        [*NOTEBOOK, f"import subprocess as sp\n\nsp.run({STARTS_CLI})\n"],
    ),
    "a command imported from subprocess": (
        {},
        CLI_CHANGED,
        [*NOTEBOOK, f"from subprocess import run\n\nrun({STARTS_CLI})\n"],
    ),
    "a command imported from os": (
        {},
        CLI_CHANGED,
        [*NOTEBOOK, "from os import system\n\nsystem('morphobricks-preprocessing run')\n"],
    ),
    "subprocess bound to another name": (
        {},
        CLI_CHANGED,
        [*NOTEBOOK, f"import subprocess\n\nrunner = subprocess\nrunner.run({STARTS_CLI})\n"],
    ),
    "a command kept to call later": (
        {},
        CLI_CHANGED,
        [*NOTEBOOK, f"import subprocess\n\nstart = subprocess.run\nstart({STARTS_CLI})\n"],
    ),
    "an os.exec call": (
        {},
        CLI_CHANGED,
        [*NOTEBOOK, f"import os\n\nos.execvp('morphobricks-preprocessing', {STARTS_CLI})\n"],
    ),
    "IPython's system": (
        {},
        CLI_CHANGED,
        [*NOTEBOOK, "get_ipython().system('morphobricks-preprocessing run')\n"],
    ),
    # Package code that runs another file without importing it.
    "a file run from its text": (
        {f"{PKG}/model.py": RUNS_UNUSED.format("exec(HERE.with_name('unused.py').read_text())")},
        CLI_CHANGED,
        NOTEBOOK,
    ),
    "a file run by runpy": (
        {f"{PKG}/model.py": RUNS_UNUSED.format("runpy.run_path(HERE.with_name('unused.py'))")},
        CLI_CHANGED,
        NOTEBOOK,
    ),
    "a module run by runpy": (
        {f"{PKG}/model.py": RUNS_UNUSED.format("runpy.run_module(f'{__package__}.unused')")},
        CLI_CHANGED,
        NOTEBOOK,
    ),
    "a file loaded by its location": (
        {
            f"{PKG}/model.py": RUNS_UNUSED.format(
                "util.spec_from_file_location('u', HERE.with_name('unused.py'))"
            )
        },
        CLI_CHANGED,
        NOTEBOOK,
    ),
    "text run by builtins.exec": (
        {f"{PKG}/model.py": RUNS_UNUSED.format("builtins.exec('import os')")},
        CLI_CHANGED,
        NOTEBOOK,
    ),
    # A second top-level package in the wheel, which the closure does not look in.
    "a second package in the wheel": (
        {
            "pyproject.toml": PYPROJECT.replace(
                'packages = ["src/morphobricks_preprocessing"]',
                'packages = ["src/morphobricks_preprocessing", "src/helpers"]',
            ),
            "src/helpers/__init__.py": "VALUE = 1\n",
            f"{PKG}/model.py": "from helpers import VALUE\n\n\ndef load():\n    return VALUE\n",
        },
        {"src/helpers/__init__.py": "VALUE = 2\n"},
        NOTEBOOK,
    ),
    # A build backend whose wheel contents the check cannot read.
    "an unknown build backend": (
        {
            "pyproject.toml": PYPROJECT.replace("hatchling.build", "other.build"),
            "src/helpers/__init__.py": "VALUE = 1\n",
        },
        {"src/helpers/__init__.py": "VALUE = 2\n"},
        NOTEBOOK,
    ),
    # Build inputs outside src/ that put other files in the wheel.
    "a file the wheel force-includes": (
        {
            "pyproject.toml": PYPROJECT + FORCE_INCLUDE.format("tool.hatch.build.targets.wheel"),
            "extra/helper.py": "VALUE = 1\n",
        },
        {"extra/helper.py": "VALUE = 2\n"},
        NOTEBOOK,
    ),
    "a hatch.toml": (
        {"hatch.toml": FORCE_INCLUDE.format("build.targets.wheel"), "extra/helper.py": "A = 1\n"},
        {"extra/helper.py": "A = 2\n"},
        NOTEBOOK,
    ),
    # A cell that installs the package other than at RELEASE_REF, after the pinned install.
    "a second install at a version": (
        {},
        {},
        [*NOTEBOOK, PIP.format("'morphobricks-preprocessing==0.7.0'")],
    ),
    "a second install at a branch": (
        {},
        {},
        [*NOTEBOOK, PIP.format("'morphobricks-preprocessing @ git+https://host/r.git@main'")],
    ),
    "a second install from a variable": (
        {},
        {},
        [*NOTEBOOK, "SOURCE = f'git+{REPOSITORY}@main'\n" + PIP.format("SOURCE")],
    ),
    "an upgrade of what is installed": (
        {},
        {},
        [*NOTEBOOK, "NAME = 'morphobricks-preprocessing'\n" + PIP.format("'--upgrade', NAME")],
    ),
    "an install of the bare name": (
        {},
        {},
        [*NOTEBOOK, "WANTED = ['morphobricks-preprocessing']\n" + PIP.format("*WANTED")],
    ),
    "RELEASE_REF set again": ({}, {}, [*NOTEBOOK, "RELEASE_REF = 'main'\n"]),
    # The forced reinstall (#371 review) keeps the pinned-target rule.
    "a forced reinstall at a branch": (
        {},
        {},
        [
            *NOTEBOOK,
            PIP.format(f"{FORCE}, 'morphobricks-preprocessing @ git+https://h/r.git@main'"),
        ],
    ),
    "a forced reinstall from an unpinned variable": (
        {},
        {},
        [
            *NOTEBOOK,
            "SOURCE = f'morphobricks-preprocessing @ git+{REPOSITORY}@main'\n"
            + PIP.format(f"{FORCE}, SOURCE"),
        ],
    ),
    "a forced reinstall given the bare name": (
        {},
        {},
        [
            *NOTEBOOK,
            REPLACE.format(name="name") + "replace('morphobricks-preprocessing', WANTED)\n",
        ],
    ),
    "an uninstall": (
        {},
        {},
        [
            *NOTEBOOK,
            PIP.replace("'install'", "'uninstall', '-y'").format("'morphobricks-preprocessing'"),
        ],
    ),
}
FOLLOWED = {
    # A build plugin may read any [tool] table, so all of pyproject.toml counts.
    "a [tool] table with a build plugin": (
        {"pyproject.toml": WITH_PLUGIN},
        {"pyproject.toml": WITH_PLUGIN.replace("level = 1", "level = 2")},
        NOTEBOOK,
        ["pyproject.toml"],
    ),
    # Package code that runs a package module by naming it in a string.
    "a package module started by python -m": (
        {
            f"{PKG}/segment.py": "import subprocess\nimport sys\n\n\ndef segment():\n"
            "    command = [sys.executable, '-m', 'morphobricks_preprocessing.cli']\n"
            "    return subprocess.run(command, check=True)\n"
        },
        CLI_CHANGED,
        NOTEBOOK,
        [f"{PKG}/unused.py"],
    ),
    "a package module named in a constant": (
        {f"{PKG}/segment.py": "HELPER = 'morphobricks_preprocessing.cli'\n"},
        CLI_CHANGED,
        NOTEBOOK,
        [f"{PKG}/unused.py"],
    ),
}


def _pin_and_head(repo, pin_files, head_files):
    return _commit(repo.path, pin_files), _commit(repo.path, head_files)


@pytest.mark.parametrize("case", REFUSED)
def test_a_use_the_review_found_is_refused(repo, case):
    pin_files, head_files, cells = REFUSED[case]
    pin, head = _pin_and_head(repo, pin_files, head_files)
    with pytest.raises(PinScopeError):
        pin_differences(repo.path, pin, head, cells)


ACCEPTED = {
    # A method named like a builtin runs no text: Cellpose's model.eval(), torch's eval().
    "a method named eval": [*NOTEBOOK, "model = workflow\nmodel.eval()\n"],
    # The real install cell's forms: the pinned f-string from variables, the repository, and
    # the distribution name read back from the installed metadata.
    "the real install cell's forms": [
        INSTALL_CELL.replace("git+REPOSITORY@", "git+{REPOSITORY}@")
        + 'REPOSITORY = "https://github.com/o/morphobricks-preprocessing.git"\n'
        + "from importlib.metadata import version\n\n"
        + "print(f\"{version('morphobricks-preprocessing')}\")\n",
        *NOTEBOOK[1:],
    ],
    "the real install cell's forced reinstall": [
        INSTALL_CELL
        + REPLACE.format(name="target")
        + "replace('morphobricks-preprocessing', WANTED)\n",
        *NOTEBOOK[1:],
    ],
}


@pytest.mark.parametrize("cells", ACCEPTED.values(), ids=list(ACCEPTED))
def test_a_cell_use_the_check_follows_is_accepted(repo, cells):
    assert pin_differences(repo.path, repo.pin, repo.pin, cells) == []


@pytest.mark.parametrize("case", FOLLOWED)
def test_a_use_the_review_found_is_followed(repo, case):
    pin_files, head_files, cells, changed = FOLLOWED[case]
    pin, head = _pin_and_head(repo, pin_files, head_files)
    assert pin_differences(repo.path, pin, head, cells) == changed


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
