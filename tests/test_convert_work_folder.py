"""The follow-ups of #483's review (issue #488): the conversion work folder, on Windows too.

Each item's tests are under its number; see the issue.
"""

import os
import socket

from morphobricks_preprocessing.lock_file import LockHolder
from morphobricks_preprocessing.run.lock import holder_running
from test_convert_partials import _holder


def test_a_holder_counts_as_gone_on_windows_and_on_another_computer(monkeypatch):
    # (1) What the docs now say: only POSIX tells whether a process runs, and only one on
    # this computer; the lock is the real guard. (Coverage: this held on main too.)
    here = LockHolder(**_holder(pid=os.getpid()))
    elsewhere = LockHolder(**_holder(pid=os.getpid(), host=socket.gethostname() + "-other"))
    assert not holder_running(elsewhere)
    monkeypatch.setattr(os, "name", "nt")
    assert not holder_running(here)
