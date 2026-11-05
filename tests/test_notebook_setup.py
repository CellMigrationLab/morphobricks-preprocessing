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
ZARR = "zarr"


@pytest.fixture
def setup_environment(tmp_path, monkeypatch):
    platform.platform()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.delitem(sys.modules, "morphobricks_preprocessing", raising=False)
    state = SimpleNamespace(
        available={*REQUIRED, ZARR},
        installs=[],
        imports=[],
        versions={
            "morphobricks-preprocessing": "0.4.0rc6",
            "morphobricks-normalization": "0.3.3",
            "ipywidgets": "7.7.1",
        },
    )

    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: object() if name in state.available else None,
    )

    def package_version(name):
        value = state.versions.get(name)
        if value is None:
            raise importlib.metadata.PackageNotFoundError(name)
        return value

    monkeypatch.setattr(importlib.metadata, "version", package_version)

    def install(command, *, check):
        assert check is True
        state.installs.append(command)
        state.available.update({*REQUIRED, ZARR})
        state.versions["morphobricks-preprocessing"] = "0.4.0rc6"

    monkeypatch.setattr(subprocess, "run", install)

    def import_interface(name):
        state.imports.append(name)
        return SimpleNamespace(PreprocessingNotebook=lambda: SimpleNamespace())

    monkeypatch.setattr(importlib, "import_module", import_interface)

    def start():
        notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
        source = next(c["source"] for c in notebook["cells"] if c["cell_type"] == "code")
        namespace = {}
        exec("".join(source), namespace)
        state.namespace = namespace
        return namespace

    state.start = start
    return state


def checkout_at(path):
    package = path / "src/morphobricks_preprocessing"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (path / "pyproject.toml").write_text("", encoding="utf-8")
    return path


def test_setup_reuses_the_matching_installed_release(setup_environment, capsys):
    state = setup_environment
    state.start()
    assert state.installs == []
    assert state.imports == ["morphobricks_preprocessing.notebook_ui"]
    out = capsys.readouterr().out
    assert "Ready. MorphoBricks preprocessing 0.4.0rc6" in out
    assert "ipywidgets 7.7.1" in out


def test_setup_installs_missing_package_from_the_exact_commit(setup_environment, capsys):
    state = setup_environment
    state.available.clear()
    state.versions.pop("morphobricks-preprocessing")
    namespace = state.start()
    (command,) = state.installs
    assert command[:5] == [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    target = command[5]
    assert target.startswith("morphobricks-preprocessing[notebook] @ git+")
    assert target.endswith("@" + namespace["RELEASE_REF"])
    assert namespace["RELEASE_REF"] == "d6930b80f26bce1b12762940d3c9ed44b038949f"
    assert "release v0.4.0rc6" in capsys.readouterr().out


def test_setup_replaces_a_mismatched_installed_release(setup_environment):
    state = setup_environment
    state.versions["morphobricks-preprocessing"] = "0.3.4"
    state.start()
    assert len(state.installs) == 1
    assert state.installs[0][-1].endswith("@" + state.namespace["RELEASE_REF"])


def test_setup_prefers_a_nearby_checkout(setup_environment, tmp_path, monkeypatch, capsys):
    state = setup_environment
    checkout = checkout_at(tmp_path / "checkout")
    monkeypatch.chdir(checkout)
    state.available.remove("matplotlib")
    state.start()
    assert sys.path[0] == str(checkout / "src")
    assert state.installs[0][-2:] == ["-e", f"{checkout}[notebook]"]
    assert "local checkout" in capsys.readouterr().out


def test_setup_does_not_mix_an_already_loaded_package_with_another_checkout(
    setup_environment, tmp_path, monkeypatch
):
    checkout = checkout_at(tmp_path / "checkout")
    monkeypatch.chdir(checkout)
    monkeypatch.setitem(
        sys.modules,
        "morphobricks_preprocessing",
        SimpleNamespace(__file__=str(tmp_path / "another_copy/__init__.py")),
    )
    with pytest.raises(RuntimeError, match="already loaded another copy"):
        setup_environment.start()
    assert setup_environment.installs == setup_environment.imports == []


def colab(monkeypatch):
    calls = []
    fake = SimpleNamespace(drive=SimpleNamespace(mount=lambda path: calls.append(path)))
    monkeypatch.setitem(sys.modules, "google.colab", fake)
    return calls


def test_colab_mounts_drive_without_a_custom_widget_manager(
    setup_environment, monkeypatch, capsys
):
    calls = colab(monkeypatch)
    setup_environment.start()
    assert calls == ["/content/drive"]
    assert "Google Colab: connecting Google Drive" in capsys.readouterr().out


def test_colab_installs_the_zarr_extra(setup_environment, monkeypatch):
    colab(monkeypatch)
    state = setup_environment
    state.available.clear()
    state.versions.pop("morphobricks-preprocessing")
    state.start()
    assert "morphobricks-preprocessing[notebook,zarr]" in state.installs[0][-1]


def test_colab_accepts_native_ipywidgets_7_without_install_or_restart(
    setup_environment, monkeypatch
):
    colab(monkeypatch)
    state = setup_environment
    state.versions["ipywidgets"] = "7.7.1"
    state.start()
    assert state.installs == []


def test_setup_stops_when_drive_cannot_be_mounted(setup_environment, monkeypatch):
    def refuse(path):
        raise ValueError("mount failed")

    monkeypatch.setitem(
        sys.modules,
        "google.colab",
        SimpleNamespace(drive=SimpleNamespace(mount=refuse)),
    )
    with pytest.raises(RuntimeError, match="Google Drive could not be connected"):
        setup_environment.start()
    assert setup_environment.imports == []


def test_setup_rejects_old_python_before_installing(setup_environment, monkeypatch):
    monkeypatch.setattr(sys, "version_info", (3, 10, 0))
    with pytest.raises(RuntimeError, match="Python 3.11 or newer"):
        setup_environment.start()
    assert setup_environment.installs == setup_environment.imports == []


def test_setup_reports_failed_installation(setup_environment, monkeypatch):
    state = setup_environment
    state.available.clear()
    state.versions.pop("morphobricks-preprocessing")

    def fail(command, *, check):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="could not install"):
        state.start()
    assert state.imports == []
