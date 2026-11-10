"""A frozen clock for tests: every ``date`` and ``datetime`` the package can read gives one day.

Conformance plan section 2, ``confirmed_on``: a writer under a frozen clock moved by a day must
write identical bytes for an unchanged model. :class:`FrozenClock` replaces
:class:`datetime.date` and :class:`datetime.datetime` in the :mod:`datetime` module and in every
module of the package that imported them by name, with subclasses whose ``today``, ``now`` and
``utcnow`` return the frozen day (at noon UTC) and count each read.

The notebook modules need the ``notebook`` extra (ipywidgets, matplotlib). Without it they cannot
be imported, so they are left out (issue #262): such a module cannot run, so it cannot read the
clock either. Any other import failure is raised.
"""

from __future__ import annotations

import datetime as dt
import importlib
import importlib.util
import pkgutil
import sys

import morphobricks_preprocessing

PACKAGE = morphobricks_preprocessing.__name__
NOTEBOOK_EXTRA = frozenset({"ipywidgets", "matplotlib"})
"""The top-level packages of the ``notebook`` extra; a module needing one is skipped without it."""


def _import_package() -> list[object]:
    """Import every module of the package (not ``__main__``) and return them all.

    A module whose import fails only because a package of the ``notebook`` extra is missing is
    skipped (issue #262); every other import error is raised.
    """
    for info in pkgutil.walk_packages(morphobricks_preprocessing.__path__, PACKAGE + "."):
        if info.name.endswith(".__main__"):
            continue
        try:
            importlib.import_module(info.name)
        except ImportError as exc:
            if not _needs_notebook_extra(exc):
                raise
    return [m for name, m in sorted(sys.modules.items()) if name.split(".")[0] == PACKAGE]


def notebook_extra_installed() -> bool:
    """True when every package of the ``notebook`` extra can be imported."""
    return all(_importable(name) for name in NOTEBOOK_EXTRA)


def _importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):  # blocked in sys.modules, or a broken entry
        return False


def _needs_notebook_extra(exc: BaseException | None) -> bool:
    """True when ``exc`` (or what it was raised from) is a missing ``notebook`` extra package.

    The notebook modules re-raise a missing ipywidgets or matplotlib with an install hint, so
    the chain of causes is followed to the :class:`ModuleNotFoundError` itself.
    """
    while exc is not None:
        if isinstance(exc, ModuleNotFoundError):
            return (exc.name or "").split(".")[0] in NOTEBOOK_EXTRA
        exc = exc.__cause__ or exc.__context__
    return False


class FrozenClock:
    """The day every clock read of the package returns, and how many reads there were.

    Parameters
    ----------
    monkeypatch
        The pytest fixture that undoes the replacement after the test.
    day
        The frozen day.
    """

    def __init__(self, monkeypatch, day: dt.date) -> None:
        self.day = day
        self.reads = 0
        clock = self

        class FrozenDate(dt.date):
            @classmethod
            def today(cls):
                clock.reads += 1
                return cls(clock.day.year, clock.day.month, clock.day.day)

        class FrozenDateTime(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                clock.reads += 1
                noon = cls(clock.day.year, clock.day.month, clock.day.day, 12)
                return noon if tz is None else noon.replace(tzinfo=dt.UTC).astimezone(tz)

            @classmethod
            def utcnow(cls):
                return cls.now(dt.UTC).replace(tzinfo=None)

            @classmethod
            def today(cls):
                return cls.now()

        self.patched: list[str] = []
        replacements = (("date", dt.date, FrozenDate), ("datetime", dt.datetime, FrozenDateTime))
        for module in [*_import_package(), dt]:
            for name, real, frozen in replacements:
                if vars(module).get(name) is real:
                    monkeypatch.setattr(module, name, frozen)
                    self.patched.append(f"{module.__name__}.{name}")

    def move(self, days: int) -> None:
        """Move the frozen day by ``days``."""
        self.day += dt.timedelta(days=days)
