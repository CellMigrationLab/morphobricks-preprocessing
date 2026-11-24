"""The release-run switch: the notebooks' pin freshness is checked only in release runs.

Owner decision (24 November 2026): the full CI runs only at release, and the notebooks'
``RELEASE_REF`` is moved only in the release pull request. A pull request no longer re-pins, so
the check that the Cellpose notebook's ``RELEASE_REF`` holds the code it runs
(:func:`notebook_pin.check_pin_holds_this_code`) skips outside a release run. A release run
sets ``MORPHOBRICKS_RELEASE_RUN=1``: the release CI (``.github/workflows/ci.yml``: a manual
run, a ``v*`` tag, a pull request labelled ``release``) and ``python tools/check.py --release``.

The structural pin checks (the pin is one commit, the install is pinned, the cells' scope is
derived) do not read the history and run in every run.
"""

from __future__ import annotations

import os

import pytest

SWITCH = "MORPHOBRICKS_RELEASE_RUN"
"""Set to ``1`` in a release run; ``0`` or unset otherwise. Any other value is refused."""
RELEASE_ONLY = (
    "release-only: the notebooks are pinned in the release PR "
    f"({SWITCH}=1 or python tools/check.py --release runs it)"
)
"""The reason a release-only test gives when it skips."""


def release_run() -> bool:
    """Return whether this is a release run; fail for a value of the switch other than 0 or 1."""
    value = os.environ.get(SWITCH, "")
    if value not in ("", "0", "1"):
        pytest.fail(f"{SWITCH} must be 1 (a release run), 0 or unset, not {value!r}")
    return value == "1"


def release_only() -> None:
    """Skip the calling test unless this is a release run."""
    if not release_run():
        pytest.skip(RELEASE_ONLY)
