"""Deprecated: moved to ``morphobricks_normalization.csv_io``.

Its public names stay importable from here for one version and warn on first use
(see :mod:`morphobricks_preprocessing._moved`). Import them from
``morphobricks_normalization.csv_io`` instead.
"""

from __future__ import annotations

from ._moved import module_attributes

__getattr__, __dir__ = module_attributes("csv_io")
