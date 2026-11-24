"""Installing Cellpose on demand, for a notebook whose first cell installs preprocessing only.

The combined notebook (``MorphoBricks_Preprocessing_Cellpose.ipynb``, #441) installs
preprocessing in cell 1, exactly as the Preprocessing notebook does, and Cellpose (with PyTorch)
only when the segmentation cell is used: :func:`install_cellpose` runs ``pip`` for the pinned
:data:`.segmentation_cellpose_model.CELLPOSE_VERSION`, and only when the user ticked the
installation (the consent, as for the model download and DINOv3). It keeps the rules of the
Cellpose notebook's install cell: the release pinned, never DINOv3, and a restart asked for
when ``pip`` changed a package the running kernel already loaded (:data:`LOADED_BY_KERNEL`),
never a guess that the old copy still works. Nothing here imports Cellpose or torch.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from collections.abc import Mapping

from . import segmentation_cellpose_model as model
from .segmentation_cellpose_model import CELLPOSE_VERSION
from .segmentation_record import SegmentationError

LOADED_BY_KERNEL = {"numpy": "numpy", "opencv-python": "cv2", "opencv-python-headless": "cv2"}
"""Packages ``pip`` may upgrade under a running kernel, each with the module that stays loaded
(as in the Cellpose notebook's install cell)."""
NOT_TICKED = (
    "Tick the installation first: Cellpose {version} and PyTorch are large downloads and are "
    "installed only when you ask."
)
"""The refusal of an installation the user did not tick (``{version}``: the pinned release)."""
RESTART = (
    "The installation changed {changed} while the runtime already has it loaded. Restart the "
    "runtime (Colab: Runtime > Restart session), then run cell 1, cell 3 and this cell again: "
    "Cellpose is installed and nothing more is installed."
)
"""The stop when ``pip`` replaced a loaded package (``{changed}``: what changed, and how)."""


def cellpose_ready() -> bool:
    """Return whether the pinned Cellpose release is installed (nothing is imported).

    Read through :func:`.segmentation_cellpose_model.installed_version`, the check the model
    step makes before it imports Cellpose.
    """
    return model.installed_version("cellpose") == CELLPOSE_VERSION


def cellpose_install_command() -> list[str]:
    """Return the ``pip`` command that installs the pinned Cellpose release (with PyTorch)."""
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        f"cellpose=={CELLPOSE_VERSION}",
    ]


def _changed(before: Mapping[str, str | None]) -> list[str]:
    """Return each package of :data:`LOADED_BY_KERNEL` pip changed while its module is loaded."""
    return [
        f"{name} {before[name]} -> {model.installed_version(name)}"
        for name, module in LOADED_BY_KERNEL.items()
        if before[name] and model.installed_version(name) != before[name] and module in sys.modules
    ]


def install_cellpose(ticked: bool) -> str:
    """Install the pinned Cellpose release when it is not installed, if ``ticked``.

    Parameters
    ----------
    ticked
        Whether the user ticked the installation (their consent to the download).

    Returns
    -------
    str
        The Cellpose version now installed (:data:`CELLPOSE_VERSION`).

    Raises
    ------
    SegmentationError
        Without the tick (nothing is installed), when ``pip`` fails, when it changed a package
        the kernel had loaded (restart the runtime), or when another version is still installed.
    """
    if cellpose_ready():
        return CELLPOSE_VERSION
    if not ticked:
        raise SegmentationError(NOT_TICKED.format(version=CELLPOSE_VERSION))
    before = {name: model.installed_version(name) for name in LOADED_BY_KERNEL}
    try:
        subprocess.run(cellpose_install_command(), check=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        raise SegmentationError(
            f"Cellpose {CELLPOSE_VERSION} could not be installed ({exc}). Check the installation "
            "output and internet access, then press the button again."
        ) from exc
    importlib.invalidate_caches()
    changed = _changed(before)
    if changed:
        raise SegmentationError(RESTART.format(changed=", ".join(changed)))
    found = model.installed_version("cellpose")
    if found != CELLPOSE_VERSION:
        raise SegmentationError(
            f"Cellpose {CELLPOSE_VERSION} is not installed ({found}). Restart the runtime, then "
            "run cell 1, cell 3 and this cell again."
        )
    return found


__all__ = [
    "LOADED_BY_KERNEL",
    "cellpose_install_command",
    "cellpose_ready",
    "install_cellpose",
]
