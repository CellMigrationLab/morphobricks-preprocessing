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

from . import notebook_style as ui
from . import segmentation_cellpose as cp
from .cellpose_install import cellpose_install_command, cellpose_ready, install_cellpose
from .notebook_cellpose_app import SegmentationApp, app_folders, segmentation_app
from .notebook_cellpose_form import CellposeNotebook
from .notebook_render import blocked_line
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
INSTALL_TEXT = (
    "Cellpose {version} is not installed in this environment. It is installed only here, only "
    "when you tick the box: it brings PyTorch, a download of a few gigabytes. Preprocessing "
    "alone never installs it. The command is <b>{command}</b>. DINOv3 is never installed here "
    "(it is a choice of the model step)."
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
    """

    def __init__(
        self, app: object, on_masks_written: Callable[[cp.BatchResult], object] | None
    ) -> None:
        """Read the app's run and masks folder, then mount or offer the installation."""
        super().__init__(layout=widgets.Layout(width="99%"))
        self.app, self._on_masks_written = app, on_masks_written
        self.run_folder, self.masks_folder = app_folders(app)
        self.refused = _refusal(self.run_folder)
        self.segmentation: SegmentationApp | None = None
        self.rechecked_run: Path | None = None
        self.notice = widgets.HTML(self._notice())
        self.install_box = widgets.VBox()
        self.children = [widgets.HTML(ui.STYLE_CSS), self.notice, self.install_box]
        if cellpose_ready():
            self._mount()
        else:
            self.install_box.children = [self._install_card()]

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

    def _mount(self) -> None:
        """Mount the segmentation component on the app's run (opened when both folders are)."""
        self.segmentation = segmentation_app(app=self.app, on_masks_written=self._on_masks_written)
        self.install_box.children = [self.segmentation]
        opener = self.segmentation.workflow.forms["open"]
        if self.run_folder and self.masks_folder and opener.stopped is not None:
            if not self.refused:  # opened with its masks folder, the run is refused
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
        """Install Cellpose (the tick is the consent) and mount the component; False if refused."""
        self.install_status.value = ui.working(f"Installing Cellpose {cp.CELLPOSE_VERSION}…")
        try:
            install_cellpose(self.install_tick.value)
        except cp.SegmentationError as exc:
            self.install_status.value = ui.status("error", "Cellpose is not installed", [str(exc)])
            return False
        self._mount()
        return True


def _refusal(run_folder: str) -> str:
    """Return why the run in ``run_folder`` cannot be opened, or "" (also for no run)."""
    if not run_folder:
        return ""
    try:
        open_run(run_folder)
    except (RunInputsError, cp.SegmentationError) as exc:
        return str(exc)
    return ""


def _refused_card(reason: str) -> str:
    """Return the red card of a run that cannot be segmented: what to do, then why."""
    return ui.status("error", "The run cannot be segmented", [RUN_REFUSED, reason])


def segmentation_step(
    app: object, *, on_masks_written: Callable[[cp.BatchResult], object] | None = None
) -> SegmentationStep:
    """Return cell 4's widget: the Cellpose segmentation of the run of the Preprocessing app.

    Parameters
    ----------
    app
        The Preprocessing app of cell 3 (``PreprocessingNotebook``); its finished run and its
        masks folder rule give the folders (:func:`.notebook_cellpose_app.app_folders`).
    on_masks_written
        Passed to :func:`.notebook_cellpose_app.segmentation_app`.
    """
    return SegmentationStep(app, on_masks_written)


__all__ = ["NO_RUN", "RUN_REFUSED", "SegmentationStep", "segmentation_step"]
