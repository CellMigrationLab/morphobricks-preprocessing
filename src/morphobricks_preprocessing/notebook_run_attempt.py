"""The start of a press of the run step: what an earlier press showed is forgotten (#517).

Moved out of :mod:`.notebook_run` (v0.9.1, review bc F2) to keep it under the 600-line rule.
A press that is refused before the run starts (settings that cannot be used) forgets the
earlier result too: no earlier result stays on screen, or in ``result``, as if it were this
press's. The files an earlier run wrote are not touched.
"""

from __future__ import annotations

from . import notebook_style as ui
from .notebook_progress import BUSY_NOTE, Wait


class _RunAttempt:
    """Clear the earlier press's result; start a new one with its wait."""

    def _clear_attempt(self) -> None:
        """Forget the earlier press's result and what it showed: a new press has started."""
        self.result = None
        self.run_next.value = ""
        self.run_checks.clear()
        self.run_lock.hide()
        self.run_report.clear()
        self.run_tiles.value = self.run_issues.value = ""
        ui.set_visible(self.progress, False)
        self._mask_step_stale()

    def _start_run(self) -> None:
        """Clear the results of an earlier run and show that this one is starting."""
        self._busy(self.run_button, "Computing…")
        self._clear_attempt()  # #517: a new attempt; an earlier one's result is not this one's
        self.run_next.value = BUSY_NOTE
        ui.set_visible(self.progress, True)
        self.progress.value, self.progress.max, self.progress.bar_style = 0, 1, "info"
        self._run_wait = Wait(self.run_status, bar=False)  # the bar is the widget above
        self._run_wait.start("Preparing: checking the folders and the image headers again")


__all__ = ["_RunAttempt"]
