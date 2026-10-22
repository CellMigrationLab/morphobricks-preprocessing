"""Deprecated: moved to ``morphobricks_normalization.pooling``.

Its public names stay importable from here for one version and warn on first use
(see :mod:`morphobricks_preprocessing._moved`). Import them from
``morphobricks_normalization.pooling`` instead.
"""

from __future__ import annotations

from ._moved import module_attributes

__getattr__, __dir__ = module_attributes("pooling")
