"""What pip records about an installed package (PEP 610 ``direct_url.json``), for the install cells.

The notebooks' install cells reuse an installed preprocessing only when this record names
``REPOSITORY`` at ``RELEASE_REF`` (issue #371). :func:`install_record_double` answers
``importlib.metadata.distribution`` from a test's ``state.direct_url``, and :func:`pip_install`
changes them as real pip does.
"""

import importlib.metadata
import json

DISTRIBUTION = "morphobricks-preprocessing"
PINNED_VERSION = "0.9.1"
"""The version both notebooks' RELEASE_REF commits hold."""


def git_record(commit, url):
    """Return the direct_url.json pip writes for an install from Git at ``commit``."""
    return json.dumps({"url": url, "vcs_info": {"vcs": "git", "commit_id": commit}})


def editable_record(folder):
    """Return the direct_url.json pip writes for an editable install of ``folder``."""
    return json.dumps({"url": folder.as_uri(), "dir_info": {"editable": True}})


def install_record_double(monkeypatch, state):
    """Answer ``distribution(name).read_text("direct_url.json")`` from ``state.direct_url``.

    ``state.direct_url`` is the file's text, None for no file, or an exception to raise; a
    package missing from ``state.versions`` is not installed. ``locate_file`` answers from
    ``state.site``, the folder pip installed into.
    """

    class Distribution:
        def read_text(self, filename):
            assert filename == "direct_url.json"
            if isinstance(state.direct_url, Exception):
                raise state.direct_url
            return state.direct_url

        def locate_file(self, path):
            return state.site / path

    def distribution(name):
        if name not in state.versions:
            raise importlib.metadata.PackageNotFoundError(name)
        return Distribution()

    monkeypatch.setattr(importlib.metadata, "distribution", distribution)


def pip_install(state, command):
    """Do to ``state`` what real pip does with ``command`` for the pinned preprocessing.

    pip takes an installed package of the same version as already satisfied, whatever commit it
    came from, and keeps it with its record unless the command forces a reinstall (the #371
    review, with pip 24.1.2 and 26.3). A forced reinstall must not reinstall the dependencies
    (numpy, loaded by a running kernel), so it always comes with ``--no-deps``.
    """
    target = next((word for word in command if " @ git+" in word), None)
    if target is None:
        return
    forced = "--force-reinstall" in command
    assert not forced or "--no-deps" in command, command
    url, commit = target.split(" @ git+", 1)[1].rsplit("@", 1)
    if forced or state.versions.get(DISTRIBUTION) != PINNED_VERSION:
        state.versions[DISTRIBUTION] = PINNED_VERSION
        state.direct_url = git_record(commit, url)


FORCED = ["install", "--disable-pip-version-check", "--force-reinstall", "--no-deps"]
"""The words of the install cells' forced reinstall after ``-m pip``."""


def forced_then_installed(commands, target):
    """Assert ``commands`` are the forced reinstall of ``target`` (no dependencies), then the
    install of ``target`` that adds what is missing; return the latter."""
    force, install = commands
    assert force[1:3] == ["-m", "pip"] and force[3:] == [*FORCED, target]
    assert install[-1] == target and "--force-reinstall" not in install
    return install
