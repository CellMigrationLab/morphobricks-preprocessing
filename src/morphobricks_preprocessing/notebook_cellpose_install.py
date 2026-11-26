"""The progress and the result of installing Cellpose in the combined notebook's cell 4.

The owner's Colab test of 26 November 2026 (issue #574) found the first installation slow and
silent: ``pip`` printed to the kernel's own output, which a widget's button does not show.
:class:`PipProgress` turns each line ``pip`` prints (:func:`.cellpose_install.install_cellpose`
``output``) into the wait of #567 (:class:`.notebook_progress.Wait`): what pip is doing now,
with an elapsed time that ticks; :func:`installed_html` then says what was installed. Nothing
here runs ``pip`` or decides anything.
"""

from __future__ import annotations

import html
import re
from collections import deque
from collections.abc import Iterable

from . import notebook_style as ui
from .notebook_progress import Wait

INSTALLING = "Installing Cellpose {version} (with PyTorch)"
"""The wait's label (``{version}``: the pinned release); what pip does now follows it."""
PIP_STEPS = (
    ("Collecting ", "finding {rest}"),
    ("Downloading ", "downloading {rest}"),
    ("Building wheel", "building {rest}"),
    ("Installing collected packages", "installing the packages (this can take minutes)"),
)
"""The lines of ``pip`` that name a new step of the installation, and what the wait says."""
KEPT_LINES = 20
"""How many of pip's last lines a failed installation shows."""


def pip_step(line: str) -> str | None:
    """Return what the wait says for one line ``pip`` printed, or None for a line it ignores."""
    text = line.strip()
    for start, said in PIP_STEPS:
        if text.startswith(start):
            rest = text[len(start) :].strip(" :")
            return said.format(rest=re.split(r" \(?from ", rest)[0])
    return None


class PipProgress:
    """Show each step ``pip`` reports in a :class:`.notebook_progress.Wait`; keep its last lines.

    Parameters
    ----------
    wait
        The started wait.
    label
        Its label (:data:`INSTALLING`, formatted).
    """

    def __init__(self, wait: Wait, label: str) -> None:
        self.wait, self.label = wait, label
        self.lines: deque[str] = deque(maxlen=KEPT_LINES)

    def line(self, text: str) -> None:
        """Take one line of pip's output: kept, and shown when it names a new step."""
        self.lines.append(text)
        step = pip_step(text)
        if step:
            self.wait.phase(f"{self.label}: {step}")


def installed_html(version: str, took: str, changes: Iterable[str]) -> str:
    """Return the green card saying Cellpose is installed, how long it took and what changed."""
    changes = list(changes)
    listed = ", ".join(changes) if changes else "nothing new (it was all there already)"
    return ui.status(
        "ok",
        f"Cellpose {version} is installed ({took})",
        [f"pip installed or changed: {html.escape(listed)}."],
        raw=True,
    )


def failed_lines(lines: Iterable[str]) -> list[str]:
    """Return pip's last lines for a failed installation's card (HTML), or none."""
    lines = [line for line in lines if line.strip()]
    if not lines:
        return []
    text = html.escape("\n".join(lines))
    return [f"pip's last lines:<pre style='white-space:pre-wrap'>{text}</pre>"]


__all__ = [
    "INSTALLING",
    "KEPT_LINES",
    "PIP_STEPS",
    "PipProgress",
    "failed_lines",
    "installed_html",
    "pip_step",
]
