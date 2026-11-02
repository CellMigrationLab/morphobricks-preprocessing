"""Exercise the standalone setup cell without installing into the test environment."""

import importlib
import importlib.metadata
import importlib.util
import json
import platform
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
ZARR = "zarr"  # required on Colab only, where Step 0 installs the zarr extra
GITHUB = "@ git+https://github.com/CellMigrationLab/morphobricks-preprocessing.git@"


@pytest.fixture
def setup_environment(tmp_path, monkeypatch):
    # Step 0 prints platform.platform(), which may run `uname -p` through subprocess the
    # first time; computing (and caching) it now keeps that away from the faked pip below.
    platform.platform()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.delitem(sys.modules, "morphobricks_preprocessing", raising=False)
    state = SimpleNamespace(available={*REQUIRED, ZARR}, installs=[], welcomes=[], imports=[])

    def find_spec(name):
        return object() if name in state.available else None

    def install(command, *, check):
        assert check is True
        state.installs.append(command)
        state.available.update({*REQUIRED, ZARR})

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
        state.namespace = {}
        exec("".join(source), state.namespace)
        return state.namespace

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
    namespace = state.namespace
    assert command[5:] == [
        "morphobricks-preprocessing[notebook] @ "
        "git+https://github.com/CellMigrationLab/morphobricks-preprocessing.git"
        f"@{namespace['RELEASE_TAG']}"
    ]
    assert state.welcomes == ["ready"]
    out = capsys.readouterr().out
    assert "Installing notebook requirements" in out
    assert f"Using release {namespace['RELEASE_TAG']} from GitHub." in out
    assert f"MorphoBricks_Preprocessing {namespace['current_version']}" in out


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


def test_setup_mounts_drive_and_enables_the_widget_manager_on_colab(
    setup_environment, monkeypatch, capsys
):
    calls = []
    colab = SimpleNamespace(
        drive=SimpleNamespace(mount=lambda path: calls.append(("mount", path))),
        output=SimpleNamespace(enable_custom_widget_manager=lambda: calls.append("widgets")),
    )
    monkeypatch.setitem(sys.modules, "google.colab", colab)
    setup_environment.start()
    assert calls == [("mount", "/content/drive"), "widgets"]
    out = capsys.readouterr().out
    assert "connecting Google Drive" in out and "Checking the Google Colab environment" in out
    assert "Local Jupyter" not in out
    assert setup_environment.welcomes == ["ready"]


def colab(monkeypatch):
    fake = SimpleNamespace(
        drive=SimpleNamespace(mount=lambda path: None),
        output=SimpleNamespace(enable_custom_widget_manager=lambda: None),
    )
    monkeypatch.setitem(sys.modules, "google.colab", fake)


def test_colab_installs_the_release_with_the_zarr_extra(setup_environment, monkeypatch, capsys):
    colab(monkeypatch)
    state = setup_environment
    state.available.clear()
    namespace = state.start()
    (command,) = state.installs
    assert command[5:] == [
        f"morphobricks-preprocessing[notebook,zarr] {GITHUB}{namespace['RELEASE_TAG']}"
    ]
    assert state.welcomes == ["ready"]


def test_colab_adds_zarr_to_an_installed_package(setup_environment, monkeypatch, capsys):
    colab(monkeypatch)
    state = setup_environment
    state.available.remove(ZARR)
    state.start()
    assert state.installs[0][5:] == ["morphobricks-preprocessing[notebook,zarr]"]
    assert "Installing notebook requirements (zarr)" in capsys.readouterr().out


def test_locally_zarr_is_neither_required_nor_installed(setup_environment):
    state = setup_environment
    state.available.discard(ZARR)
    state.start()
    assert state.installs == []
    state.available.clear()
    state.start()
    assert state.installs[0][5:][0].startswith(f"morphobricks-preprocessing[notebook] {GITHUB}")


def test_setup_stops_when_drive_cannot_be_mounted(setup_environment, monkeypatch):
    def refuse(path):
        raise ValueError("mount failed")

    colab = SimpleNamespace(drive=SimpleNamespace(mount=refuse), output=SimpleNamespace())
    monkeypatch.setitem(sys.modules, "google.colab", colab)
    with pytest.raises(RuntimeError, match="Google Drive could not be connected"):
        setup_environment.start()
    assert setup_environment.installs == setup_environment.welcomes == []


def test_setup_reports_the_local_environment(setup_environment, capsys):
    setup_environment.start()
    out = capsys.readouterr().out
    assert "Local Jupyter: Python" in out and "Working folder:" in out
    assert "Google Drive" not in out


def test_setup_upgrades_ipywidgets_7(setup_environment, monkeypatch, capsys):
    real = importlib.metadata.version
    widgets = {"version": "7.7.1"}

    def version(name):
        return widgets["version"] if name == "ipywidgets" else real(name)

    def install(command, *, check):
        setup_environment.installs.append(command)
        widgets["version"] = "8.1.5"

    monkeypatch.setattr(importlib.metadata, "version", version)
    monkeypatch.setattr(subprocess, "run", install)
    setup_environment.start()
    assert setup_environment.installs[0][-1] == "morphobricks-preprocessing[notebook]"
    assert "Installing notebook requirements (ipywidgets 8)" in capsys.readouterr().out


def test_setup_asks_for_a_restart_when_ipywidgets_7_is_loaded(setup_environment, monkeypatch):
    monkeypatch.setitem(sys.modules, "ipywidgets", SimpleNamespace(__version__="7.7.1"))
    with pytest.raises(RuntimeError, match="Restart session"):
        setup_environment.start()
    assert setup_environment.imports == setup_environment.welcomes == []


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
