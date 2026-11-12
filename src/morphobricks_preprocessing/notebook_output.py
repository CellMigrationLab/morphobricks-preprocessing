"""The notebook's output-folder cards: the stale-lock button and the run report (PP-10).

A run, ``check``, a design answer and a stated pixel size each hold the output folder's
lock while they write (design v0.4 §1). When another holder has it, the step shows
:class:`_LockCard`: who holds it (computer, process ID, start time) and a button that
removes the lock of a stopped run with the same rule as ``--remove-stale-lock``
(:func:`.run.lock.remove_stale_lock`): only on that click, never the lock of a process
still running on this computer. :class:`_ReportView` shows the report a check or a run
wrote (``discovery_report.txt``), with where it is.
"""

from __future__ import annotations

import html
import json
import logging
from pathlib import Path

import ipywidgets as widgets

from . import notebook_style as ui
from .discovery import default_output_folder
from .discovery_report import REPORT_FILENAME, REPORT_TEXT_FILENAME
from .report_view import report_html
from .run import (
    LockHolder,
    OutputLockedError,
    Preflight,
    read_lock,
    remove_stale_lock,
    write_check_report,
)
from .run.check_step import suggested_review

DRIVE_LIMIT = (
    "On Google Drive the lock cannot guard an output folder across computers or Colab "
    "sessions: Drive copies the lock file with a delay, so use one session per output folder."
)
"""Why the lock is not enough on Google Drive (plan §12 question 58)."""


_HOLDERS = (
    "Another run, check or notebook is writing to this output folder, or one was stopped (for "
    "example Colab disconnected) and left its lock behind. Wait for a run that is still going."
)
"""Who may hold the output folder's lock, for :class:`_LockCard`."""


def holder_lines(holder: LockHolder) -> list[str]:
    """Name the holder of a lock: its computer, process ID and start time."""
    if not holder.pid:
        return ["The lock file cannot be read, so its holder is unknown."]
    return [
        f"Computer: {holder.host or 'unknown'}",
        f"Process ID: {holder.pid}",
        f"Started: {holder.started_at or 'unknown'} (UTC)",
    ]


class _LockCard:
    """The refusal ``output_locked`` and the button that removes the lock of a stopped run."""

    def __init__(
        self,
        again: str,
        header: str = "The output folder is locked",
        explanation: str = _HOLDERS,
        who: str = "Another run",
    ) -> None:
        """Build the card, hidden; ``again`` names the button to press after a removal.

        ``header`` and ``explanation`` (HTML) name the folder and who may hold its lock;
        the defaults are the output folder's (a run, a check or a notebook); ``who`` names
        the holder in the refusal.
        """
        self.again, self.who = again, who
        self.output: Path | None = None
        self.shown: LockHolder | None = None
        self.holder = widgets.HTML()
        self.button = ui.button(
            " Remove the lock of a stopped run",
            style="danger",
            icon="unlock",
            tooltip="Only when no other run or notebook uses this output folder",
        )
        self.result = widgets.HTML()
        self.card = ui.card(
            header,
            [
                self.holder,
                ui.help_text(
                    f"{explanation} Click the button only if you are sure none is going on: "
                    "it never removes the lock of a process still running on this computer. "
                    f"{DRIVE_LIMIT}"
                ),
                self.button,
                self.result,
            ],
            visible=False,
        )
        self.button.on_click(lambda _: self.remove())

    def show(self, output: Path) -> None:
        """Show who holds the lock of ``output``; the user decides whether to remove it."""
        self.output = output
        holder = self.shown = read_lock(output)
        self.result.value = ""
        if holder is None:
            self.hide()
            return
        self.holder.value = ui.status(
            "error",
            f"{self.who} holds the lock of {output}. Nothing was written.",
            holder_lines(holder),
        )
        self.button.disabled = False
        ui.set_visible(self.card, True)

    def hide(self) -> None:
        """Hide the card (no refusal to show)."""
        self.result.value = ""
        ui.set_visible(self.card, False)

    def remove(self) -> LockHolder | None:
        """Remove the lock the user says is stale; keep one held by a running process here.

        Only the holder the card names is removed: a lock taken since by another run is shown
        instead, for the user to decide again.
        """
        if self.output is None:
            return None
        if read_lock(self.output) not in (None, self.shown):
            self.show(self.output)
            self.result.value = ui.status(
                "warn",
                "Nothing was removed: another run took the lock since it was shown.",
                ["Its holder is named above. Wait for it, or click again if it is stopped too."],
            )
            return None
        try:
            holder = remove_stale_lock(self.output)
        except OutputLockedError as exc:
            self.result.value = ui.status(
                "error",
                "The lock was kept: its process is still running on this computer.",
                [*holder_lines(exc.holder), "Wait for that run to finish."],
            )
            return None
        self.button.disabled = True
        if holder is None:
            self.result.value = ui.status("info", "There is no lock any more.", [self.again])
        else:
            self.result.value = ui.status(
                "ok",
                "The lock of the stopped run was removed.",
                [*holder_lines(holder), self.again],
            )
        return holder


class _ReportView:
    """Where the report of the last check or run is, and its text, folded."""

    def __init__(self) -> None:
        """Build the view, empty."""
        self.where = widgets.HTML()
        self.text = widgets.HTML()
        self.box = ui.accordion("Show the report", [self.text])
        self.widget = widgets.VBox([self.where, self.box])
        ui.set_visible(self.box, False)

    def show(self, output: Path, what: str) -> None:
        """Show the report ``what`` ("The check", "The run") wrote to ``output``."""
        path = output / REPORT_TEXT_FILENAME
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            self.clear()
            return
        self.where.value = ui.status(
            "info",
            f"{what} wrote its report.",
            [
                f"Readable: <b>{html.escape(str(path))}</b>",
                f"For tools: <b>{html.escape(str(output / REPORT_FILENAME))}</b>",
            ],
            raw=True,
        )
        try:
            data = json.loads((output / REPORT_FILENAME).read_text(encoding="utf-8"))
            self.text.value = report_html(data, text)
        except (OSError, ValueError, AttributeError, TypeError):
            # The data file cannot be read as written: the plain text, as before.
            self.text.value = f"<pre style='white-space:pre-wrap'>{html.escape(text)}</pre>"
        self.box.selected_index = None
        ui.set_visible(self.box, True)

    def flat(self) -> None:
        """Show the report in the page itself (the stepper folds the whole view already)."""
        self.widget.children = [self.where, self.text]

    def clear(self) -> None:
        """Show no report."""
        self.where.value = self.text.value = ""
        ui.set_visible(self.box, False)


class _OutputCards:
    """The lock cards and report views of steps 1 and 3, and the check's report."""

    def _build_output_cards(self) -> None:
        """Build the lock card and the report view of each step."""
        self.folder_lock = _LockCard("Then click Check folder again.")
        self.run_lock = _LockCard("Then click Compute histograms again.")
        self.check_report = _ReportView()
        self.run_report = _ReportView()

    def _output_path(self, root: Path) -> Path:
        """Return the output folder in use for the experiment at ``root``, resolved."""
        output = self._output_folder()
        return (Path(output) if output else default_output_folder(root)).resolve()

    def _write_check(self, pre: Preflight, missing_as_singleton: bool = False) -> None:
        """Write the check's report, as the command line's ``check`` writes it.

        Only for this notebook's own output folder: an embedding brick that hides it
        (``show_output=False``) gets the check alone. No design is written: the run derives
        ``experiment_design.csv`` from the published description. Images lacking some axes
        are read by ``missing_as_singleton``, the answer step 2
        shows (the earlier run's, else no), as ``check --missing-as-singleton`` reads them.
        """
        self.folder_lock.hide()
        self.check_report.clear()
        if not self._show_output:
            return
        checked, review = suggested_review(pre, missing_as_singleton=missing_as_singleton)
        output = self._output_path(pre.discovery.root)
        try:
            report, refusal = write_check_report(checked, output, review)
        except OSError as exc:
            logging.getLogger(__name__).warning("Cannot write the check report", exc_info=True)
            self.check_report.where.value = ui.status(
                "warn",
                "The check could not write its report to the output folder.",
                [str(exc), "Choose an output folder you can write to; the run needs one too."],
            )
            return
        if refusal is not None and refusal.code != "output_locked":
            # output_inside_masks, of a recorded masks folder
            self.check_report.where.value = ui.status(
                "error", "The check wrote nothing.", [refusal.message, refusal.action]
            )
        elif refusal is not None:
            self.folder_lock.show(output)
        elif report is not None:
            self.check_report.show(output, "The check")
