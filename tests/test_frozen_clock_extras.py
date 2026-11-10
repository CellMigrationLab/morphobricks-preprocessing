"""The frozen clock works without the ``notebook`` extra (issue #262).

``tests/frozen_clock.py`` imports every module of the package. Without ipywidgets and
matplotlib the notebook modules cannot be imported; they are skipped, and any other import
failure is still raised. Run in a fresh interpreter where both packages are blocked.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent

_BLOCKED = """
import sys
sys.path.insert(0, {tests!r})
for name in ("ipywidgets", "matplotlib"):
    sys.modules[name] = None  # import of either raises ModuleNotFoundError
import frozen_clock
modules = {{m.__name__ for m in frozen_clock._import_package()}}
assert "morphobricks_preprocessing.notebook_ui" not in modules, modules
assert "morphobricks_preprocessing.design_derive" in modules, modules
assert "morphobricks_preprocessing.run.pipeline" in modules, modules
print("ok")
"""


def test_the_notebook_modules_are_skipped_without_the_extra():
    code = _BLOCKED.format(tests=str(TESTS))
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"


def test_another_missing_module_is_still_raised(monkeypatch):
    import frozen_clock

    def missing(name, package=None):
        raise ModuleNotFoundError("No module named 'tifffile'", name="tifffile")

    monkeypatch.setattr(frozen_clock.importlib, "import_module", missing)
    try:
        frozen_clock._import_package()
    except ModuleNotFoundError as exc:
        assert exc.name == "tifffile"
    else:
        raise AssertionError("a missing required dependency was skipped")
