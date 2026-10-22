"""The code moved to morphobricks_normalization stays importable here, with a warning."""

import importlib
import re
import warnings
from pathlib import Path

import numpy as np
import pytest

import morphobricks_preprocessing as mbp
from morphobricks_preprocessing import _moved
from morphobricks_preprocessing.cli import main
from tiff_tree import write_data

PACKAGE = Path(mbp.__file__).parent
MOVED_MODULES = sorted(_moved.MOVED_NAMES)
SUBMODULE_NAMES = [(m, n) for m in MOVED_MODULES for n in _moved.MOVED_NAMES[m]]


@pytest.fixture(autouse=True)
def fresh_warnings(monkeypatch):
    # Other tests (and their helpers) already read some old paths in this session.
    monkeypatch.setattr(_moved, "_warned", set())


def new_object(module, name):
    return getattr(importlib.import_module(f"morphobricks_normalization.{module}"), name)


@pytest.mark.parametrize("module", MOVED_MODULES)
def test_moved_modules_match_the_normalization_contract(module):
    # Every public name of the new module is forwarded, and nothing else.
    new = importlib.import_module(f"morphobricks_normalization.{module}")
    defined = {
        name
        for name, value in vars(new).items()
        if not name.startswith("_")
        and not isinstance(value, type(np))
        and getattr(value, "__module__", new.__name__) == new.__name__
    }
    assert set(_moved.MOVED_NAMES[module]) == defined


def test_top_level_names_are_the_ones_the_package_exported():
    for name, module in _moved.TOP_LEVEL_NAMES.items():
        assert name in _moved.MOVED_NAMES[module]
        assert name in mbp.__all__
        assert name in dir(mbp)


@pytest.mark.parametrize(("name", "module"), sorted(_moved.TOP_LEVEL_NAMES.items()))
def test_top_level_name_warns_once_and_is_the_new_object(name, module):
    with pytest.warns(DeprecationWarning) as record:
        value = getattr(mbp, name)
    (warning,) = record
    message = str(warning.message)
    assert f"morphobricks_preprocessing.{name} has moved to" in message
    assert f"morphobricks_normalization.{module}.{name}" in message
    assert warning.filename == __file__  # points at the caller
    assert value is new_object(module, name)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert getattr(mbp, name) is value


@pytest.mark.parametrize(("module", "name"), SUBMODULE_NAMES)
def test_old_submodule_name_warns_once_and_is_the_new_object(module, name):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        old = importlib.import_module(f"morphobricks_preprocessing.{module}")
    with pytest.warns(DeprecationWarning) as record:
        value = getattr(old, name)
    (warning,) = record
    message = str(warning.message)
    assert f"morphobricks_preprocessing.{module}.{name} has moved to" in message
    assert f"morphobricks_normalization.{module}.{name}" in message
    assert warning.filename == __file__
    assert value is new_object(module, name)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert getattr(old, name) is value
    assert name in dir(old)


def test_from_imports_keep_working():
    with pytest.warns(DeprecationWarning, match="csv_io.SCHEMA_VERSION"):
        from morphobricks_preprocessing.csv_io import SCHEMA_VERSION
    with pytest.warns(DeprecationWarning, match="morphobricks_preprocessing.PlaneRecord"):
        from morphobricks_preprocessing import PlaneRecord
    assert SCHEMA_VERSION == "2"
    from morphobricks_normalization import PlaneRecord as new_record

    assert PlaneRecord is new_record


def test_each_old_path_warns_separately():
    with pytest.warns(DeprecationWarning, match=r"preprocessing\.PooledHistogram"):
        _ = mbp.PooledHistogram
    with pytest.warns(DeprecationWarning, match=r"preprocessing\.pooling\.PooledHistogram"):
        _ = mbp.pooling.PooledHistogram


def test_exceptions_are_caught_through_the_old_names():
    with pytest.warns(DeprecationWarning):
        old_error = mbp.UnsupportedDtypeError
    from morphobricks_normalization.dtypes import check_supported

    with pytest.raises(old_error):
        check_supported(np.dtype(bool))


@pytest.mark.parametrize("module", ["morphobricks_preprocessing", *MOVED_MODULES])
def test_unknown_names_are_still_missing(module):
    target = mbp if module == "morphobricks_preprocessing" else getattr(mbp, module)
    with pytest.raises(AttributeError, match="no_such_name"):
        _ = target.no_such_name


def test_package_code_imports_from_the_normalization_brick():
    old_import = re.compile(
        r"^\s*from \.(dtypes|histogram|records|csv_io|pooling|normalization) import"
        r"|^\s*from morphobricks_preprocessing\.(dtypes|histogram|records|csv_io|pooling|"
        r"normalization) import",
        re.MULTILINE,
    )
    for path in PACKAGE.glob("*.py"):
        assert not old_import.search(path.read_text(encoding="utf-8")), path.name


def test_the_package_itself_never_warns(tmp_path, capsys):
    root = tmp_path / "exp"
    rng = np.random.default_rng(0)
    for rel in ["ctrl/rep1/a.tif", "drug/rep1/b.tif"]:
        write_data(root / rel, rng.integers(0, 200, (2, 8, 8), dtype=np.uint8), axes="ZYX")
    assert main(["check", str(root)]) == 0
    assert main(["run", str(root), "--axes", "ZYX"]) == 0
    capsys.readouterr()
    assert _moved._warned == set()
