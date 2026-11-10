"""Exercise the standalone setup cell without installing into the test environment."""

import importlib
import importlib.metadata
import importlib.util
import json
import platform
import re
import shutil
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


def cell_source():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    return "".join(next(c["source"] for c in notebook["cells"] if c["cell_type"] == "code"))


RELEASE_REF = re.search(r'RELEASE_REF = "([0-9a-f]{40})"', cell_source()).group(1)


def install_git_double(monkeypatch, state):
    """Answer the Git questions of the setup cell from ``state.git`` (default: clean release)."""
    state.git = SimpleNamespace(
        commit=RELEASE_REF, dirty=False, toplevel=None, real=False, calls=[]
    )
    real_run = subprocess.run  # taken before the fixture replaces it

    def check_output(command, **kwargs):
        if state.git.real:
            return real_run(command, check=True, stdout=subprocess.PIPE, **kwargs).stdout
        assert command[:2] == ["git", "-C"]
        arguments = tuple(command[3:])
        state.git.calls.append(arguments)
        if arguments == ("rev-parse", "--show-toplevel"):
            return (state.git.toplevel or command[2]) + "\n"
        if arguments == ("rev-parse", "HEAD"):
            if state.git.commit is None:
                raise subprocess.CalledProcessError(128, command)
            return state.git.commit + "\n"
        assert arguments == ("status", "--porcelain")
        return " M src/x.py\n" if state.git.dirty else ""

    monkeypatch.setattr(subprocess, "check_output", check_output)


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
            "morphobricks-preprocessing": "0.5.0",
            "morphobricks-normalization": "0.3.4",
            "ipywidgets": "7.7.1",
        },
    )

    install_git_double(monkeypatch, state)

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
        state.versions["morphobricks-preprocessing"] = "0.5.0"

    monkeypatch.setattr(subprocess, "run", install)

    def import_interface(name):
        state.imports.append(name)
        if name == "morphobricks_preprocessing":
            return SimpleNamespace(__file__="/tmp/morphobricks_preprocessing/__init__.py")

        import ipywidgets as widgets

        class FakeNotebook:
            def __init__(self, **options):  # describe=True, the shipped notebook's run
                self.options = options

            def __getattr__(self, name):  # welcome, folder_step, axis_step, channels_step, ...
                return lambda: widgets.HTML(name)

        return SimpleNamespace(PreprocessingNotebook=FakeNotebook)

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
    (path / "pyproject.toml").write_text(
        '[project]\nname = "morphobricks-preprocessing"\nversion = "0.5.0"\n',
        encoding="utf-8",
    )
    return path


def test_setup_reuses_the_matching_installed_release(setup_environment, capsys):
    state = setup_environment
    state.start()
    assert state.installs == []
    assert state.imports == [
        "morphobricks_preprocessing.notebook_ui",
        "morphobricks_preprocessing",
    ]
    out = capsys.readouterr().out
    assert "Ready: preprocessing 0.5.0" in out
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
    assert namespace["RELEASE_REF"] == "0ea3254418fb808feddb0a235fabc025bd855e9e"
    assert "pinned v0.5.0 candidate" in capsys.readouterr().out


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
    out = capsys.readouterr().out
    assert "local checkout" in out
    assert f"commit {RELEASE_REF}, clean tree" in out
    assert state.namespace["SETUP_RECORD"]["checkout_commit"] == RELEASE_REF
    assert state.namespace["SETUP_RECORD"]["checkout_has_uncommitted_changes"] is False
    assert state.namespace["SETUP_RECORD"]["development_override"] is False


OTHER_COMMIT = "0123456789abcdef0123456789abcdef01234567"


def run_cell_with_override(state):
    """Run the setup cell with DEVELOPMENT_OVERRIDE set to True, the one edit a developer makes."""
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    source = "".join(next(c["source"] for c in notebook["cells"] if c["cell_type"] == "code"))
    assert source.count("DEVELOPMENT_OVERRIDE = False") == 1
    namespace = {}
    exec(source.replace("DEVELOPMENT_OVERRIDE = False", "DEVELOPMENT_OVERRIDE = True"), namespace)
    return namespace


def start_in_checkout(state, tmp_path, monkeypatch):
    checkout = checkout_at(tmp_path / "checkout")
    monkeypatch.chdir(checkout)
    return checkout


@pytest.mark.parametrize(
    ("commit", "dirty", "match"),
    [
        (OTHER_COMMIT, False, f"its commit is {OTHER_COMMIT}, not RELEASE_REF"),
        (RELEASE_REF, True, "it has uncommitted changes"),  # the release commit, then edited
        (OTHER_COMMIT, True, "not RELEASE_REF.*uncommitted changes"),
        (None, False, "its commit is unknown"),
    ],
)
def test_setup_refuses_a_checkout_that_is_not_the_clean_release(
    setup_environment, tmp_path, monkeypatch, commit, dirty, match
):
    # #103: a checkout of the right version number but another commit, or edited, passed the pin.
    state = setup_environment
    state.git.commit, state.git.dirty = commit, dirty
    start_in_checkout(state, tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match=match) as refusal:
        state.start()
    assert "DEVELOPMENT_OVERRIDE = True" in str(refusal.value)
    assert state.installs == state.imports == []


def test_setup_refuses_a_directory_that_is_not_the_root_of_a_git_work_tree(
    setup_environment, tmp_path, monkeypatch
):
    state = setup_environment
    # The checkout sits inside another repository: that repository's commit is not the checkout's.
    state.git.toplevel = str(tmp_path)
    start_in_checkout(state, tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="its commit is unknown"):
        state.start()
    assert state.installs == state.imports == []


def test_an_explicit_development_override_uses_the_checkout_and_says_so(
    setup_environment, tmp_path, monkeypatch, capsys
):
    state = setup_environment
    state.git.commit, state.git.dirty = OTHER_COMMIT, True
    checkout = start_in_checkout(state, tmp_path, monkeypatch)
    namespace = run_cell_with_override(state)
    out = capsys.readouterr().out
    assert sys.path[0] == str(checkout / "src")
    assert f"commit {OTHER_COMMIT}, uncommitted changes" in out
    assert "DEVELOPMENT OVERRIDE is on" in out
    assert "DEVELOPMENT OVERRIDE on" in out.split("Notebook 0.5.0")[1]
    record = namespace["SETUP_RECORD"]
    assert record["checkout_commit"] == OTHER_COMMIT
    assert record["checkout_has_uncommitted_changes"] is True
    assert record["development_override"] is True


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_checkout_identity_reads_a_real_work_tree(setup_environment, tmp_path, monkeypatch):
    state = setup_environment
    state.git.real = True
    checkout = start_in_checkout(state, tmp_path, monkeypatch)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-C", str(checkout)]
    for command in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "x"]):
        subprocess.check_call([*git, *command])
    head = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    namespace = run_cell_with_override(state)  # another commit: only the override runs it
    assert namespace["_checkout_identity"](checkout) == (head, False)
    (checkout / "src/morphobricks_preprocessing/__init__.py").write_text("x = 1\n", "utf-8")
    assert namespace["_checkout_identity"](checkout) == (head, True)
    assert namespace["_checkout_identity"](tmp_path) == (None, None)  # not a work tree root


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
    with pytest.raises(
        RuntimeError, match="already loaded another MorphoBricks preprocessing copy"
    ):
        setup_environment.start()
    assert setup_environment.installs == setup_environment.imports == []


def colab(monkeypatch):
    calls = []
    fake = SimpleNamespace(drive=SimpleNamespace(mount=lambda path: calls.append(path)))
    monkeypatch.setitem(sys.modules, "google.colab", fake)
    return calls


def test_colab_mounts_drive_without_a_custom_widget_manager(setup_environment, monkeypatch, capsys):
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
