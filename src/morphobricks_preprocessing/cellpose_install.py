"""Installing Cellpose on demand, for a notebook whose first cell installs preprocessing only.

The combined notebook (``MorphoBricks_Preprocessing_Cellpose.ipynb``, #441) installs
preprocessing in cell 1, exactly as the Preprocessing notebook does, and Cellpose (with PyTorch)
only when the segmentation cell is used: :func:`install_cellpose` runs ``pip`` for the pinned
:data:`.segmentation_cellpose_model.CELLPOSE_VERSION`, and only when the user ticked the
installation (the consent). It keeps the rules of the Cellpose notebook's install cell: the
release pinned, and a restart asked for when ``pip`` changed a package the running kernel
already loaded (:data:`LOADED_BY_KERNEL`), never a guess that the old copy still works.

DINOv3 (v0.9.1): when no DINOv3 is installed, the pinned DINOv3 code
(:func:`.segmentation_cellpose_model.dinov3_install_command`, ``--no-deps``, no weights) is
installed *before* Cellpose, in the same ticked installation. Cellpose 4.2.1.1 imports DINOv3
only once, when its network module is first imported, so a DINOv3 installed later (on the first
``cpdino`` load after a ``cpsam`` one) needed a runtime restart (owner's Colab test of v0.9.0).
A DINOv3 already installed, at any commit, is left as it is; the model step checks its commit.

``pip``'s lines are passed on as it prints them (``output``), so the notebook shows its
progress (owner Colab test, #574), and :func:`installed_distributions` / :func:`install_changes`
say what it installed. Nothing here imports Cellpose or torch.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import re
import subprocess
import sys
from collections.abc import Callable, Mapping

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
    "runtime (Colab: Runtime > Restart session), then {again}: Cellpose is installed and nothing "
    "more is installed."
)
"""The stop when ``pip`` replaced a loaded package (``{changed}``: what changed, and how;
``{again}``: :data:`AGAIN`)."""
AGAIN = (
    "run cells 1 to 3 again (in cell 3, run preprocessing again: the histograms are reused) "
    "and then this cell"
)
"""What to run after a restart in the combined notebook: its cells 1 to 3, then cell 4."""


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


def installed_distributions() -> dict[str, str]:
    """Return the version of every installed distribution, by its normalized name (PEP 503)."""
    found = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"]
        if name:
            found[re.sub(r"[-_.]+", "-", name).lower()] = dist.version
    return found


def install_changes(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
    """Return what an installation added or changed: ``name version`` (``was old``), sorted."""
    return [
        f"{name} {version}" + (f" (was {before[name]})" if name in before else "")
        for name, version in sorted(after.items())
        if before.get(name) != version
    ]


def _pip(command: list[str], output: Callable[[str], object]) -> None:
    """Run ``pip``, telling ``output`` each line it prints (also its errors), as it prints it.

    Like ``subprocess.run``, pip is killed (and waited for) when anything stops the reading,
    an interrupt included: it never goes on installing behind a stopped cell. A byte that is
    not text is replaced, so that a decoding error cannot stop the reading either.

    Raises
    ------
    subprocess.CalledProcessError
        When ``pip`` fails.
    """
    with subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        bufsize=1,
    ) as pip:
        try:
            for line in pip.stdout:
                output(line.rstrip())
        except BaseException:
            pip.kill()
            pip.wait()
            raise
    if pip.returncode:
        raise subprocess.CalledProcessError(pip.returncode, command)


def install_cellpose(ticked: bool, output: Callable[[str], object] = print) -> str:
    """Install the pinned Cellpose release when it is not installed, if ``ticked``.

    When no DINOv3 is installed, the pinned DINOv3 code is installed first (see the module
    docstring), so that a ``cpdino`` model never needs a restart in this runtime.

    Parameters
    ----------
    ticked
        Whether the user ticked the installation (their consent to the download).
    output
        Told each line ``pip`` prints, as it prints it (the notebook shows its progress from
        them, owner Colab test #574); by default printed, where a widget's callback would
        otherwise lose them.

    Returns
    -------
    str
        The Cellpose version now installed (:data:`CELLPOSE_VERSION`).

    Raises
    ------
    SegmentationError
        Without the tick (nothing is installed), when ``pip`` fails (DINOv3's installation
        included: Cellpose is then not installed), when it changed a package
        the kernel had loaded (restart the runtime), or when another version is still installed.
    """
    if cellpose_ready():
        return CELLPOSE_VERSION
    if not ticked:
        raise SegmentationError(NOT_TICKED.format(version=CELLPOSE_VERSION))
    before = {name: model.installed_version(name) for name in LOADED_BY_KERNEL}
    if not model.dinov3_present():
        try:
            _pip(model.dinov3_install_command(), output)
        except (subprocess.CalledProcessError, OSError) as exc:
            raise SegmentationError(
                f"DINOv3 (the code of the cpdino models, installed before Cellpose) could not "
                f"be installed ({exc}); Cellpose was not installed. Check the installation "
                "output and internet access, then press the button again."
            ) from exc
        importlib.invalidate_caches()
    try:
        _pip(cellpose_install_command(), output)
    except (subprocess.CalledProcessError, OSError) as exc:
        raise SegmentationError(
            f"Cellpose {CELLPOSE_VERSION} could not be installed ({exc}). Check the installation "
            "output and internet access, then press the button again."
        ) from exc
    importlib.invalidate_caches()
    changed = _changed(before)
    if changed:
        raise SegmentationError(RESTART.format(changed=", ".join(changed), again=AGAIN))
    found = model.installed_version("cellpose")
    if found != CELLPOSE_VERSION:
        raise SegmentationError(
            f"Cellpose {CELLPOSE_VERSION} is not installed ({found}). Restart the runtime, then "
            f"{AGAIN}."
        )
    return found


__all__ = [
    "LOADED_BY_KERNEL",
    "cellpose_install_command",
    "cellpose_ready",
    "install_cellpose",
    "install_changes",
    "installed_distributions",
]
