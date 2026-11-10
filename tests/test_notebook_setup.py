"""Exercise the notebook's three code cells without installing into the test environment.

Cell 1 installs, cell 2 mounts Google Drive on Colab, cell 3 shows the app; the cells run in
one namespace, as in a kernel.
"""

import ast
import functools
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


INSTALL, DRIVE, RUN = 1, 2, 3
"""The cells' numbers, as their titles and the notebook's text name them."""
COLAB_DRIVE = "/content/drive"


def cell_source(number=INSTALL):
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    code = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
    return "".join(code[number - 1]["source"])


RELEASE_REF = re.search(r'RELEASE_REF = "([0-9a-f]{40})"', cell_source()).group(1)
_APP_CALL = ast.parse(
    re.search(r"PreprocessingNotebook\([^)]*\)", cell_source(RUN)).group(0), mode="eval"
).body
APP_OPTIONS = {keyword.arg: ast.literal_eval(keyword.value) for keyword in _APP_CALL.keywords}
"""The options cell 3 passes, as its call names them (``test_notebook_file.RELEASE_CALLS``)."""


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


def run_cells(state, *numbers, fresh=True):
    """Run the code cells ``numbers`` (all three by default) in order, in one namespace.

    ``fresh`` starts a new kernel namespace; ``/content/drive`` is ``state.drive`` (the fake
    Drive's mount point).
    """
    if fresh:
        state.namespace = {}
    for number in numbers or (INSTALL, DRIVE, RUN):
        source = cell_source(number).replace(COLAB_DRIVE, str(state.drive))
        exec(source, state.namespace)
    return state.namespace


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
        apps=[],
        drive=tmp_path / "content/drive",
        namespace={},
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
                state.apps.append(options)

            def __getattr__(self, name):  # welcome, folder_step, axis_step, channels_step, ...
                return lambda: widgets.HTML(name)

        return SimpleNamespace(PreprocessingNotebook=FakeNotebook)

    monkeypatch.setattr(importlib, "import_module", import_interface)

    state.start = functools.partial(run_cells, state)
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
        "morphobricks_preprocessing.notebook_ui",  # cell 1 checks the interface loads
        "morphobricks_preprocessing",
        "morphobricks_preprocessing.notebook_ui",  # cell 3 shows it
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
    assert namespace["RELEASE_REF"] == "fd7afd339bcaab7579c806a28a65bb7ecebe940c"
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
    """Fake ``google.colab``: its ``drive.mount`` records the mount point and makes MyDrive."""
    calls = []

    def mount(path):
        calls.append(path)
        (Path(path) / "MyDrive").mkdir(parents=True, exist_ok=True)

    fake = SimpleNamespace(drive=SimpleNamespace(mount=mount))
    monkeypatch.setitem(sys.modules, "google.colab", fake)
    return calls


def test_colab_mounts_drive_without_a_custom_widget_manager(setup_environment, monkeypatch, capsys):
    calls = colab(monkeypatch)
    setup_environment.start()
    assert f'drive.mount("{COLAB_DRIVE}")' in cell_source(DRIVE)
    assert calls == [str(setup_environment.drive)]  # the cells' /content/drive, faked
    out = capsys.readouterr().out
    assert "Google Colab: connecting Google Drive" in out
    assert "Google Drive is connected" in out
    assert setup_environment.apps == [APP_OPTIONS]


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
    state = setup_environment
    state.start(INSTALL)
    imported = list(state.imports)
    with pytest.raises(RuntimeError, match="Google Drive could not be connected"):
        state.start(DRIVE, fresh=False)
    assert state.imports == imported
    assert state.apps == []


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


# The three cells (owner, 10 November 2026): each does its own part, and a cell run out of order
# says which cell to run; no cell runs another for the user.


def test_cell_1_installs_without_mounting_drive_or_showing_the_app(
    setup_environment, monkeypatch, capsys
):
    calls = colab(monkeypatch)
    state = setup_environment
    state.available.clear()
    state.versions.pop("morphobricks-preprocessing")
    namespace = state.start(INSTALL)
    assert len(state.installs) == 1
    assert "morphobricks-preprocessing[notebook,zarr]" in state.installs[0][-1]
    assert calls == []
    assert state.apps == []
    assert namespace["SETUP_RECORD"]["release_ref"] == RELEASE_REF
    out = capsys.readouterr().out
    assert "Ready: preprocessing 0.5.0; normalization 0.3.4; ipywidgets 7.7.1." in out
    assert "Next: run cell 2 (Mount Google Drive), then cell 3 (Run preprocessing)." in out


def test_running_cell_1_again_installs_nothing_more(setup_environment, capsys):
    state = setup_environment
    state.available.clear()
    state.versions.pop("morphobricks-preprocessing")
    state.start(INSTALL)
    first = capsys.readouterr().out
    record = dict(state.namespace["SETUP_RECORD"])
    state.start(INSTALL, fresh=False)
    again = capsys.readouterr().out
    assert len(state.installs) == 1
    assert "Installing notebook requirements" in first
    assert "Installing notebook requirements" not in again
    assert "Ready: preprocessing 0.5.0" in again
    assert state.namespace["SETUP_RECORD"]["preprocessing_version"] == "0.5.0"
    assert record["release_ref"] == state.namespace["SETUP_RECORD"]["release_ref"]


def test_cell_2_outside_colab_prints_one_note_and_does_nothing(setup_environment, capsys):
    state = setup_environment
    assert "google.colab" not in sys.modules
    state.start(DRIVE)
    out = capsys.readouterr().out
    assert out.strip().splitlines() == [
        "Not Google Colab: there is no Google Drive to mount here, so nothing was done."
    ]
    assert state.installs == state.imports == state.apps == []
    assert not state.drive.exists()


def test_cell_2_alone_on_colab_only_mounts_drive(setup_environment, monkeypatch):
    calls = colab(monkeypatch)
    state = setup_environment
    state.start(DRIVE)
    assert calls == [str(state.drive)]
    assert state.installs == state.imports == state.apps == []


@pytest.mark.parametrize("on_colab", [False, True])
def test_cell_3_without_cell_1_names_cell_1(setup_environment, monkeypatch, on_colab):
    state = setup_environment
    if on_colab:
        colab(monkeypatch)
    with pytest.raises(RuntimeError, match=r"run cell 1 \(Install MorphoBricks preprocessing\)"):
        state.start(RUN)
    assert state.installs == state.imports == state.apps == []


def test_cell_3_after_a_failed_cell_1_still_names_cell_1(setup_environment, monkeypatch):
    state = setup_environment
    monkeypatch.setattr(sys, "version_info", (3, 10, 0))
    with pytest.raises(RuntimeError, match="Python 3.11 or newer"):
        state.start(INSTALL)
    with pytest.raises(RuntimeError, match="run cell 1"):
        state.start(RUN, fresh=False)
    assert state.apps == []


def test_cell_3_on_colab_without_drive_names_cell_2(setup_environment, monkeypatch):
    calls = colab(monkeypatch)
    state = setup_environment
    state.start(INSTALL)
    imported = list(state.imports)
    with pytest.raises(RuntimeError, match=r"Google Drive is not connected.*run cell 2"):
        state.start(RUN, fresh=False)
    assert calls == []  # never mounted for the user
    assert state.imports == imported
    assert state.apps == []
    state.start(DRIVE, RUN, fresh=False)
    assert state.apps == [APP_OPTIONS]


def test_cell_3_outside_colab_needs_no_drive(setup_environment):
    state = setup_environment
    state.start(INSTALL, RUN)  # cell 2 skipped: nothing to mount outside Colab
    assert state.apps == [APP_OPTIONS]
    assert "app" in state.namespace
