"""Widgets for the biologist-facing notebook.

The notebook ``notebooks/MorphoBricks_Preprocessing.ipynb`` displays the four steps of
:class:`PreprocessingNotebook` together in one widget application:

1. **Folder** - choose the experiment folder, check it, confirm the labels
   taken from the folder names, and confirm the experiment design: which
   biological repeats belong together across conditions, that they are
   comparable, and which conditions are controls; see and state the pixel
   sizes; with a masks folder, see the masks paired with the images and state the
   axes of the mask types that need it. The check writes its report (and the design
   template) to the output folder, as the command line's ``check`` does.
2. **Axes** - review the role of every image axis, look at planes with the
   preview, and confirm; then confirm the image type (fluorescence or RGB).
3. **Run** - check that everything is ready, see what will happen (the mode and
   the inputs that changed since the earlier run), and compute
   or update the histogram table; then the intensity checks of the table, with
   their plots; after a run that recorded masks, the optional mask step
   (**Describe the masks**, as the command line's ``masks``).
4. **Results** - pooled statistics and intensity distributions per group, and the
   intensity checks of any histogram table.

Each step is a page of cards (see :mod:`.notebook_style`): a blue box says what
the step is for, every control has a help line, the main button is at the
bottom, a status card says what happened, and a separate "What next?" box explains
the next action. Later steps stay locked until the earlier ones are confirmed, and
changing the folder resets them. The widgets only collect choices and show results; every check and
computation is done by the same functions the command line and the tests use.
Needs the ``notebook`` extra (``ipywidgets`` and ``matplotlib``).
"""

from __future__ import annotations

import logging
from pathlib import Path

from morphobricks_normalization.ranges import HistogramSettings

try:
    import ipywidgets as widgets
except ImportError as exc:  # pragma: no cover - depends on the environment
    raise ImportError(
        "The notebook interface needs ipywidgets and matplotlib. Install them with: "
        'pip install "morphobricks-preprocessing[notebook]"'
    ) from exc

from . import notebook_style as ui
from .axes import AxisMapping
from .dataset import DatasetReport
from .image_type import ImageTypeReview
from .notebook_axis_preview import _AxisPreview
from .notebook_axis_step import _AxisStep
from .notebook_calibration import _CalibrationCard
from .notebook_channels import _ChannelsStep
from .notebook_check_save import _CheckSaveScreen
from .notebook_convert import _ConvertCard
from .notebook_described import _DescribedRun
from .notebook_description import _DescriptionCard
from .notebook_design import _DesignCard
from .notebook_folder import _FolderStep
from .notebook_mask_step import _MaskStepCard
from .notebook_masks import _MasksCard
from .notebook_masks_screen import _MasksScreen
from .notebook_options import (
    _PACKAGE_LOGGER,
    EXCLUDABLE_CODES,
    HISTOGRAM_RANGE_OPTIONS,
    IMAGE_TYPE_NAMES,
    IMAGE_TYPE_OPTIONS,
    LEVEL_OPTIONS,
    ROLE_OPTIONS,
)
from .notebook_output import _OutputCards
from .notebook_plots import (
    IntensityPanel,
    distribution_png,
    intensity_boxes_png,
    intensity_findings_html,
    plane_png,
    repeat_curves_png,
)
from .notebook_prefill import _Prefill
from .notebook_render import (
    _labels_explanation,
    balance_html,
    issue_count_chips,
    issues_html,
    labels_html,
    plane_summary_html,
    preview_caption,
    role_effect,
)
from .notebook_results import _ResultsStep
from .notebook_run import _RunStep
from .notebook_run_plan import _RunPlan
from .notebook_steps import _StepsFlow
from .notebook_values import display_range
from .planes import PlaneLayout
from .run import Preflight, RunResult

__all__ = [
    "EXCLUDABLE_CODES",
    "HISTOGRAM_RANGE_OPTIONS",
    "IMAGE_TYPE_NAMES",
    "IMAGE_TYPE_OPTIONS",
    "IntensityPanel",
    "LEVEL_OPTIONS",
    "PreprocessingNotebook",
    "ROLE_OPTIONS",
    "balance_html",
    "display_range",
    "distribution_png",
    "intensity_boxes_png",
    "intensity_findings_html",
    "issue_count_chips",
    "issues_html",
    "labels_html",
    "plane_png",
    "plane_summary_html",
    "preview_caption",
    "repeat_curves_png",
    "role_effect",
]


class PreprocessingNotebook(
    _StepsFlow,
    _DescribedRun,
    _CheckSaveScreen,
    _FolderStep,
    _MasksCard,
    _DesignCard,
    _DescriptionCard,
    _ChannelsStep,
    _MasksScreen,
    _CalibrationCard,
    _OutputCards,
    _AxisStep,
    _AxisPreview,
    _ConvertCard,
    _RunStep,
    _MaskStepCard,
    _RunPlan,
    _Prefill,
    _ResultsStep,
):
    """The steps of the notebook, sharing one state.

    Parameters
    ----------
    experiment_folder
        Folder to prefill in step 1.
    output_folder
        Output folder to prefill; empty means the default next to the experiment.

    Every run is described (:mod:`.notebook_described`, Spec ADR 0002 AR10): the experiment is
    saved as ``morphobricks.toml`` on *Check and save* before the run, and a run without it is
    refused (``A_DESCRIPTION_REQUIRED``). There is no undescribed mode.

    Attributes
    ----------
    preflight
        Result of the last folder check, or None.
    mapping
        The axis roles as currently edited in step 2.
    confirmed_mapping
        The axis roles the user confirmed, or None.
    confirmed_image_type
        ``"fluorescence"`` or ``"rgb"`` once the user confirmed the image type
        in step 2, or None.
    design
        The experiment design as answered in step 1, or None until the labels
        are confirmed.
    dataset
        Images per condition and repeat group with the current pairing, and the
        warnings about them.
    result
        Result of the last run, or None.
    """

    def __init__(self, experiment_folder: str = "", output_folder: str = "") -> None:
        self.preflight: Preflight | None = None
        # The pairing card's folders and their repeats (None while the card is closed), and the
        # output folder its answers were read for (issue #98): see notebook_design.
        self.pairing: dict[tuple[str, str], str] | None = None
        self._design_output: Path | None = None
        self.dataset = DatasetReport()
        self.mapping: AxisMapping | None = None
        self.confirmed_mapping: AxisMapping | None = None
        self.confirmed_image_type: str | None = None
        self._image_type_review: ImageTypeReview | None = None
        self.result: RunResult | None = None
        self.log = _LogCapture()
        self._checked_folder = ""
        self._preview_shown = False
        self._preview_drawn = None
        self._show_output = True
        self._show_design = True
        self._layouts_cache: tuple[tuple, dict[str, PlaneLayout]] | None = None
        self._init_prefill()
        self._build_check_save()
        self._build_output_cards()
        self._build_calibration_card()
        self._build_masks_cards()
        self._build_channels_step()
        self._build_masks_screen()
        self._build_folder_step(str(experiment_folder), str(output_folder))
        self._build_axis_step()
        self._build_convert_card()
        self._build_mask_step_card()
        self._build_run_step()
        self._build_results_step()
        self._prefill_from_output()
        self._refresh_axis_step()
        self._refresh_run_step()

    # Pages -------------------------------------------------------------------------------

    def welcome(self) -> widgets.Widget:
        """Confirm that the notebook started, with the package version and what to do next."""
        from . import __version__

        return ui.page(
            "MorphoBricks preprocessing",
            "Per-plane intensity histograms for an experiment folder of TIFF and OME-Zarr images.",
            [
                widgets.HTML(
                    ui.status(
                        "ok",
                        f"The notebook is ready (morphobricks-preprocessing {__version__}).",
                    )
                ),
                widgets.HTML(
                    ui.next_steps(
                        "Go to Step 1 and check your experiment folder.",
                        "Run its cell, choose your image folder, then click Check folder.",
                    )
                ),
            ],
        )

    def folder_step(
        self,
        title: str | None = None,
        purpose: str | None = None,
        *,
        intro: str | None = None,
        show_output: bool = True,
        show_next: bool = True,
        show_design: bool = True,
    ) -> widgets.Widget:
        """Step 1: choose and check the experiment folder, confirm the labels.

        The defaults give this notebook's step. Another brick embedding the step
        (it checks the folder and the image headers, nothing more) can give its
        own words and leave out the output folder of this brick's histograms and
        its experiment design.

        Parameters
        ----------
        title
            Page title; None gives "Step 1 · Check the experiment folder".
        purpose
            One-sentence purpose under the title (HTML); None gives this
            notebook's.
        intro
            Text of the blue "what happens here" box (HTML); None gives this
            notebook's, an empty string leaves the box out.
        show_output
            False hides the "Output folder (optional)" card, which sets where this
            brick writes its histogram table; the check then uses the folder in its
            box (the default when empty). The card is shown again when the check
            finds that folder inside the experiment, so it can be fixed. It also
            leaves out the "Pixel sizes" card and the masks folder with its "Masks"
            card (the check then pairs no masks folder, as without one), and the check
            writes nothing (no report, no design template): the step only checks.
        show_next
            False hides the "What next?" box, which names this notebook's next
            steps.
        show_design
            False leaves out the "Experiment design" card: once the labels are
            confirmed nothing is asked and no ``experiment_design.csv`` (nor its
            output folder) is written, the step's texts no longer name the design
            or this notebook's step 3, and step 3 runs without a design.

        Returns
        -------
        ipywidgets.Widget
            The page.
        """
        ui.set_visible(self.folder_next, show_next)
        self._show_output = show_output
        ui.set_visible(self.output_card, show_output)
        self._refresh_mask_step()  # hidden, and never offered, without the output folder
        self.labels_confirmed.explain(_labels_explanation(show_design))
        if show_design != self._show_design:
            self._show_design = show_design
            # Labels confirmed on an earlier page open or close the design to match.
            self._labels_changed()
        if intro is None:
            intro = (
                "<b>What happens here:</b> the folders are scanned, the labels are taken "
                "from the folder names (condition / biological repeat / technical repeat) "
                "and every image header is checked. Problems are listed with what to do."
            )
        return ui.page(
            "Step 1 · Check the experiment folder" if title is None else title,
            (
                "Choose the folder with your images. It is checked without reading any pixels; "
                "nothing in it is changed."
            )
            if purpose is None
            else purpose,
            [
                *([ui.intro(intro)] if intro else []),
                self.folder_card,
                self.output_card,
                *([self.masks_input_card] if show_output else []),
                self.exclude_box,
                self.check_button,
                self.folder_status,
                self.folder_lock.card,
                self.check_report.widget,
                self.folder_tiles,
                self.labels_card,
                *([self.design_card, self.names_card, self.references_card] if show_design else []),
                *([self.calibration_card] if show_output else []),
                self.consistency_card,
                *([self.masks_card] if show_output else []),
                self.issues_card,
                self.folder_next,
            ],
        )

    def axis_step(
        self,
        title: str | None = None,
        purpose: str | None = None,
        *,
        intro: str | None = None,
        show_next: bool = True,
    ) -> widgets.Widget:
        """Step 2: axis roles, preview and confirmation, then the image type.

        The defaults give this notebook's step; another brick embedding it can give
        its own words, since this notebook's explain the roles by the planes its
        histograms are computed on. The image type question follows the confirmed
        axis roles either way.

        Parameters
        ----------
        title
            Page title; None gives "Step 2 · Review the axis roles".
        purpose
            One-sentence purpose under the title (HTML); None gives this
            notebook's.
        intro
            Text of the blue "why this matters" box (HTML); None gives this
            notebook's, an empty string leaves the box out.
        show_next
            False hides the "What next?" box, which sends the user to this
            notebook's step 3 to compute the histograms.

        Returns
        -------
        ipywidgets.Widget
            The page.
        """
        ui.set_visible(self.axis_next, show_next)
        if intro is None:
            intro = (
                "<b>Why this matters:</b> every image is cut into flat 2D planes, one per "
                "depth slice, channel and time point. The roles below are suggested from "
                "the image metadata, which is not always right, so check them with the "
                "preview before confirming."
            )
        return ui.page(
            "Step 2 · Review the axis roles" if title is None else title,
            "Tell the brick which image dimension is width, height, depth, channel and time."
            if purpose is None
            else purpose,
            [*([ui.intro(intro)] if intro else []), self.axis_box],
        )

    def run_step(self) -> widgets.Widget:
        """Step 3: readiness, settings and the run."""
        return ui.page(
            "Step 3 · Compute the histograms",
            "Read the images one plane at a time and write the histogram table.",
            [
                self.readiness_card,
                self.settings_card,
                self.plan_card,
                self.run_button,
                self.progress,
                self.run_status,
                self.run_lock.card,
                self.run_report.widget,
                self.run_tiles,
                self.run_issues,
                self.run_checks.card,
                self.mask_step_card,
                self.details_box,
                self.run_next,
            ],
        )

    def results_step(self) -> widgets.Widget:
        """Step 4: pooled statistics and intensity distributions from the table."""
        return ui.page(
            "Calculate the statistics (optional)",  # the stepper numbers the steps
            "Compare conditions and repeats using the histograms computed by the run.",
            [
                ui.intro(
                    "<b>This step is optional.</b> The histogram table written by the run is "
                    "all that later MorphoBricks bricks need; this step does not change it "
                    "and writes no files. Use it to compare groups and check that the "
                    "brightness looks as expected.<br>Statistics are <b>pooled</b> from the "
                    "histograms of all planes in each group: a group's median is the median "
                    "of all its pixels, not an average of per-image medians."
                ),
                self.csv_card,
                self.results_card,
                self.stats_tiles,
                self.statistics_card,
                self.distribution_card,
                self.table_checks.card,
                self.stats_next,
            ],
        )

    # Shared ------------------------------------------------------------------------------

    def _busy(self, button: widgets.Button, text: str) -> None:
        button.disabled = True
        button.description = f" {text}"
        button.icon = "spinner"

    def _idle(self, button: widgets.Button, text: str, icon: str) -> None:
        button.description = text
        button.icon = icon
        button.disabled = False
        if button is self.run_button:
            self._refresh_run_step()

    def _excluded(self) -> list[str]:
        return [line.strip() for line in self.exclude.value.splitlines() if line.strip()]

    def _output_folder(self) -> str | None:
        return ui.clean_path(self.output.value) or None

    def _csv_path(self) -> Path | None:
        path = ui.clean_path(self.csv_file.value)
        return Path(path) if path else None

    def _memory_limit(self) -> int:
        return int(self.memory_gib.value * 2**30)

    def _histogram_settings(self) -> HistogramSettings:
        """Return the histogram range chosen in step 3; raise ValueError for unusable quantiles."""
        return self._histogram_from(
            self.histogram_range.value, (self.robust_low.value, self.robust_high.value)
        )

    def _histogram_settings_or_none(self) -> HistogramSettings | None:
        """Return :meth:`_histogram_settings`, or None while the quantiles are unusable."""
        try:
            return self._histogram_settings()
        except ValueError:
            return None

    def _show_log(self) -> None:
        self.details.value = "\n".join(self.log.lines[-500:])


class _LogCapture(logging.Handler):
    """Collects the package's log messages for the "Technical details" panel."""

    def __init__(self):  # noqa: ANN204
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))

    def attached(self):  # noqa: ANN202
        return _Attached(self)


class _ToRootWarnings(logging.Handler):
    """Hands WARNING and above to the root logger's handlers (as propagation would)."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)

    def emit(self, record: logging.LogRecord) -> None:
        # With no root handler (plain Jupyter), propagation would reach logging.lastResort.
        handlers = logging.getLogger().handlers or [logging.lastResort]
        for handler in handlers:
            if handler is not None and record.levelno >= handler.level:
                handler.handle(record)


class _Attached:
    def __init__(self, handler: logging.Handler):  # noqa: ANN204
        self.handler = handler
        self.logger = logging.getLogger(_PACKAGE_LOGGER)

    def __enter__(self):  # noqa: ANN204
        self.level = self.logger.level
        self.propagate = self.logger.propagate
        self.logger.addHandler(self.handler)
        self.logger.setLevel(logging.DEBUG)
        # DEBUG is for the panel only: a root handler (Colab's) would print every record.
        self.logger.propagate = False
        self.forward = _ToRootWarnings()
        self.logger.addHandler(self.forward)
        return self.handler

    def __exit__(self, *exc):  # noqa: ANN002, ANN204
        self.logger.removeHandler(self.handler)
        self.logger.removeHandler(self.forward)
        self.logger.propagate = self.propagate
        self.logger.setLevel(self.level)
        return False
