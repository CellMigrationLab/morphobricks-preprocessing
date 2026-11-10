"""A frozen clock for tests: every ``date`` and ``datetime`` the package can read gives one day.

Conformance plan section 2, ``confirmed_on``: a writer under a frozen clock moved by a day must
write identical bytes for an unchanged model. :class:`FrozenClock` replaces
:class:`datetime.date` and :class:`datetime.datetime` in the :mod:`datetime` module and in every
module of the package that imported them by name, with subclasses whose ``today``, ``now`` and
``utcnow`` return the frozen day (at noon UTC) and count each read.
"""

from __future__ import annotations

import datetime as dt
import importlib
import pkgutil
import sys

import morphobricks_preprocessing

PACKAGE = morphobricks_preprocessing.__name__


def _import_package() -> list[object]:
    """Import every module of the package (not ``__main__``) and return them all."""
    for info in pkgutil.walk_packages(morphobricks_preprocessing.__path__, PACKAGE + "."):
        if not info.name.endswith(".__main__"):
            importlib.import_module(info.name)
    return [m for name, m in sorted(sys.modules.items()) if name.split(".")[0] == PACKAGE]


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
