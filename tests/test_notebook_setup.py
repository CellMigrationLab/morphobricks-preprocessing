"""Exercise the standalone setup cell without installing into the test environment."""

import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

NOTEBOOK = Path(__file__).parents[1] / "notebooks/MorphoBricks_Preprocessing.ipynb"
REQUIRED = {
    "morphobricks_preprocessing",
    "morphobricks_normalization",
    "numpy",
    "tifffile",
    "ipywidgets",
    "matplotlib",
}


@pytest.fixture
def setup_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.delitem(sys.modules, "morphobricks_preprocessing", raising=False)
    state = SimpleNamespace(available=REQUIRED.copy(), installs=[], welcomes=[], imports=[])

    def find_spec(name):
        return object() if name in state.available else None

    def install(command, *, check):
        assert check is True
        state.installs.append(command)
        state.available.update(REQUIRED)

    def import_interface(name):
        state.imports.append(name)
        return SimpleNamespace(
            PreprocessingNotebook=lambda: SimpleNamespace(
                welcome=lambda: state.welcomes.append("ready")
            )
        )

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    monkeypatch.setattr(importlib, "import_module", import_interface)
    monkeypatch.setattr(subprocess, "run", install)

    def start():
        notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
        source = next(c["source"] for c in notebook["cells"] if c["cell_type"] == "code")
        namespace = {}
        exec("".join(source), namespace)
        return namespace

    state.start = start
    return state


def checkout_at(path):
    package = path / "src/morphobricks_preprocessing"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (path / "pyproject.toml").write_text("", encoding="utf-8")
    return path


def test_setup_reuses_installed_package_without_installing(setup_environment, capsys):
    state = setup_environment
    state.start()
    assert state.installs == []
    assert state.imports == ["morphobricks_preprocessing.notebook_ui"]
    assert state.welcomes == ["ready"]
    assert "Using the installed package" in capsys.readouterr().out


def test_setup_installs_missing_package_from_github_in_this_kernel(setup_environment, capsys):
    state = setup_environment
    state.available.clear()
    state.start()
    (command,) = state.installs
    assert command[:5] == [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    assert command[5:] == [
        "morphobricks-preprocessing[notebook] @ "
        "git+https://github.com/CellMigrationLab/morphobricks-preprocessing.git"
    ]
    assert state.welcomes == ["ready"]
    assert "Installing notebook requirements" in capsys.readouterr().out


def test_setup_repairs_missing_notebook_dependencies(setup_environment):
    state = setup_environment
    state.available.remove("ipywidgets")
    state.start()
    assert state.installs[0][-1] == "morphobricks-preprocessing[notebook]"
    assert state.welcomes == ["ready"]


def test_setup_installs_the_normalization_brick_into_an_older_checkout_environment(
    setup_environment, tmp_path, monkeypatch, capsys
):
    # Environments set up before preprocessing depended on morphobricks-normalization.
    state = setup_environment
    checkout = checkout_at(tmp_path / "checkout")
    monkeypatch.chdir(checkout)
    state.available.remove("morphobricks_normalization")
    state.start()
    assert (
        "Installing notebook requirements (morphobricks_normalization)" in capsys.readouterr().out
    )
    assert state.installs[0][-2:] == ["-e", f"{checkout}[notebook]"]
    assert state.welcomes == ["ready"]


@pytest.mark.parametrize("missing_dependencies", [False, True])
def test_setup_prefers_nearby_checkout_and_can_install_its_extras(
    setup_environment, tmp_path, monkeypatch, missing_dependencies, capsys
):
    state = setup_environment
    checkout = checkout_at(tmp_path / "a checkout with spaces")
    notebooks = checkout / "notebooks"
    notebooks.mkdir()
    monkeypatch.chdir(notebooks)
    if missing_dependencies:
        state.available.remove("matplotlib")
    namespace = state.start()
    assert sys.path[0] == str(checkout / "src")
    assert "Using the local checkout" in capsys.readouterr().out
    if missing_dependencies:
        assert state.installs[0][-2:] == ["-e", f"{checkout}[notebook]"]
    else:
        assert state.installs == []
    namespace["_start_preprocessing_notebook"]()
    assert sys.path.count(str(checkout / "src")) == 1


def test_setup_does_not_mix_an_already_loaded_package_with_another_checkout(
    setup_environment, tmp_path, monkeypatch
):
    state = setup_environment
    checkout = checkout_at(tmp_path / "checkout")
    monkeypatch.chdir(checkout)
    monkeypatch.setitem(
        sys.modules,
        "morphobricks_preprocessing",
        SimpleNamespace(__file__=str(tmp_path / "another_copy/__init__.py")),
    )
    with pytest.raises(RuntimeError, match="already loaded another copy"):
        state.start()
    assert state.installs == state.imports == state.welcomes == []


def test_setup_moves_an_existing_source_path_to_the_front(setup_environment, tmp_path, monkeypatch):
    checkout = checkout_at(tmp_path / "checkout")
    source = str(checkout / "src")
    monkeypatch.chdir(checkout)
    sys.path.extend([source, source])
    setup_environment.start()
    assert sys.path[0] == source
    assert sys.path.count(source) == 1


def test_setup_reports_colab_without_importing_or_mounting_drive(
    setup_environment, monkeypatch, capsys
):
    monkeypatch.setitem(sys.modules, "google.colab", SimpleNamespace())
    setup_environment.start()
    assert "Checking the Google Colab environment" in capsys.readouterr().out
    assert setup_environment.installs == []


def test_setup_rejects_old_python_before_installing(setup_environment, monkeypatch):
    monkeypatch.setattr(sys, "version_info", (3, 10, 0))
    with pytest.raises(RuntimeError, match="Python 3.11 or newer"):
        setup_environment.start()
    assert setup_environment.installs == setup_environment.imports == []


def test_setup_reports_failed_installation_without_showing_ready(setup_environment, monkeypatch):
    state = setup_environment
    state.available.clear()

    def fail(command, *, check):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="could not install"):
        state.start()
    assert state.imports == state.welcomes == []


def test_setup_requests_restart_if_installed_packages_are_still_unavailable(
    setup_environment, monkeypatch
):
    state = setup_environment
    state.available.clear()
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match="Restart the kernel"):
        state.start()
    assert state.imports == state.welcomes == []


def test_setup_reports_import_errors_without_reinstalling(setup_environment, monkeypatch):
    state = setup_environment

    def broken(name):
        raise ImportError("broken binary dependency")

    monkeypatch.setattr(importlib, "import_module", broken)
    with pytest.raises(RuntimeError, match="interface could not be loaded") as excinfo:
        state.start()
    assert str(excinfo.value.__cause__) == "broken binary dependency"
    assert state.installs == state.welcomes == []
