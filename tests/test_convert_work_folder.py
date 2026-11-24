"""The follow-ups of #483's review (issue #488): the conversion work folder, on Windows too.

Each item's tests are under its number; see the issue.
"""

import contextlib
import os
import socket
import sys

import pytest

import morphobricks_preprocessing.conversion_plan as plan_module
from morphobricks_preprocessing import convert_experiment
from morphobricks_preprocessing.conversion_record import RECORD_FILENAME
from morphobricks_preprocessing.lock_file import LockHolder
from morphobricks_preprocessing.run.lock import holder_running
from test_convert_partials import STORES, _holder, _store
from test_convert_run import SWAPPED, codes, swapped_experiment


def test_a_holder_counts_as_gone_on_windows_and_on_another_computer(monkeypatch):
    # (1) What the docs now say: only POSIX tells whether a process runs, and only one on
    # this computer; the lock is the real guard. (Coverage: this held on main too.)
    here = LockHolder(**_holder(pid=os.getpid()))
    elsewhere = LockHolder(**_holder(pid=os.getpid(), host=socket.gethostname() + "-other"))
    assert not holder_running(elsewhere)
    monkeypatch.setattr(os, "name", "nt")
    assert not holder_running(here)


def _deepest(folder):
    """The longest folder path and file path below ``folder``, relative, in characters."""
    found = [p for p in folder.rglob("*") if p.name != RECORD_FILENAME]
    folders = [len(str(p.relative_to(folder))) for p in found if p.is_dir()]
    files = [len(str(p.relative_to(folder))) for p in found if not p.is_dir()]
    return max(folders), max(files)


@pytest.mark.parametrize(("store", "copy"), STORES)
def test_a_copy_folder_path_too_long_for_windows_is_refused_when_planned(
    tmp_path, monkeypatch, store, copy
):
    # (2) Every file fits in 259 characters, but a folder is longer than 247: Windows
    # cannot make it without long paths, so it is refused when planned, not at mkdir.
    _store(store)
    exp = swapped_experiment(tmp_path / "exp")
    assert convert_experiment(exp, SWAPPED, tmp_path / "probe", store=store).written
    folder, file = _deepest(tmp_path / "probe")
    work = len("/.mbconv/files/")
    copies = tmp_path / ("c" * (250 - work - folder - len(str(tmp_path)) - 1))
    assert len(str(copies)) + work + file <= 259, "the files must fit"
    monkeypatch.setattr(plan_module, "_windows", lambda: True)
    result = convert_experiment(exp, SWAPPED, copies, store=store)
    assert not result.written and codes(result.issues) == ["copy_path_too_long"]
    message = result.issues[0].message
    assert copy in result.issues[0].paths and "a folder whose path is longer than 247" in message
    assert "259" not in message and not copies.exists()
    monkeypatch.setattr(plan_module, "_windows", lambda: False)  # long paths enabled
    assert convert_experiment(exp, SWAPPED, copies, store=store).written


@pytest.mark.parametrize(
    ("name", "enabled", "applies"),
    [("nt", False, True), ("nt", True, False), ("posix", None, False)],
    ids=["windows", "windows_long_paths", "posix"],
)
def test_the_windows_limits_apply_only_without_long_paths(monkeypatch, name, enabled, applies):
    def setting():
        assert enabled is not None, "the Windows setting is read on Windows only"
        return enabled

    monkeypatch.setattr(plan_module, "_long_paths_enabled", setting)
    monkeypatch.setattr(os, "name", name)
    assert plan_module._windows() is applies


class _Registry:
    """A stand-in for ``winreg`` answering ``LongPathsEnabled`` with ``value``."""

    HKEY_LOCAL_MACHINE = "HKLM"

    def __init__(self, value):
        self.value, self.asked = value, []

    def OpenKey(self, root, key):  # noqa: N802 (winreg's name)
        self.asked.append((root, key))
        if self.value is None:
            raise FileNotFoundError(key)
        return contextlib.nullcontext("key")

    def QueryValueEx(self, key, name):  # noqa: N802 (winreg's name)
        self.asked.append(name)
        return self.value, 4


@pytest.mark.parametrize(("value", "enabled"), [(1, True), (0, False), (None, False)])
def test_the_long_paths_setting_is_read_from_the_registry(monkeypatch, value, enabled):
    registry = _Registry(value)
    monkeypatch.setitem(sys.modules, "winreg", registry)
    assert plan_module._long_paths_enabled() is enabled
    assert registry.asked[0] == ("HKLM", r"SYSTEM\CurrentControlSet\Control\FileSystem")
    if value is not None:
        assert registry.asked[1] == "LongPathsEnabled"


def test_without_winreg_long_paths_count_as_disabled(monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", None)  # import winreg raises ImportError
    assert plan_module._long_paths_enabled() is False
