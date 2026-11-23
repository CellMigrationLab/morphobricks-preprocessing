"""The Cellpose pin check after the #422 re-review (issue #423).

Each case is a use the check missed or refused wrongly before #423, run on the small Git
repositories of ``test_notebook_cellpose_pin.py``. The check is a drift detector, not a
security boundary: these are uses a legitimate edit can make, not deliberate obfuscation.
"""

import pytest

import test_notebook_cellpose_pin as pin_tests
from notebook_pin import PinScopeError, pin_differences
from test_notebook_cellpose_pin import CLI_CHANGED, NOTEBOOK, PIP, PKG, PYPROJECT, _pin_and_head

repo = pin_tests.repo
"""The fixture: a Git repository whose first commit holds the small package."""

LOADS_IN_MODEL = "{}\n\n\ndef load():\n    return {}\n"
"""``model.py`` (which the notebook runs) with an import line and the body of ``load()``."""
REPLACE = (
    "from importlib.metadata import distribution\n\n\n"
    "def replace(name, target):\n"
    "    distribution(name)\n"
    "    {}\n"
    "    subprocess.run([sys.executable, '-m', 'pip', 'install', '--force-reinstall', {}])\n\n\n"
)
"""A cell function like the real install cell's forced reinstall, given the bare distribution
name to read it back from the metadata, with one line that copies it and the word ``pip`` is
given in the pinned requirement's place."""
REPLACES_BARE_NAME = "replace('morphobricks-preprocessing', WANTED)\n"
REFUSED = {
    # (1) a loader reached by an alias or getattr, in a cell and in package code.
    "a cell's aliased import_module": (
        {},
        [*NOTEBOOK, "from importlib import import_module as load\n\nload('json')\n"],
    ),
    "a cell's aliased runpy loader": (
        {},
        [*NOTEBOOK, "from runpy import run_path as run_file\n\nrun_file('m.py')\n"],
    ),
    "a cell's getattr on importlib": (
        {},
        [*NOTEBOOK, "import importlib\n\ngetattr(importlib, 'import_module')('json')\n"],
    ),
    "package code's aliased import_module": (
        {
            f"{PKG}/model.py": LOADS_IN_MODEL.format(
                "from importlib import import_module as find", "find('json')"
            )
        },
        NOTEBOOK,
    ),
    "package code's aliased file loader": (
        {
            f"{PKG}/model.py": LOADS_IN_MODEL.format(
                "from importlib.util import spec_from_file_location as locate",
                "locate('u', 'unused.py')",
            )
        },
        NOTEBOOK,
    ),
    "package code's getattr on importlib": (
        {
            f"{PKG}/model.py": LOADS_IN_MODEL.format(
                "import importlib", "getattr(importlib, 'import_module')('json')"
            )
        },
        NOTEBOOK,
    ),
    # (3) a word, or the bare distribution name a cell function is given, that reaches pip
    # through a list built elsewhere or a copy.
    "the bare name a cell function passes to pip in a list": (
        {},
        [*NOTEBOOK, REPLACE.format("wanted = [name]", "*wanted") + REPLACES_BARE_NAME],
    ),
    "the bare name a cell function passes to pip in a copy": (
        {},
        [*NOTEBOOK, REPLACE.format("source = name", "source") + REPLACES_BARE_NAME],
    ),
    "a pip word added to a list elsewhere": (
        {},
        [*NOTEBOOK, "OPTIONS = ['--editable']\nOPTIONS.append('.')\n" + PIP.format("*OPTIONS")],
    ),
}


@pytest.mark.parametrize("case", REFUSED)
def test_a_use_the_re_review_found_is_refused(repo, case):
    head_files, cells = REFUSED[case]
    pin, head = _pin_and_head(repo, head_files, {})
    with pytest.raises(PinScopeError):
        pin_differences(repo.path, pin, head, cells)


FOLLOWED = {
    # (2) a package object named in the "module:attribute" form (pkgutil.resolve_name, an
    # entry point): the module before the colon is followed like any dotted name.
    "a module:attribute string resolve_name is given": {
        f"{PKG}/segment.py": "import pkgutil\n\n\ndef segment():\n"
        "    return pkgutil.resolve_name('morphobricks_preprocessing.cli:other')()\n"
    },
    "a module:attribute string kept in a constant": {
        f"{PKG}/segment.py": "MAIN = 'morphobricks_preprocessing.cli:other'\n"
    },
}


@pytest.mark.parametrize("case", FOLLOWED)
def test_a_use_the_re_review_found_is_followed(repo, case):
    pin, head = _pin_and_head(repo, FOLLOWED[case], CLI_CHANGED)
    assert pin_differences(repo.path, pin, head, NOTEBOOK) == [f"{PKG}/unused.py"]


# (4) build settings that do not add files to the wheel are compared or ignored, not refused.
HATCH_OTHER = (
    '\n[tool.hatch.build.targets.sdist]\ninclude = ["src", "docs"]\n'
    '\n[tool.hatch.envs.default]\ndependencies = ["pytest"]\n'
)
EXCLUDE = '\n[tool.hatch.build]\nexclude = ["*.json"]\n'
BUILD_SETTINGS = {
    "the sdist target and Hatch environments, ignored": (
        PYPROJECT + HATCH_OTHER,
        PYPROJECT + HATCH_OTHER.replace('"docs"', '"tests"').replace("pytest", "pytest>=8"),
        [],
    ),
    "the build's exclude, the same at both commits": (
        PYPROJECT + EXCLUDE,
        PYPROJECT + EXCLUDE,
        [],
    ),
    "the build's exclude, changed": (
        PYPROJECT + EXCLUDE,
        PYPROJECT + EXCLUDE.replace("*.json", "*.txt"),
        ["pyproject.toml"],
    ),
}


@pytest.mark.parametrize("case", BUILD_SETTINGS)
def test_a_build_setting_that_adds_no_file_is_not_refused(repo, case):
    pin_text, head_text, changed = BUILD_SETTINGS[case]
    pin, head = _pin_and_head(repo, {"pyproject.toml": pin_text}, {"pyproject.toml": head_text})
    assert pin_differences(repo.path, pin, head, NOTEBOOK) == changed
