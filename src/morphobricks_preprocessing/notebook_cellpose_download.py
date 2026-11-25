"""What the notebook shows while Cellpose loads a model: one progress bar, no log noise.

Cellpose 4.2.1.1 downloads built-in weights with ``cellpose.utils.download_url_to_file``, which
draws a plain-text ``tqdm`` bar (in a notebook, a new line of text at each update), and logs
each step at INFO through the ``cellpose`` loggers. While a model loads,
:func:`quiet_cellpose` only changes how that is **shown**:

- the bar is ``tqdm.auto``'s (one widget bar in a notebook), same total, unit and updates;
- the ``cellpose`` loggers' INFO lines are dropped and their warnings are collected, to be
  shown in one card afterwards (never lost).

Nothing about **what** is loaded changes: the same URL, file and bytes, and the checks of
:func:`.segmentation_cellpose_model.load_model` (no download without the user's tick, no model
substitution, no device fallback) run unchanged. Everything is put back when the load ends,
also when it fails. Cellpose is imported here only when the pinned release is installed (the
load imports it right after anyway); otherwise nothing is patched and the load refuses as
before.
"""

from __future__ import annotations

import importlib
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType

from . import segmentation_cellpose_model as model_module

LOGGER = "cellpose"
"""The parent of every Cellpose logger (``cellpose.models``, ``cellpose.core``, ...)."""
BAR_LABEL = "Downloading the weights"
CPDINO_IMPORT = "Could not import CPDINO"
"""How Cellpose 4.2.1.1's ``cellpose.models`` starts the warning it logs at import when DINOv3
is not importable; the warning goes on to recommend ``pip install git+.../dinov3`` unpinned."""
CPDINO_NOTE = (
    "Cellpose could not import DINOv3. Only the cpdino models need it: for those, tick "
    "Install DINOv3 now (it installs the pinned commit), then restart the runtime. The other "
    "models do not use it."
)
"""What is shown instead: the notebook's pinned installation (``install_dinov3``), never an
unpinned one."""


class _Collect(logging.Handler):
    """Keep the text of every warning or error logged while it is attached."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if text.startswith(CPDINO_IMPORT):
            text = CPDINO_NOTE  # never an unpinned install: the notebook's own tick instead
        self.lines.append(f"{record.levelname.title()}: {text}")


def _cellpose_utils() -> ModuleType | None:
    """Return ``cellpose.utils`` of the pinned release, or None (nothing is then patched)."""
    loaded = sys.modules.get("cellpose.utils")
    if loaded is not None:
        return loaded
    if model_module.installed_version("cellpose") != model_module.CELLPOSE_VERSION:
        return None
    try:
        return importlib.import_module("cellpose.utils")
    except ImportError:
        return None


def _auto_bar() -> type | None:
    """Return ``tqdm.auto.tqdm`` (Cellpose depends on tqdm), or None where it does not import."""
    try:
        return importlib.import_module("tqdm.auto").tqdm
    except (ImportError, AttributeError):
        return None


def notebook_bar(*args: object, **kwargs: object) -> object:
    """Return ``tqdm.auto``'s bar with Cellpose's own arguments and a plain label."""
    kwargs.setdefault("desc", BAR_LABEL)
    return _auto_bar()(*args, **kwargs)


@contextmanager
def quiet_cellpose() -> Iterator[list[str]]:
    """Show Cellpose's download as one ``tqdm.auto`` bar and collect its warnings.

    Yields
    ------
    list of str
        The warnings Cellpose logged during the block (``"Warning: ..."``), filled as it runs.
    """
    logger = logging.getLogger(LOGGER)
    handler, propagate = _Collect(), logger.propagate
    logger.addHandler(handler)  # first: importing Cellpose below may already log
    logger.propagate = False
    utils, original = None, None
    try:
        utils = _cellpose_utils()
        if utils is not None and _auto_bar() is not None:  # else Cellpose's own bar stays
            original = getattr(utils, "tqdm", None)
        if original is not None:
            utils.tqdm = notebook_bar
        yield handler.lines
    finally:
        if original is not None:
            utils.tqdm = original
        logger.removeHandler(handler)
        logger.propagate = propagate


__all__ = ["BAR_LABEL", "CPDINO_IMPORT", "CPDINO_NOTE", "LOGGER", "notebook_bar", "quiet_cellpose"]
