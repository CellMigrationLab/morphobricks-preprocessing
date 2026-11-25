"""Restore the earlier work of an output folder as soon as it is set (owner, 25 November 2026).

When the output folder is typed or chosen (or follows a new experiment folder) and no check
is on screen, the notebook looks at what the folder holds (:func:`.earlier_work.earlier_work`)
and, when it holds earlier work of this experiment folder, restores it through the paths that
already exist, then says exactly what it restored, from when, and what it read again:

1. **The folder check.** The check runs as *Check folder* runs it, but the header of an image
   whose fingerprint equals the one in ``check_headers.json`` is the recorded header
   (:mod:`.check_record`); every other image is read. A record of another experiment folder,
   of another version of the software, or that cannot be read is not used: every header is
   read again, and the message says why. Mask headers, the conversion record and each TIFF's
   projection marker are read as the check always reads them.
2. **The answers.** The check opens the saved draft and the saved description as *Check
   folder* opens them (:mod:`.notebook_described`, :mod:`.notebook_description_saved`), and
   the earlier run's settings fill the inputs (:mod:`.notebook_prefill`). The steps open on
   the first step still to do; nothing is confirmed for the user (the axes, for one, are
   offered for one click).
3. **A finished run.** When the run manifest is of this experiment, of the saved description
   as it is, with every image unchanged, and ``RunInputs.open`` opens it
   (:func:`.earlier_work.run_mismatch`), Run shows its report, its intensity checks and the
   next steps, and the combined notebook's Cellpose cell takes it
   (:func:`.notebook_cellpose_app.app_folders`, which opens it through ``RunInputs``).

The experiment folder, when empty, is taken from the earlier work (its recorded root); a
different experiment folder, or a record and a run that name different folders, restore
nothing. *Start fresh* forgets what was restored: no file is changed or deleted. Another output
folder clears what was restored from the first, until the user answers, confirms, saves or runs
(:meth:`_RestoreEarlier._acted`): then it is their work, kept as after a manual check. The folder
boxes are read once typed (``continuous_update=False``), never at each key.
"""

from __future__ import annotations

import html
import traceback
from pathlib import Path

import ipywidgets as widgets
from morphobricks_normalization.csv_io import OUTPUT_FILENAME

from . import notebook_style as ui
from .check_record import CHECK_RECORD_FILENAME
from .description_file import DRAFT_FILENAME
from .earlier_work import WHAT, EarlierWork, earlier_work, run_mismatch
from .manifest import MANIFEST_FILENAME
from .notebook_stepper import StepState
from .validation import plural

SHOWN_NAMES = 5
"""How many images read again the message names."""
FRESH_HELP = "Forget what was restored and start a new session; no file is changed or deleted"
RUN_NEXT = (
    "Your run is complete: go on to the next steps.",
    "The histogram table is ready for later MorphoBricks bricks. In the Preprocessing and "
    "Cellpose notebook, run cell 4 (Segment with Cellpose) for this run. To change an answer, "
    "open its step: Run then updates the table.",
)


class _RestoreEarlier:
    """The restore of an output folder's earlier work, its message and *Start fresh*."""

    def _build_restore(self) -> None:
        """Build the message card and *Start fresh* (hidden until earlier work is found)."""
        self._restore_key: tuple | None = None
        self._fresh_for: Path | None = None
        self._restoring = False
        self._restored_from: Path | None = None
        self.restored_run: Path | None = None
        self.restore_status = widgets.HTML()
        self.restore_fresh = ui.button(" Start fresh", icon="refresh", tooltip=FRESH_HELP)
        self.restore_fresh.on_click(lambda _: self._start_fresh())
        self.restore_box = widgets.VBox([self.restore_status, self.restore_fresh])
        ui.set_visible(self.restore_box, False)

    # The trigger -------------------------------------------------------------------------

    def _restore_earlier_work(self) -> None:
        """Restore the output folder's earlier work, once per output and experiment folder."""
        if self._restoring or not hasattr(self, "restore_box") or not self._show_output:
            return  # an embedding brick that hides the output folder writes nothing
        target = self._prefill_target()
        folder = ui.clean_path(self.folder.value)
        if (target, folder) == self._restore_key:
            return
        self._restore_key = (target, folder)
        if self._restored_from is not None and self._restored_from != target:
            before = self._restored_from
            self._clear_checked(ui.status("info", "The output folder changed."))
            self._say(
                "info",
                "The output folder changed: nothing restored from the earlier one is used.",
                [f"It was {before}. No file was changed or deleted."],
            )
        if target is None or self.preflight is not None or self._fresh_for == target:
            return
        self._restoring = True
        try:
            self._restore(target, folder)
        except Exception as exc:  # a restore that fails never stops the notebook
            self.log.lines.append(traceback.format_exc())
            self._clear_checked("")
            self._say(
                "error",
                "Earlier work was found, but it could not be restored.",
                [
                    f"{type(exc).__name__}: {exc}",
                    "Nothing of it is used, and no file was changed. Click Check folder to "
                    "check the folder; open Technical details for the full report.",
                ],
                fresh=False,
            )
            self._show_log()
        finally:
            self._restoring = False

    def _restore(self, target: Path, folder: str) -> None:
        """Restore the earlier work in ``target`` for the experiment ``folder`` (may be empty)."""
        try:
            work = earlier_work(target)
        except OSError as exc:
            self._say("error", "The output folder could not be read.", [str(exc)], fresh=False)
            return
        if not work.exists:
            ui.set_visible(self.restore_box, False)
            return
        root = self._restore_root(work, folder)
        if root is None:
            return
        self._restore_key = (target, ui.clean_path(self.folder.value))
        why_not = work.record_problem or (
            "there is no recorded folder check"
            if work.record is None
            else work.record.problem(root)
        )
        recorded = None if why_not else work.record.headers
        pre = self._run_check(recorded)
        if pre is None:  # the check said why, under its button
            self._say(
                "error",
                f"Earlier work from {work.latest} was found, but the folder could not be checked.",
                ["Read the message under the check button.", "No file was changed or deleted."],
                fresh=False,
            )
            return
        self._restored_from = target
        self._scanned_by_restore(pre)
        run_line = self._restore_run(work, pre)
        opened = self._go_restored()
        self._say_restored(work, pre, why_not, run_line, opened)

    def _restore_root(self, work: EarlierWork, folder: str) -> Path | None:
        """Return the experiment folder to restore, filling an empty box; None and say why."""
        root = work.experiment_root
        found = ", ".join(WHAT[name] for name, _ in work.found)
        if len(work.roots) > 1:
            self._say(
                "warn",
                f"Found earlier work from {work.latest}, but it names two experiment folders.",
                [
                    f"The recorded check is of {work.roots[0]}; the run is of {work.roots[1]}. "
                    "They disagree, so nothing was restored. Enter the experiment folder and "
                    "click Check folder.",
                ],
                fresh=False,
            )
            return None
        if root is None:
            self._say(
                "info",
                f"Found earlier work from {work.latest}: {found}.",
                [
                    *([work.record_problem] if work.record_problem else []),
                    "The experiment folder it is of is not recorded, so nothing was restored "
                    "yet. Enter the experiment folder and click Check folder to read it.",
                ],
                fresh=False,
            )
            return None
        if folder and Path(folder).resolve() != root:
            self._say(
                "info",
                f"Found earlier work from {work.latest}, of another experiment folder.",
                [
                    f"It is of {root}; nothing was restored. Choose another output folder for "
                    "this experiment, or that experiment folder.",
                ],
                fresh=False,
            )
            return None
        if not folder:
            if not root.is_dir():
                self._say(
                    "warn",
                    f"Found earlier work from {work.latest}, but its experiment folder is gone.",
                    [f"{root} is not found; nothing was restored. Enter the experiment folder."],
                    fresh=False,
                )
                return None
            self.folder.value = str(root)  # from the record; the box can be changed
        return root

    # What a restored check shows ---------------------------------------------------------

    def _scanned_by_restore(self, pre: object) -> None:
        """Count the restored check's folder walk as the stepper's scan (the check is enabled)."""
        if getattr(self, "_scanned", "absent") == "absent":
            return
        self._scan, self._scanned = pre.discovery, self._scan_inputs()
        self._refresh_scan_gate()

    def _restore_run(self, work: EarlierWork, pre: object) -> str:
        """Show a finished, current run on Run; return the line about the run ("" without)."""
        when = dict(work.found).get(MANIFEST_FILENAME, "")
        if work.manifest is None:
            if work.manifest_problem:
                return f"The earlier run cannot be used: {work.manifest_problem}."
            return ""
        state = self._published()
        sha = None if state is None or state.changed else state.sha256
        why = run_mismatch(work.output, work.manifest, pre, sha)
        if why:
            return f"The run of {when} is not current ({why}): Run computes what changed."
        self._show_restored_run(work.output, when)
        return f"the finished run of {when}"

    def _show_restored_run(self, output: Path, when: str) -> None:
        """Show the finished run: its files, report, intensity checks and the next steps."""
        self.restored_run = output
        table = output / OUTPUT_FILENAME
        self.run_status.value = ui.status(
            "ok",
            f"The earlier run is complete ({when}).",
            [
                html.escape(f"Table: {table}"),
                html.escape(f"Manifest: {output / MANIFEST_FILENAME}"),
                "It is of the saved description as it is, and no image changed since. Run "
                "again only after changing an answer.",
            ],
            raw=True,
        )
        self.run_report.show(output, "The earlier run")
        self._show_run_checks(table)
        self.csv_file.value = str(table)
        self.run_next.value = ui.next_steps(*RUN_NEXT)

    def _go_restored(self) -> str:
        """Open the finished run, or the first step still to do; return its title ("" none)."""
        stepper = getattr(self, "_stepper", None)
        if stepper is None:
            return ""
        last = len(stepper.steps) - 1
        index = last if self.restored_run is not None else self._first_open()
        stepper.go(index)
        return stepper.steps[stepper.current].title

    def _say_restored(
        self, work: EarlierWork, pre: object, why_not: str, run_line: str, opened: str
    ) -> None:
        """Say what was restored, from when, and what was read again."""
        restored, lines = [], [f"Output folder: {work.output}"]
        reused = set(pre.headers_reused)
        again = sorted(m.relative_path for m in pre.metadata.files if m.relative_path not in reused)
        if reused:
            restored.append("the folder check")
            lines.append(
                f"Folder check: {plural(len(reused), 'image header')} reused, unchanged since "
                f"{dict(work.found).get(CHECK_RECORD_FILENAME, work.latest)}."
            )
        if again:
            named = ", ".join(again[:SHOWN_NAMES]) + (", …" if len(again) > SHOWN_NAMES else "")
            reason = why_not or "new or changed since the recorded check"
            lines.append(f"Read again: {plural(len(again), 'image header')} ({reason}): {named}.")
        masks = pre.masks.files if pre.masks is not None else ()
        if masks:
            lines.append(f"The headers of {plural(len(masks), 'mask')} were read again.")
        restored.extend(self._answers_restored(work, lines))
        if run_line.startswith("the finished run"):
            restored.append(run_line)
        elif run_line:
            lines.append(run_line)
        if opened and self.restored_run is not None:
            lines.append(f"Opened on {opened}: the run is complete; its results are there.")
        elif opened:
            lines.append(f"Opened on {opened}, the first step still to do.")
        lines.append(
            "Check folder again to read every header again. Start fresh forgets what was "
            "restored; no file is changed or deleted."
        )
        what = ", ".join(restored) or "nothing but the folder walk"
        kind = "warn" if again or why_not else "ok"
        self._say(kind, f"Found earlier work from {work.latest}: restored {what}.", lines)

    def _answers_restored(self, work: EarlierWork, lines: list[str]) -> list[str]:
        """Return the answers restored (draft, description, settings), noting what was not."""
        restored = []
        if self._draft_saved:
            restored.append("your saved answers")
        elif work.has(DRAFT_FILENAME):
            lines.append(
                f"The saved answers ({DRAFT_FILENAME}) were not used: see the note on Design."
            )
        state = self._published()
        if state is not None:
            restored.append(f"the saved experiment description ({state.saved_on})")
            if state.changed:
                lines.append("The folders changed since the description was saved: see Images.")
        if getattr(self, "_recorded", None) is not None:
            restored.append("the earlier run's settings")
        return restored

    def _say(self, kind: str, title: str, lines: list[str], *, fresh: bool = True) -> None:
        """Show the restore message, with *Start fresh* when something was restored."""
        self.restore_status.value = ui.status(kind, title, lines)
        ui.set_visible(self.restore_fresh, fresh)
        ui.set_visible(self.restore_box, True)

    # Start fresh, and what clears a restore ----------------------------------------------

    def _start_fresh(self) -> None:
        """Forget what was restored: a new session state; no file is changed or deleted."""
        self._fresh_for = self._prefill_target()
        self._clear_checked(ui.status("info", "Started fresh."))
        self._say(
            "info",
            "Started fresh: nothing restored is used.",
            [
                "No file was changed or deleted.",
                "Check folder reads every image header again. As always, it reads the answers "
                "saved in this output folder; to start without them, choose another output "
                "folder.",
            ],
            fresh=False,
        )

    def _run_check(self, recorded: object = None) -> object:
        """Run the check; a check the user asks for replaces what was restored."""
        if not self._restoring:
            self._forget_restore()
            ui.set_visible(self.restore_box, False)
        return super()._run_check(recorded)

    def _clear_check_views(self) -> None:
        """Forget what was restored with the check it belonged to."""
        super()._clear_check_views()
        self._forget_restore()
        ui.set_visible(self.restore_box, False)

    def _forget_restore(self) -> None:
        """Forget the restored output folder and run, and what Run showed of that run."""
        self._restored_from = None
        if self.restored_run is not None:
            self.restored_run = None
            self.run_status.value = self.run_next.value = ""
            self.run_report.clear()
            self.run_checks.clear()

    def _start_run(self) -> None:
        super()._start_run()
        self.restored_run = None  # this run replaces it
        self._acted()

    # The user's own work after a restore makes it theirs, as after a manual check -------

    def _acted(self) -> None:
        """Keep the restored state as the user's own: another output folder no longer clears it."""
        if not self._restoring:
            self._restored_from = None

    def _save_answers(self, answers: object, *, rebuild: bool = True) -> bool:
        saved = super()._save_answers(answers, rebuild=rebuild)
        self._acted()
        return saved

    def _toggle_confirmation(self) -> None:
        super()._toggle_confirmation()
        self._acted()

    def _confirm_quick_axes(self) -> bool:
        confirmed = super()._confirm_quick_axes()
        self._acted()
        return confirmed

    def save_experiment(self) -> bool:
        """Check and save the experiment (see :meth:`._CheckSaveScreen.save_experiment`)."""
        saved = super().save_experiment()
        self._acted()
        return saved

    def _refresh_run_step(self) -> None:
        super()._refresh_run_step()
        if self.restored_run is not None and self.result is None:
            self.run_next.value = ui.next_steps(*RUN_NEXT)

    def _run_state(self) -> StepState:
        if self.restored_run is not None and self.result is None:
            return StepState(True, "the earlier run is complete")
        return super()._run_state()


__all__ = ["FRESH_HELP", "RUN_NEXT", "_RestoreEarlier"]
