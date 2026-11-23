"""What pip records about an installed package (PEP 610 ``direct_url.json``), for the install cells.

The notebooks' install cells reuse an installed preprocessing only when this record names
``REPOSITORY`` at ``RELEASE_REF`` (issue #371). :func:`install_record_double` answers
``importlib.metadata.distribution`` from a test's ``state.direct_url``.
"""

import importlib.metadata
import json


def git_record(commit, url):
    """Return the direct_url.json pip writes for an install from Git at ``commit``."""
    return json.dumps({"url": url, "vcs_info": {"vcs": "git", "commit_id": commit}})


def editable_record(folder):
    """Return the direct_url.json pip writes for an editable install of ``folder``."""
    return json.dumps({"url": folder.as_uri(), "dir_info": {"editable": True}})


def install_record_double(monkeypatch, state):
    """Answer ``distribution(name).read_text("direct_url.json")`` from ``state.direct_url``.

    ``state.direct_url`` is the file's text, None for no file, or an exception to raise; a
    package missing from ``state.versions`` is not installed.
    """

    class Distribution:
        def read_text(self, filename):
            assert filename == "direct_url.json"
            if isinstance(state.direct_url, Exception):
                raise state.direct_url
            return state.direct_url

    def distribution(name):
        if name not in state.versions:
            raise importlib.metadata.PackageNotFoundError(name)
        return Distribution()

    monkeypatch.setattr(importlib.metadata, "distribution", distribution)
