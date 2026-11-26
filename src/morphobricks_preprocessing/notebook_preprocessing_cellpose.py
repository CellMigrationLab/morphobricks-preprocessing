"""Cell 4 of the combined notebook: segment the run of the Preprocessing app with Cellpose.

``MorphoBricks_Preprocessing_Cellpose.ipynb`` (#441) has the Preprocessing notebook's three
cells (install, Drive, the app), then this cell and the registration
(:mod:`.notebook_mask_register`). :func:`segmentation_step` mounts the one segmentation
component (:func:`.notebook_cellpose_app.segmentation_app`) on the run the app just finished,
with the masks folder the app's own rule gives (the masks folder checked, else the one the run
recorded; :func:`.notebook_cellpose_app.app_folders`). Nothing is guessed:

- with no finished run in the app (none yet, or a run that stopped), the cell says so and names
  cell 3; the run boxes stay blank for an earlier finished run the user types in;
- a run that cannot be opened (images changed since the run, a stopped run) is refused at once,
  with or without a masks folder: the refusal of :func:`.segmentation_cellpose_run.open_run`,
  unchanged, and cell 3 named as the fix;
- Cellpose (with PyTorch) is installed only on the user's tick (:mod:`.cellpose_install`),
  before the component appears; a package the kernel had loaded asks for a restart.

The Preprocessing app (``notebook_ui``) never imports this module, so preprocessing alone never
installs or imports Cellpose or torch.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from pathlib import Path

import ipywidgets as widgets
from IPython import get_ipython
from IPython.display import display

from . import notebook_style as ui
from . import segmentation_cellpose as cp
from .cellpose_install import (
    cellpose_install_command,
    cellpose_ready,
    install_cellpose,
    install_changes,
    installed_distributions,
)
from .notebook_cellpose_app import SegmentationApp, app_folders, segmentation_app
from .notebook_cellpose_form import CellposeNotebook
from .notebook_cellpose_install import INSTALLING, PipProgress, failed_lines, installed_html
from .notebook_open_progress import opening
from .notebook_progress import Wait
from .notebook_render import blocked_line
from .open_checks import Checking, quiet
from .run_inputs import RunInputsError
from .segmentation_cellpose_run import open_run

APP_CELL = 3
"""The combined notebook's cell holding the Preprocessing app."""
NO_RUN = (
    "There is no finished preprocessing run in the app of cell 3 (no run yet, or a run that "
    "stopped). Run preprocessing in cell 3 (step Run) first, then run this cell again. To "
    "segment an earlier finished run instead, type its output folder and the masks folder under "
    "Open the run below."
)
"""What cell 4 says, in a red card, when the app holds no finished run."""
RUN_REFUSED = (
    "The run of cell 3 cannot be segmented as it is. Run preprocessing again in cell 3 (step "
    "Run), then run this cell again."
)
"""What cell 4 says, with the refusal, when the app's run cannot be opened."""
REGISTER_WITH = "run cell 5 (Register the masks)"
"""What the line shown once the masks are written names in this notebook: its cell 5 registers
the masks (:mod:`.notebook_mask_register`)."""
INSTALL_TEXT = (
    "Cellpose {version} is not installed in this environment. It is installed only here, only "
    "when you tick the box: it brings PyTorch, a download of a few gigabytes. Preprocessing "
    "alone never installs it. The command is <b>{command}</b>. When no DINOv3 is installed, "
    "the pinned DINOv3 code (the network code of the cpdino models; no weights) is installed "
    "just before Cellpose, so that a cpdino model never needs a runtime restart."
)


class SegmentationStep(widgets.VBox):
    """Cell 4's widget: what it found in the app, the installation if needed, the component.

    Attributes
    ----------
    run_folder, masks_folder
        The folders taken from the app (blank when it holds no finished run).
    notice
        The card saying which run is used, or why none is or why it cannot be segmented.
    refused
        Why the app's run cannot be opened (:func:`.segmentation_cellpose_run.open_run`'s
        refusal, unchanged), or "".
    segmentation
        The mounted :class:`.SegmentationApp`, or None until Cellpose is installed.
    rechecked_run
        The segmented run's output folder once cell 5 made the app's folder check again for it
        (:mod:`.notebook_mask_register`), which clears the app's run until it runs again; None
        before. Kept here so that cell 5 run again continues.
    registered_manifest
        The SHA-256 of the run manifest cell 5's run published when it registered the masks,
        or None; cell 5 accepts that publication besides the one cell 4 opened.
    """

    def __init__(
        self, app: object, on_masks_written: Callable[[cp.BatchResult], object] | None
    ) -> None:
        """Read the app's run and masks folder, then mount or offer the installation.

        Nothing is opened yet: :meth:`check` opens the run, once this widget can show it.
        """
        super().__init__(layout=widgets.Layout(width="99%"))
        self.app, self._on_masks_written = app, on_masks_written
        self.run_folder, self.masks_folder = app_folders(app)
        self.refused = ""
        self.segmentation: SegmentationApp | None = None
        self.rechecked_run: Path | None = None
        self.registered_manifest: str | None = None
        # Shown by segmentation_step: the next display of this step (the cell's) adds nothing.
        self.skip_next_display = False
        self.notice = widgets.HTML(self._notice())
        self.opening = widgets.HTML()  # the wait of the run's checks without the component
        self.installed = widgets.HTML()  # what the installation installed, once it did
        self.install_box = widgets.VBox()
        self.children = [
            widgets.HTML(ui.STYLE_CSS),
            self.notice,
            self.opening,
            self.installed,
            self.install_box,
        ]
        if cellpose_ready():
            self._mount(open_now=False)
        else:
            self.install_box.children = [self._install_card()]

    def check(self) -> None:
        """Open the app's run once, showing the wait, and say when it cannot be segmented.

        With the component and both folders, **Open the run** opens it (its wait under the
        button); otherwise it is opened here only for its refusal (its wait under the notice).
        Opened once, not twice (owner Colab test, #574): every image of a ``verify_content``
        run is read whole for its SHA-256 at each opening.
        """
        if not self.run_folder:
            return
        if self.segmentation is not None and self.masks_folder:
            self._open_in_form(reraise=True)
            return
        with opening(self.opening) as checking:
            self.refused = _refusal(self.run_folder, checking)
        self.notice.value = self._notice()

    def _ipython_display_(self, **kwargs: object) -> None:
        """Display the widget, unless :func:`segmentation_step` just did (then once nothing).

        :attr:`skip_next_display` is consumed by the next display of this same step only (the
        cell's ``display(segmentation)``); every later display shows it. ipywidgets 7 (Colab)
        displays a widget by ``Widget._ipython_display_``, ipywidgets 8 by
        ``_repr_mimebundle_`` (7 has none): whichever the installed version has.
        """
        if self.skip_next_display:
            self.skip_next_display = False
            return
        shown_by_widget = getattr(super(), "_ipython_display_", None)
        if shown_by_widget is not None:
            shown_by_widget(**kwargs)
            return
        display(self._repr_mimebundle_(), raw=True)

    @property
    def workflow(self) -> CellposeNotebook | None:
        """The segmentation's state (its masks folder and results), or None before it shows."""
        return None if self.segmentation is None else self.segmentation.workflow

    def _notice(self) -> str:
        """Return the card naming the run and masks folder used, or saying there is no run."""
        if not self.run_folder:
            return ui.status("error", "No finished run to segment", [NO_RUN])
        return self._run_card() + (_refused_card(self.refused) if self.refused else "")

    def _run_card(self) -> str:
        """Return the card naming the app's run and its masks folder."""
        masks = self.masks_folder or "none yet: type one below, outside the experiment and the run"
        return ui.status(
            "info",
            "The run of cell 3",
            [
                f"Run: <b>{html.escape(self.run_folder)}</b>",
                f"Masks folder: <b>{html.escape(masks)}</b>",
            ],
            raw=True,
        )

    def _mount(self, open_now: bool = True) -> None:
        """Mount the segmentation component on the app's run; ``open_now``: open it there."""
        self.segmentation = segmentation_app(
            app=self.app,
            on_masks_written=self._on_masks_written,
            register_with=REGISTER_WITH,
            open_now=False,
        )
        self.install_box.children = [self.segmentation]  # on screen before the wait
        if open_now:
            self._open_in_form()

    def _open_in_form(self, reraise: bool = False) -> None:
        """Open the run by **Open the run** when both folders are known; keep its refusal.

        ``reraise``: an error that is not a refusal goes out of the cell (:meth:`check`), as
        it did before the opening moved into the form; a button's shows it under the button.
        """
        opener = self.segmentation.workflow.forms["open"]
        if not (self.run_folder and self.masks_folder):
            return
        opener.submit(reraise=reraise)
        if opener.stopped is not None:
            # A refused masks folder is said under Open the run; the run itself is not refused.
            if not self.refused and not self.segmentation.workflow.folders_refused:
                self.refused = opener.refusal
                self.notice.value += _refused_card(self.refused)

    def _install_card(self) -> widgets.VBox:
        """Return the card that installs Cellpose on the user's tick, then mounts the component."""
        command = " ".join(cellpose_install_command()[2:])
        text = INSTALL_TEXT.format(version=cp.CELLPOSE_VERSION, command=html.escape(command))
        self.install_tick = widgets.Checkbox(
            value=False,
            description=f"Install Cellpose {cp.CELLPOSE_VERSION} (with PyTorch) now",
            indent=False,
            layout=widgets.Layout(width="auto"),
        )
        self.install_button = ui.button(
            f"Install Cellpose {cp.CELLPOSE_VERSION}", icon="download", main=True
        )
        self.install_blocker = widgets.HTML()
        self.install_status = widgets.HTML()
        self.install_tick.observe(lambda _: self._update_install_button(), names="value")
        self.install_button.on_click(lambda _: self.install())
        self._update_install_button()
        return ui.card(
            "Install Cellpose",
            [
                ui.help_text(text),
                self.install_tick,
                widgets.HBox([self.install_button, self.install_blocker]),
                self.install_status,
            ],
        )

    def _update_install_button(self) -> None:
        """Enable *Install* only once ticked, with the visible reason while it is not."""
        ticked = self.install_tick.value
        self.install_button.disabled = not ticked
        self.install_blocker.value = blocked_line([] if ticked else ["tick the installation"])

    def install(self) -> bool:
        """Install Cellpose (the tick is the consent) and mount the component; False if refused.

        The wait says what pip does now, with a ticking time; then a card says what pip
        installed or changed, above the component (owner Colab test, #574).
        """
        label = INSTALLING.format(version=cp.CELLPOSE_VERSION)
        wait = Wait(self.install_status)
        wait.start(f"{label}: starting pip")
        shown, before = PipProgress(wait, label), installed_distributions()
        try:
            with wait.ticking():
                version = install_cellpose(self.install_tick.value, shown.line)
        except cp.SegmentationError as exc:
            lines = [html.escape(str(exc)), *failed_lines(shown.lines)]
            self.install_status.value = ui.status(
                "error", "Cellpose is not installed", lines, raw=True
            )
            return False
        changes = install_changes(before, installed_distributions())
        self.installed.value = installed_html(version, wait.took(), changes)
        self._mount()
        return True


def _refusal(run_folder: str, checking: Checking = quiet) -> str:
    """Return why the run in ``run_folder`` cannot be opened, or "" (also for no run)."""
    if not run_folder:
        return ""
    try:
        open_run(run_folder, checking=checking)
    except (RunInputsError, cp.SegmentationError) as exc:
        return str(exc)
    return ""


def _refused_card(reason: str) -> str:
    """Return the red card of a run that cannot be segmented: what to do, then why."""
    return ui.status("error", "The run cannot be segmented", [RUN_REFUSED, reason])


def _shown_first(step: SegmentationStep) -> None:
    """Display ``step`` now, in a notebook; the cell's own ``display(step)`` then adds nothing."""
    display(step)
    step.skip_next_display = True


def _default_show() -> Callable[[SegmentationStep], object] | None:
    """Return how cell 4 shows its widget first: :func:`_shown_first` in a kernel, else None."""
    return _shown_first if get_ipython() is not None else None


_DEFAULT = object()
"""``show`` not given: :func:`_default_show`."""


def segmentation_step(
    app: object,
    *,
    on_masks_written: Callable[[cp.BatchResult], object] | None = None,
    show: Callable[[SegmentationStep], object] | None | object = _DEFAULT,
) -> SegmentationStep:
    """Return cell 4's widget, its run opened: the Cellpose segmentation of the app's run.

    Parameters
    ----------
    app
        The Preprocessing app of cell 3 (``PreprocessingNotebook``); its finished run and its
        masks folder rule give the folders (:func:`.notebook_cellpose_app.app_folders`).
    on_masks_written
        Passed to :func:`.notebook_cellpose_app.segmentation_app`.
    show
        Called with the widget before the run is opened, so that the wait of its checks is on
        screen (owner Colab test, #574); None: the widget is returned opened, for the caller
        to show. By default, in a notebook, the widget is displayed first, and the cell's
        ``display(segmentation)`` that follows shows nothing more: cell 4 is the same for the
        release it pins and for this code. Outside a notebook: None.
    """
    step = SegmentationStep(app, on_masks_written)
    if show is _DEFAULT:
        show = _default_show()
    if show is not None:
        show(step)
    step.check()
    return step


__all__ = ["NO_RUN", "REGISTER_WITH", "RUN_REFUSED", "SegmentationStep", "segmentation_step"]
