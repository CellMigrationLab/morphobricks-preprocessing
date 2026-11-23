"""The widgets and the one object behind the Cellpose notebook (``MorphoBricks_Cellpose.ipynb``).

Every code cell of the notebook is one call on :class:`CellposeWorkflow`; the logic is in
:mod:`.segmentation_cellpose` (and the hand-off it builds on), this module only shows it: the
channel dropdowns, the preview figure, the progress line and the final table. A refusal of the
hand-off or of the run is shown **verbatim** and stops the cell (:class:`CellStop`). Needs the
``notebook`` extra (``ipywidgets``, ``matplotlib``); Cellpose is imported only when a model is
loaded.
"""

from __future__ import annotations

import functools
import html
import time
from collections.abc import Callable
from pathlib import Path
from typing import ParamSpec, TypeVar

import ipywidgets as widgets
import numpy as np
from IPython.display import display
from matplotlib.figure import Figure

from . import segmentation_cellpose as cp
from .notebook_progress import progress_html
from .notebook_stop import CellStop
from .notebook_style import STYLE_CSS, status, table, tiles
from .preview_render import as_colour, display_panel, outline
from .run_inputs import RunInputs, RunInputsError
from .segmentation_handoff import (
    SegmentationPlan,
    images_to_segment,
    resolve_segmentation,
    stray_backups,
)
from .segmentation_record import SegmentationError, check_mask_type

NONE = "(none)"
OUTPUT_NAMES = {cp.CELL_OUTPUT: "Cell mask", cp.NUCLEI_OUTPUT: "Nuclei mask"}
"""The outputs offered, as the notebook names them. *Both* comes with #440."""
P = ParamSpec("P")
T = TypeVar("T")
REFUSALS = (SegmentationError, RunInputsError)
"""What the hand-off and the run refuse with; their messages are shown unchanged."""


def stops(method: Callable[P, T]) -> Callable[P, T]:
    """Turn a refusal of the hand-off or of the run into a :class:`CellStop` with its message."""

    @functools.wraps(method)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return method(*args, **kwargs)
        except REFUSALS as exc:
            raise CellStop(str(exc)) from exc

    return wrapper


def _verbatim(text: str) -> str:
    """Return ``text`` as an HTML block that keeps its line breaks and shows it unchanged."""
    return f"<pre style='white-space:pre-wrap;margin:0'>{html.escape(text)}</pre>"


def _card(kind: str, title: str, lines: tuple[str, ...] = (), *, raw: bool = False) -> widgets.HTML:
    """Return a status card as a widget, with the notebook's look."""
    return widgets.HTML(f"<div class='mb-root'>{status(kind, title, lines, raw=raw)}</div>")


class ChannelPicker:
    """A cell channel and a nuclear channel, both optional, by channel ID, for one output.

    Owner, 23 November 2026: a **cell mask** reads the cell channel, then the nuclear channel
    if one is chosen; a **nuclei mask** reads the nuclear channel only
    (:func:`.segmentation_cellpose.input_channels`, which refuses a choice that does not fit).
    The roles only name and order the channels; they are resolved by ID as before. Nothing is
    chosen for the user: both start blank.

    The options are the channel IDs of the run; one that not every image has says so, and
    choosing it shows the resolver's refusal (``A_SCOPE_INCOMPLETE``) unchanged under the
    dropdowns. :attr:`plan` is the resolved selection, or None while the choice is refused.
    """

    def __init__(self, inputs: RunInputs, output: str = cp.CELL_OUTPUT) -> None:
        """Build the dropdowns for ``inputs`` and the ``output`` and resolve the choice."""
        self.inputs, self.output = inputs, output
        coverage = cp.channel_coverage(inputs)
        options = [
            (NONE, ""),
            *(
                (c if have == total else f"{c} (in {have} of {total} images)", c)
                for c, (have, total) in coverage.items()
            ),
        ]
        style = {"description_width": "11em"}
        self.cell = widgets.Dropdown(
            options=options, value="", description="Cell channel", style=style
        )
        self.nuclear = widgets.Dropdown(
            options=options, value="", description="Nuclear channel", style=style
        )
        self.dropdowns = [self.cell, self.nuclear]
        self.message = widgets.HTML()
        self.plan: SegmentationPlan | None = None
        self.refusal = ""
        self.channel_ids: tuple[str, ...] = ()
        self.channel_roles: tuple[str, ...] = ()
        for dropdown in self.dropdowns:
            dropdown.observe(self._changed, names="value")
        self._changed()

    def _changed(self, _change: object = None) -> None:
        """Resolve the choice; show what was resolved, or the refusal verbatim."""
        try:
            ids, roles = cp.input_channels(self.output, self.cell.value, self.nuclear.value)
            self.channel_ids, self.channel_roles = ids, roles
            cp.check_channel_count(ids)
            self.plan = resolve_segmentation(self.inputs, ids)
            self.refusal = ""
            named = ", ".join(f"{c} ({r})" for c, r in zip(ids, roles, strict=True))
            note = _card("ok", f"{OUTPUT_NAMES[self.output]} from {named}")
        except REFUSALS as exc:
            self.plan, self.refusal = None, str(exc)
            self.channel_ids = self.channel_roles = ()
            note = _card(
                "error", "These channels cannot be used", (_verbatim(self.refusal),), raw=True
            )
        self.message.value = note.value

    def current_plan(self) -> SegmentationPlan:
        """Resolve the dropdowns as they are now and return the plan, or refuse (once).

        The dropdowns may have changed since the model was loaded; every step that segments
        calls this first, so a refused choice stops the cell with the resolver's message
        before anything starts.

        Raises
        ------
        SegmentationError
            With the resolver's message, when the choice is refused.
        """
        self._changed()
        if self.plan is None:
            raise SegmentationError(self.refusal)
        return self.plan

    def box(self) -> widgets.VBox:
        """Return the dropdowns and the message under them."""
        help_line = widgets.HTML(
            "<div class='mb-help'>Choose by channel ID. The <b>cell channel</b> (cytoplasm or "
            "cell border) and the <b>nuclear channel</b> are both optional, but at least one is "
            "needed. A cell mask reads the cell channel, then the nuclear channel if one is "
            "chosen; a nuclei mask reads the nuclear channel only.</div>"
        )
        return widgets.VBox([*self.dropdowns, help_line, self.message])


def preview_figure(stack: np.ndarray, labels: np.ndarray, channel_ids: tuple[str, ...]) -> Figure:
    """Return the preview: each channel (display contrast only) and the outlines of the labels.

    The contrast stretch (preprocessing's own display contrast, which also shows non-finite
    pixels as such) is for the eye: the stack that was segmented, and the masks, are the stored
    values and are never changed by it.
    """
    panels = [display_panel(np.asarray(channel)).pixels for channel in stack]
    edge = outline(np.asarray(labels))
    figure = Figure(figsize=(4 * (len(panels) + 1), 4))
    axes = figure.subplots(1, len(panels) + 1, squeeze=False)[0]
    for axis, panel, name in zip(axes, panels, channel_ids, strict=False):
        axis.imshow(panel, cmap="gray", vmin=0, vmax=255)
        axis.set_title(f"{name} (display contrast)")
    overlay = as_colour(panels[0]).copy()
    overlay[edge] = (255, 217, 0)
    axes[-1].imshow(overlay)
    axes[-1].set_title(f"Cellpose: {int(labels.max())} objects")
    for axis in axes:
        axis.axis("off")
    figure.tight_layout()
    return figure


class CellposeWorkflow:
    """The Cellpose notebook's state: the run, the channels, the model and the batch.

    Each notebook cell calls one method; a method that needs an earlier cell says which one.
    ``shown`` keeps the HTML of every card shown (for tests and for reading the notebook back).
    """

    def __init__(self) -> None:
        """Start with nothing chosen."""
        self.inputs: RunInputs | None = None
        self.masks_root: Path | None = None
        self.mask_type = ""
        self.picker: ChannelPicker | None = None
        self.loaded: cp.LoadedModel | None = None
        self.settings: cp.Settings | None = None
        self.result: cp.BatchResult | None = None
        self.shown: list[str] = []

    def _show(self, widget: widgets.Widget) -> None:
        """Display ``widget`` (the style sheet goes first, once)."""
        if not self.shown:
            display(widgets.HTML(STYLE_CSS))
        self.shown.append(getattr(widget, "value", ""))
        display(widget)

    def _need(self, what: str, cell: int) -> None:
        """Refuse a step whose earlier cell has not run."""
        missing = {
            "run": self.inputs is None,
            "channels": self.picker is None,
            "model": self.loaded is None,
        }[what]
        if missing:
            raise SegmentationError(f"Run cell {cell} first.")

    @stops
    def open(self, run_folder: str, masks_folder: str) -> None:
        """Open the run and say what it holds (cell 3)."""
        if not masks_folder.strip():
            raise SegmentationError("Give the masks folder (outside the experiment and the run).")
        inputs = cp.open_run(run_folder)
        self.inputs, self.masks_root = inputs, Path(masks_folder.strip())
        self.picker = self.loaded = self.result = None
        coverage = cp.channel_coverage(inputs)
        lines = [
            f"{c}: in {have} of {total} images" + ("" if have == total else " (partial)")
            for c, (have, total) in coverage.items()
        ]
        deep = cp.non_2d_images(inputs)
        if deep:
            lines.append(
                f"{len(deep)} image(s) have Z or T above 1 and will fail: segmentation here is "
                f"2D only ({deep[0]}...)."
            )
        self._show(
            _card(
                "warn" if deep else "ok", f"Run opened: {len(inputs.fovs())} images", tuple(lines)
            )
        )

    @stops
    def choose_channels(self, mask_type: str, output: str = cp.CELL_OUTPUT) -> None:
        """Check the mask type and the output, and show the channel dropdowns (cell 4)."""
        self._need("run", 3)
        if output not in OUTPUT_NAMES:
            raise SegmentationError(
                f"The output must be one of {', '.join(OUTPUT_NAMES)}, not {output!r}."
            )
        check_mask_type(mask_type)
        todo = images_to_segment(self.inputs, mask_type, self.masks_root)
        self.mask_type, self.loaded = mask_type, None
        self.picker = ChannelPicker(self.inputs, output)
        self._show(
            _card(
                "info",
                f"{OUTPUT_NAMES[output]}, mask type {mask_type}: {len(todo)} of "
                f"{len(self.inputs.fovs())} images have no mask yet",
            )
        )
        self._show(self.picker.box())

    @stops
    def set_settings(
        self,
        diameter: str,
        flow_threshold: float,
        cellprob_threshold: float,
        batch_size: int,
    ) -> None:
        """Check the inference settings (cell 5); a blank diameter is the model's native scale.

        Normalisation is not a choice: it is always Cellpose's own, per image (1st to 99th
        percentile of each channel).
        """
        self.settings = cp.Settings(
            cp.diameter_from_text(diameter),
            flow_threshold,
            cellprob_threshold,
            batch_size=batch_size,
        )

    @stops
    def load(
        self,
        model: str,
        custom_model_file: str,
        device: str,
        download_weights: bool,
        install_dino: bool,
    ) -> None:
        """Say what loading does, then load the verified model (cell 5)."""
        self._need("channels", 4)
        self.picker.current_plan()
        if self.settings is None:
            raise SegmentationError("Set the settings first: run set_settings in cell 5.")
        chosen = custom_model_file.strip() or model
        if install_dino and chosen in cp.DINO_MODELS:
            self._show(_card("info", "Installing DINOv3", (" ".join(cp.dinov3_install_command()),)))
            cp.install_dinov3()
        self._show(
            _card(
                "info",
                f"Loading {chosen} on {device}",
                (
                    "Built-in weights are downloaded from Hugging Face on first use; they are "
                    "downloaded only if the download box is ticked.",
                    "On a GPU Cellpose may move its flow-threshold quality step to the CPU "
                    "when GPU memory is short, and always computes the masks on the CPU on "
                    "mps. That is Cellpose's own behaviour; it is recorded as a note.",
                ),
            )
        )
        self.loaded = cp.load_model(chosen, device, allow_download=download_weights)
        loaded, diameter = self.loaded, self.settings.diameter
        self._show(
            _card(
                "ok",
                f"Model {loaded.name} loaded on {loaded.device}",
                (
                    f"Weights file: {loaded.file}",
                    f"SHA-256: {loaded.sha256}",
                    f"Network {loaded.backbone}, tile size {loaded.tile_size}",
                    (
                        f"Weights downloaded now from {loaded.source}"
                        if loaded.downloaded
                        else "Weights were already on this computer (nothing downloaded)"
                    ),
                    "Diameter: "
                    + (
                        f"{diameter:g} pixels"
                        if diameter is not None
                        else "blank = " + cp.NATIVE_SCALE
                    ),
                ),
            )
        )

    @stops
    def preview(self, image: str = "") -> None:
        """Segment one image and show it with the outlines; nothing is written (cell 6)."""
        self._need("model", 5)
        plan = self.picker.current_plan()
        fovs = self.inputs.fovs()
        todo = images_to_segment(self.inputs, self.mask_type, self.masks_root)
        by_path = {f.relative_path: f for f in fovs}
        if image.strip() and image.strip() not in by_path:
            raise SegmentationError(
                f"{image.strip()!r} is not an image of this run. Leave it blank, or use a path "
                f"such as {fovs[0].relative_path}."
            )
        fov = by_path[image.strip()] if image.strip() else (todo or fovs)[0]
        stack = plan.read(fov)
        labels = cp.segment_image(self.loaded, self.settings, stack)
        self._show(_card("info", f"Preview of {fov.relative_path} (nothing is written)"))
        display(preview_figure(stack, labels, plan.channel_ids))

    @stops
    def run(self, overwrite: bool = False) -> cp.BatchResult:
        """Segment the images, write the masks and the record, and show the table (cell 7)."""
        self._need("model", 5)
        job = cp.SegmentationJob(
            self.inputs,
            self.picker.current_plan(),
            self.loaded,
            self.settings,
            self.mask_type,
            self.masks_root,
            overwrite,
            self.picker.channel_roles,
            self.picker.output,
        )
        bar = widgets.HTML()
        display(bar)
        started = time.monotonic()

        def progress(done: int, total: int, item: str) -> None:
            bar.value = progress_html("Segmenting", done, total, item, time.monotonic() - started)

        self.result = result = cp.run_batch(job, progress=progress)
        counts = result.counts()
        rows = [["Image", "Status", "Message"], *map(list, result.rows())]
        failed = counts["failed"]
        if result.stopped:
            self._show(_card("error", "The batch stopped early", (result.stopped,)))
        if strays := stray_backups(self.masks_root, self.mask_type):  # reported, never deleted
            names = ", ".join(p.name for p in strays)
            self._show(_card("warn", f"Copies of earlier masks left by a stopped run: {names}"))
        self._show(
            widgets.HTML(
                "<div class='mb-root'>"
                + tiles(
                    [
                        ("✅", str(counts["written"]), "written"),
                        ("⏩", str(counts["skipped"]), "skipped"),
                        ("⚠️", str(counts[cp.NEEDS_ATTENTION]), "need attention"),
                        ("❌", str(failed), "failed"),
                    ]
                )
                + table(rows)
                + "</div>"
            )
        )
        return result

    @stops
    def next_steps(self) -> None:
        """Say what to do now that the masks are made (the closing cell)."""
        self._need("channels", 4)
        lines = cp.next_steps_lines(self.mask_type, self.masks_root)
        self._show(_card("info", "Next steps", lines))
