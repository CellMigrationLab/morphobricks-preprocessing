"""The pin check of the combined notebook (#441): the Preprocessing cells, with ``checkout=True``.

``MorphoBricks_Preprocessing_Cellpose.ipynb`` shares the Preprocessing notebook's install and
app cells, which the strict check (the Cellpose notebook's) refuses: a nearby checkout is
recognised by its ``src/morphobricks_preprocessing/__init__.py``, verified with read-only
``git`` questions and installed editable, and the app is loaded by
``import_module("morphobricks_preprocessing.notebook_ui")``. With ``checkout=True`` these are
followed, and everything else stays refused. The small repositories are those of
``test_notebook_cellpose_pin.py``.
"""

from pathlib import Path

import pytest

from notebook_pin import PinScopeError, code_sources, notebook_modules, pin_differences
from test_notebook_cellpose_pin import CLI_CHANGED, NOTEBOOK, PIP, _commit, repo  # noqa: F401

ROOT = Path(__file__).parents[1]
PREPROCESSING = code_sources(ROOT / "notebooks/MorphoBricks_Preprocessing.ipynb")
GIT = (
    "import subprocess\n\n\n"
    "def _git(folder, *arguments):\n"
    "    return subprocess.check_output(['git', '-C', str(folder), *arguments], text=True)\n\n\n"
    "_git('.', {})\n"
)


def test_the_preprocessing_cells_are_refused_strictly_and_followed_with_checkout():
    with pytest.raises(PinScopeError):
        notebook_modules(PREPROCESSING)
    used = notebook_modules(PREPROCESSING, checkout=True)
    assert "morphobricks_preprocessing.notebook_ui" in used  # import_module of a literal name


def test_a_module_loaded_by_a_literal_name_is_compared(repo):  # noqa: F811
    loads = [
        *NOTEBOOK,
        "import importlib\n\nimportlib.import_module('morphobricks_preprocessing.cli')\n",
    ]
    head = _commit(repo.path, CLI_CHANGED)  # cli imports unused
    assert pin_differences(repo.path, repo.pin, head, loads, checkout=True) == [
        "src/morphobricks_preprocessing/unused.py"
    ]


ACCEPTED = {
    "the checkout's identity questions": GIT.format("'rev-parse', '--show-toplevel'"),
    "its tree state": GIT.format("'status', '--porcelain'"),
    "an editable install": PIP.format("'-e', f'{folder}[notebook]'"),
    "a message naming the requirement": "print(f'morphobricks-preprocessing {RELEASE_REF[:12]}')\n",
}


@pytest.mark.parametrize("cell", ACCEPTED.values(), ids=list(ACCEPTED))
def test_what_the_checkout_cell_does_is_accepted_with_checkout_only(repo, cell):  # noqa: F811
    cells = [*NOTEBOOK, cell]
    assert pin_differences(repo.path, repo.pin, repo.pin, cells, checkout=True) == []
    with pytest.raises(PinScopeError):
        pin_differences(repo.path, repo.pin, repo.pin, cells)


REFUSED = {
    "git that changes the checkout": GIT.format("'checkout', 'main'"),
    "git words from a list": GIT.replace("*arguments", "*arguments, 'reset'").format("'HEAD'"),
    "a module loaded by a computed name": (
        "import importlib\n\nname = 'cli'\n"
        "importlib.import_module(f'morphobricks_preprocessing.{name}')\n"
    ),
    "an unpinned install": PIP.format("f'morphobricks-preprocessing @ git+{REPOSITORY}@main'"),
    "an editable install of a named package": PIP.format("'-e', 'morphobricks-preprocessing'"),
    "the package as a pip word": PIP.format("'morphobricks_preprocessing'"),
    "another command": (
        "import subprocess\n\nsubprocess.run(['morphobricks-preprocessing', 'run'])\n"
    ),
}


@pytest.mark.parametrize("cell", REFUSED.values(), ids=list(REFUSED))
def test_everything_else_stays_refused_with_checkout(repo, cell):  # noqa: F811
    with pytest.raises(PinScopeError):
        pin_differences(repo.path, repo.pin, repo.pin, [*NOTEBOOK, cell], checkout=True)


def test_words_passed_on_by_star_args_reach_pip(repo):  # noqa: F811
    # The arguments of a cell function's *args were not followed before #441.
    cell = (
        "import subprocess\nimport sys\n\n\n"
        "def install(*words):\n"
        "    subprocess.run([sys.executable, '-m', 'pip', 'install', *words])\n\n\n"
        "install('--upgrade')\n"
    )
    with pytest.raises(PinScopeError, match="--upgrade"):
        pin_differences(repo.path, repo.pin, repo.pin, [*NOTEBOOK, cell])
