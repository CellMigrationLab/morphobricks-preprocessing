"""The pin check of the combined notebook (#441): the Preprocessing cells, with ``checkout=3``.

``MorphoBricks_Preprocessing_Cellpose.ipynb`` shares the Preprocessing notebook's install and
app cells, which the strict check (the Cellpose notebook's) refuses: a nearby checkout is
recognised by its ``src/morphobricks_preprocessing/__init__.py``, verified with read-only
``git`` questions and installed editable, and the app is loaded by
``import_module("morphobricks_preprocessing.notebook_ui")``. With ``checkout=N`` these are
followed in the first N cells only (the shared ones; 3 in the combined notebook), and
everything else stays refused. The small repositories are those of
``test_notebook_cellpose_pin.py``; a checkout cell is put first, with ``checkout=1``.
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
    used = notebook_modules(PREPROCESSING, checkout=len(PREPROCESSING))
    assert "morphobricks_preprocessing.notebook_ui" in used  # import_module of a literal name


def test_a_module_loaded_by_a_literal_name_is_compared(repo):  # noqa: F811
    loads = [
        "import importlib\n\nimportlib.import_module('morphobricks_preprocessing.cli')\n",
        *NOTEBOOK,
    ]
    head = _commit(repo.path, CLI_CHANGED)  # cli imports unused
    assert pin_differences(repo.path, repo.pin, head, loads, checkout=1) == [
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
    cells = [cell, *NOTEBOOK]
    assert pin_differences(repo.path, repo.pin, repo.pin, cells, checkout=1) == []
    with pytest.raises(PinScopeError):
        pin_differences(repo.path, repo.pin, repo.pin, cells)


@pytest.mark.parametrize("cell", ACCEPTED.values(), ids=list(ACCEPTED))
def test_the_checkout_allowances_apply_only_to_the_shared_cells(repo, cell):  # noqa: F811
    # Before (#463 review), checkout=True allowed them in every cell, cells 4 and 5 included.
    with pytest.raises(PinScopeError):
        pin_differences(repo.path, repo.pin, repo.pin, [*NOTEBOOK, cell], checkout=1)


REFUSED = {
    "git that changes the checkout": GIT.format("'checkout', 'main'"),
    "git words from a list": GIT.replace("*arguments", "*arguments, 'reset'").format("'HEAD'"),
    "a module loaded by a computed name": (
        "import importlib\n\nname = 'cli'\n"
        "importlib.import_module(f'morphobricks_preprocessing.{name}')\n"
    ),
    "an unpinned install": PIP.format("f'morphobricks-preprocessing @ git+{REPOSITORY}@main'"),
    # Added after the #463 review: each was accepted with checkout=True before.
    "a computed git word": GIT.format("'rev-' + 'parse', 'HEAD'"),
    "a git word from a call": GIT.format("'status'.upper()"),
    "an @main f-string returned by a function": (
        "def target():\n    return f'morphobricks-preprocessing @ git+{REPOSITORY}@main'\n\n\n"
        + PIP.format("target()")
    ),
    "an @ source f-string returned by a function": (
        "def target(name):\n    return f'{name} @ file:///tmp/checkout'\n\n\n"
        + PIP.format("target('x')")
    ),
    "an editable install of a named package": PIP.format("'-e', 'morphobricks-preprocessing'"),
    "the package as a pip word": PIP.format("'morphobricks_preprocessing'"),
    "another command": (
        "import subprocess\n\nsubprocess.run(['morphobricks-preprocessing', 'run'])\n"
    ),
}


@pytest.mark.parametrize("cell", REFUSED.values(), ids=list(REFUSED))
def test_everything_else_stays_refused_with_checkout(repo, cell):  # noqa: F811
    with pytest.raises(PinScopeError):
        pin_differences(repo.path, repo.pin, repo.pin, [cell, *NOTEBOOK], checkout=1)


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


NAMES = {
    "set twice": "notebook_name = 'MorphoBricks_Preprocessing'\nnotebook_name = 'Other'\n",
    "an install source as its value": (
        "notebook_name = 'morphobricks-preprocessing @ git+https://example.invalid/x@main'\n"
    ),
    "a computed value": "notebook_name = 'MorphoBricks_' + 'Preprocessing'\n",
}


@pytest.mark.parametrize("cell", NAMES.values(), ids=list(NAMES))
def test_notebook_name_is_one_plain_name_set_once(repo, cell):  # noqa: F811
    # notebook_name may name the package (NAMED_ONCE): it is counted as RELEASE_REF is, and
    # its value is a plain name (letters, digits, underscores), never an install source.
    with pytest.raises(PinScopeError, match="notebook_name"):
        pin_differences(repo.path, repo.pin, repo.pin, [cell, *NOTEBOOK], checkout=1)
    good = "notebook_name = 'MorphoBricks_Preprocessing_Cellpose'\n"
    assert pin_differences(repo.path, repo.pin, repo.pin, [good, *NOTEBOOK], checkout=1) == []
