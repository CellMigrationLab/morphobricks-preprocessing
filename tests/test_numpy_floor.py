"""The declared NumPy floor covers the NumPy functions the code uses (contract §19, B1).

The image QC sums use ``numpy.vecdot``, new in NumPy 2.0, so ``pyproject.toml`` must not
allow an older NumPy.
"""

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
SINCE = {"np.vecdot": "2.0", "numpy.vecdot": "2.0"}
"""NumPy functions used under ``src`` and the first NumPy release that has each."""


def numpy_floor():
    """Return the lowest NumPy version the project's dependencies allow."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    (spec,) = (
        Requirement(text)
        for text in data["project"]["dependencies"]
        if Requirement(text).name == "numpy"
    )
    return min(Version(s.version) for s in spec.specifier if s.operator in (">=", "=="))


def test_the_numpy_floor_is_a_release_that_has_every_function_the_code_uses():
    used = {
        name: since
        for path in (ROOT / "src").rglob("*.py")
        for name, since in SINCE.items()
        if name in path.read_text(encoding="utf-8")
    }
    assert numpy_floor() >= max((Version(since) for since in used.values()), default=Version("0"))
