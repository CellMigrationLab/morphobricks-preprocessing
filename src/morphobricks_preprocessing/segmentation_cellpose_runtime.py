"""Whether the Cellpose, torch and DINOv3 code loaded in this runtime is the code on disk.

Part of :mod:`.segmentation_cellpose_model` (external review bc of #582, B7). The installed
metadata says what is on disk; a module already imported stays the code it was when imported.
``pip`` replacing Cellpose, torch or DINOv3 under a running kernel leaves the old code in
``sys.modules`` while the metadata names the new version: the old code must not run, nor be
recorded as the pinned one. Each check here compares what is loaded with what is installed and
asks for a restart only when they differ (an unchanged runtime is never asked to restart).

- Cellpose: ``cellpose.version.version``, read from the metadata when Cellpose was imported.
- torch: ``torch.__version__`` against its installed version.
- DINOv3: the commit installed when ``cellpose.vit`` (which imports DINOv3 once) was imported
  through this brick, against the commit installed now; and both network constructors Cellpose
  uses (``dinov3_vitl16``, ``dinov3_vitb16``) imported into ``cellpose.vit``. A Cellpose
  imported before this brick imported it has no recorded DINOv3 commit: only the constructors
  are checked then.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from .segmentation_record import SegmentationError

RESTART = (
    "{what} was replaced on disk after this runtime loaded it (loaded: {loaded}; installed "
    "now: {installed}), so the code that would run is not the installed one. Restart the "
    "runtime (Colab: Runtime > Restart session), run the cells again and press the button "
    "again; nothing is downloaded again."
)
CONSTRUCTORS = {"cpdino": "dinov3_vitl16", "cpdino-vitb": "dinov3_vitb16"}
"""The DINOv3 constructor each model's network is built with (Cellpose 4.2.1.1 ``vit.py``)."""

_imported_dinov3: list[str | None] = []
"""The DINOv3 commit installed when this brick imported ``cellpose.vit`` (empty: not seen)."""


def remember_dinov3(commit: str | None) -> None:
    """Record the DINOv3 commit installed as ``cellpose.vit`` is imported (and DINOv3 with it)."""
    _imported_dinov3[:] = [commit]


def check_loaded(installed_version: Callable[[str], str | None]) -> None:
    """Refuse a loaded Cellpose or torch that is not the installed one (a restart is needed).

    Parameters
    ----------
    installed_version
        Returns a distribution's installed version, or None.

    Raises
    ------
    SegmentationError
        Naming what was replaced, what is loaded and what is installed.
    """
    loaded = getattr(sys.modules.get("cellpose.version"), "version", None)
    _same("Cellpose", loaded, installed_version("cellpose"))
    torch = sys.modules.get("torch")
    _same("PyTorch", getattr(torch, "__version__", None), installed_version("torch"))


def check_dinov3_loaded(vit: object, model: str | None, commit: str) -> None:
    """Refuse a DINOv3 loaded at another commit, or without the constructor ``model`` needs.

    Raises
    ------
    SegmentationError
        For a DINOv3 replaced since it was imported, or a missing constructor.
    """
    if _imported_dinov3 and _imported_dinov3[0] not in (None, commit):
        raise SegmentationError(
            RESTART.format(what="DINOv3", loaded=_imported_dinov3[0], installed=commit)
        )
    needed = [CONSTRUCTORS[model]] if model in CONSTRUCTORS else list(CONSTRUCTORS.values())
    missing = [name for name in needed if not hasattr(vit, name)]
    if missing:  # called once the DINOv3 import itself is known to have worked
        raise SegmentationError(
            f"The DINOv3 loaded here lacks {', '.join(missing)}, which Cellpose needs for "
            f"{model or 'the cpdino models'}: it is not the pinned DINOv3. Nothing was loaded."
        )


def _same(what: str, loaded: str | None, installed: str | None) -> None:
    """Refuse when both versions are known and differ."""
    if loaded is not None and installed is not None and loaded != installed:
        raise SegmentationError(RESTART.format(what=what, loaded=loaded, installed=installed))


__all__ = ["CONSTRUCTORS", "RESTART", "check_dinov3_loaded", "check_loaded", "remember_dinov3"]
