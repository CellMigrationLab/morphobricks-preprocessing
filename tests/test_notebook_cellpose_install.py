"""The Cellpose notebook's install cell, run with the package installer and its records replaced.

Cellpose and the pinned preprocessing are never installed here: ``pip`` is a ledger that changes
the installed versions and pip's PEP 610 record as real pip does (``installed_record``).
"""

import subprocess
import sys

import pytest

from installed_record import editable_record, git_record, install_record_double
from test_notebook_cellpose import INSTALL, ROOT, run_cell, setting


@pytest.fixture
def installer(monkeypatch):
    """Run the install cell with the installer and the metadata replaced by a ledger."""
    state = type("S", (), {})()
    state.versions = {"cellpose": "4.2.1.1", "morphobricks-preprocessing": "0.8.0"}
    state.direct_url = git_record(setting("RELEASE_REF"), setting("REPOSITORY"))
    state.pip = []
    state.upgrades = {}  # versions pip changes when it runs
    import importlib.metadata as metadata

    def fake_version(name):
        if name not in state.versions:
            raise metadata.PackageNotFoundError(name)
        return state.versions[name]

    def fake_run(command, check):
        state.pip.append(command)
        state.versions.update(state.upgrades)
        state.versions["cellpose"] = "4.2.1.1"
        state.direct_url = git_record(setting("RELEASE_REF"), setting("REPOSITORY"))

    monkeypatch.setattr(metadata, "version", fake_version)
    install_record_double(monkeypatch, state)
    monkeypatch.setattr(subprocess, "run", fake_run)
    return state


def test_the_install_cell_installs_nothing_that_is_already_there(installer, capsys):
    namespace = {}
    run_cell(namespace, INSTALL)
    assert installer.pip == []
    out = capsys.readouterr().out
    assert "Ready: Cellpose 4.2.1.1" in out and setting("RELEASE_REF")[:12] in out
    assert namespace["SETUP_RECORD"]["cellpose_version"] == "4.2.1.1"
    assert "dinov3" not in "".join(sys.modules)  # DINOv3 is never installed by this cell


def test_the_install_cell_installs_the_pinned_versions_when_they_differ(installer):
    installer.versions["cellpose"] = "4.0.0"
    installer.direct_url = git_record("0" * 40, setting("REPOSITORY"))
    run_cell({}, INSTALL)
    (command,) = installer.pip
    assert command[1:4] == ["-m", "pip", "install"]
    assert "cellpose==4.2.1.1" in command
    assert any(
        a.startswith("morphobricks-preprocessing[") and a.endswith(f"@{setting('RELEASE_REF')}")
        for a in command
    )
    assert not any("dinov3" in a for a in command)


def test_the_banner_shows_the_installed_commit(installer, capsys):
    namespace = {}
    run_cell(namespace, INSTALL)
    assert f"preprocessing 0.8.0 (commit {setting('RELEASE_REF')})." in capsys.readouterr().out
    assert namespace["SETUP_RECORD"]["preprocessing_commit"] == setting("RELEASE_REF")


# Issue #371: only pip's record of REPOSITORY at RELEASE_REF is the pinned preprocessing.
NOT_THE_PIN = {
    "same version, another commit": git_record("2" * 40, setting("REPOSITORY")),
    "another repository at the commit": git_record(setting("RELEASE_REF"), "https://x.org/a.git"),
    "no direct_url.json": None,
    "unreadable direct_url.json": "{not json",
    "direct_url.json not an object": "[]",
    "an editable install": editable_record(ROOT),
}


@pytest.mark.parametrize("record", NOT_THE_PIN.values(), ids=NOT_THE_PIN)
def test_the_install_cell_installs_the_pin_over_another_commit(installer, record, capsys):
    installer.direct_url = record  # the installed version stays 0.8.0
    run_cell({}, INSTALL)
    (command,) = installer.pip
    assert command[-1].endswith(f"@ git+{setting('REPOSITORY')}@{setting('RELEASE_REF')}")
    assert f"(commit {setting('RELEASE_REF')})" in capsys.readouterr().out


def test_the_install_cell_stops_when_the_install_leaves_another_commit(installer, monkeypatch):
    installer.direct_url = git_record("2" * 40, setting("REPOSITORY"))
    monkeypatch.setattr(subprocess, "run", lambda command, check: installer.pip.append(command))
    namespace = {}
    with pytest.raises(RuntimeError, match=f"not {setting('RELEASE_REF')}: pip recorded 2222"):
        run_cell(namespace, INSTALL)
    assert namespace["SETUP_RECORD"] == {}


def test_an_install_that_upgrades_a_loaded_numpy_or_opencv_says_to_restart(installer):
    assert "numpy" in sys.modules  # loaded by the kernel, as in Colab
    installer.versions.update(cellpose="4.0.0", numpy="1.26.4")
    installer.upgrades = {"numpy": "2.1.0"}
    with pytest.raises(RuntimeError, match=r"upgraded numpy 1.26.4 -> 2.1.0.*Restart the runtime"):
        run_cell({}, INSTALL)
    run_cell({}, INSTALL)  # after the restart: nothing more is installed, and it is ready
    assert len(installer.pip) == 1


def test_an_upgrade_of_a_package_the_kernel_has_not_loaded_needs_no_restart(installer):
    installer.versions.update(cellpose="4.0.0", **{"opencv-python": "4.9.0"})
    installer.upgrades = {"opencv-python": "4.10.0"}
    assert "cv2" not in sys.modules
    run_cell({}, INSTALL)
