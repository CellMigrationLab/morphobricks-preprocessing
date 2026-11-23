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
from IPython.display import display

from . import segmentation_cellpose as cp
from .notebook_cellpose_channels import (
    NONE,
    OUTPUT_NAMES,
    REFUSALS,
    ChannelPicker,
    MaskTarget,
    _card,
)
from .notebook_cellpose_preview import MaskResult, preview_figures
from .notebook_progress import progress_html
from .notebook_stop import CellStop
from .notebook_style import STYLE_CSS, table, tiles
from .run_inputs import Fov, RunInputs
from .segmentation_cellpose_run import channel_layouts, numbered_layout
from .segmentation_handoff import images_to_segment, stray_backups
from .segmentation_record import SegmentationError, check_mask_type

__all__ = [
    "NONE",
    "OUTPUT_NAMES",
    "REFUSALS",
    "CellposeWorkflow",
    "CellStop",
    "ChannelPicker",
    "MaskTarget",
    "stops",
]

P = ParamSpec("P")
T = TypeVar("T")


def stops(method: Callable[P, T]) -> Callable[P, T]:
    """Turn a refusal of the hand-off or of the run into a :class:`CellStop` with its message."""

    @functools.wraps(method)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return method(*args, **kwargs)
        except REFUSALS as exc:
            raise CellStop(str(exc)) from exc

    return wrapper


class CellposeWorkflow:
    """The Cellpose notebook's state: the run, the channels, the model and the batch.

    Each notebook cell calls one method; a method that needs an earlier cell says which one.
    ``shown`` keeps the HTML of every card shown (for tests and for reading the notebook back).

    The output Both (#440) makes two mask types, :attr:`mask_types` by output (``cells``,
    ``nuclei``). Both use :attr:`loaded` and :attr:`settings` unless a nuclei model was loaded
    (:meth:`load_nuclei`): then the nuclei use :attr:`nuclei_loaded` and
    :attr:`nuclei_settings`. Each mask type is its own batch, record, skip and overwrite.
    """

    def __init__(self) -> None:
        """Start with nothing chosen."""
        self.inputs: RunInputs | None = None
        self.masks_root: Path | None = None
        self.mask_type = ""
        self.mask_types: dict[str, str] = {}
        self.picker: ChannelPicker | None = None
        self.loaded: cp.LoadedModel | None = None
        self.settings: cp.Settings | None = None
        self.nuclei_loaded: cp.LoadedModel | None = None
        self.nuclei_settings: cp.Settings | None = None
        self.result: cp.BatchResult | None = None
        self.results: dict[str, cp.BatchResult] = {}
        self.previewed: list[MaskResult] = []
        self.shown: list[str] = []
        self.on_masks_written: Callable[[cp.BatchResult], object] | None = None

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

    def drop_choice(self) -> None:
        """Forget the channels, the mask types and every loaded model (the choice changed)."""
        self.picker = self.loaded = self.nuclei_loaded = self.nuclei_settings = None
        self.mask_types = {}

    def model_for(self, output: str) -> tuple[cp.LoadedModel, cp.Settings]:
        """Return the model and the settings that segment the masks of ``output``.

        Nuclei settings of their own without their model loaded are refused: the nuclei are
        never segmented with the cells' model instead.
        """
        if output == cp.NUCLEI_OUTPUT and self.nuclei_settings is not None:
            if self.nuclei_loaded is None:
                raise SegmentationError(
                    "The nuclei have their own model: load the nuclei model first (cell 5)."
                )
            return self.nuclei_loaded, self.nuclei_settings
        return self.loaded, self.settings

    @stops
    def open(self, run_folder: str, masks_folder: str) -> None:
        """Open the run and say what it holds (cell 3)."""
        if not masks_folder.strip():
            raise SegmentationError("Give the masks folder (outside the experiment and the run).")
        inputs = cp.open_run(run_folder)
        self.inputs, self.masks_root = inputs, Path(masks_folder.strip())
        self.drop_choice()
        self.result, self.results = None, {}
        coverage = cp.channel_coverage(inputs)
        lines = [
            f"{c}: in {have} of {total} images" + ("" if have == total else " (partial)")
            for c, (have, total) in coverage.items()
        ]
        layouts = channel_layouts(inputs)
        lines.append(
            f"Channel numbers: {numbered_layout(next(iter(layouts)))}, the same in every image."
            if len(layouts) == 1 and None not in layouts
            else "Channel numbers differ between images: choose the channels by tag."
        )
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
    def choose_channels(
        self, mask_type: str, output: str = cp.CELL_OUTPUT, nuclei_mask_type: str = ""
    ) -> None:
        """Check the mask type(s) and the output, and show the channel dropdowns (cell 4).

        For Both, ``mask_type`` names the cell masks and ``nuclei_mask_type`` the nuclei masks;
        the two must differ.
        """
        self._need("run", 3)
        if output not in OUTPUT_NAMES:
            raise SegmentationError(
                f"The output must be one of {', '.join(OUTPUT_NAMES)}, not {output!r}."
            )
        types = (
            {cp.CELL_OUTPUT: mask_type, cp.NUCLEI_OUTPUT: nuclei_mask_type}
            if output == cp.BOTH_OUTPUT
            else {output: mask_type}
        )
        for name in types.values():
            check_mask_type(name)
        if len({name.casefold() for name in types.values()}) < len(types):
            raise SegmentationError(
                f"The cell and the nuclei mask types must differ (both are {mask_type!r}): each "
                "is its own folder of masks. Rename one."
            )
        todo = {o: images_to_segment(self.inputs, t, self.masks_root) for o, t in types.items()}
        self.drop_choice()
        self.mask_types, self.mask_type = types, next(iter(types.values()))
        self.picker = ChannelPicker(self.inputs, output)
        lines = tuple(
            f"{OUTPUT_NAMES[o]}, mask type {t}: {len(todo[o])} of {len(self.inputs.fovs())} "
            "images have no mask yet"
            for o, t in types.items()
        )
        self._show(_card("info", lines[0], lines[1:]))
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
        self.settings = _settings(diameter, flow_threshold, cellprob_threshold, batch_size)

    @stops
    def set_nuclei_settings(
        self,
        diameter: str,
        flow_threshold: float,
        cellprob_threshold: float,
        batch_size: int,
    ) -> None:
        """Check the settings of a separate nuclei model (output Both, cell 5's Advanced)."""
        self._need_both()
        self.nuclei_settings = _settings(diameter, flow_threshold, cellprob_threshold, batch_size)

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
        self.picker.current_targets()
        if self.settings is None:
            raise SegmentationError("Set the settings first: run set_settings in cell 5.")
        self.loaded = None
        self.loaded = self._load_model(
            model, custom_model_file, device, download_weights, install_dino, self.settings
        )

    @stops
    def load_nuclei(
        self,
        model: str,
        custom_model_file: str,
        device: str,
        download_weights: bool,
        install_dino: bool,
    ) -> None:
        """Load a separate model for the nuclei of the output Both (cell 5's Advanced)."""
        self._need_both()
        if self.nuclei_settings is None:
            raise SegmentationError("Set the nuclei settings first (cell 5, Advanced).")
        self.nuclei_loaded = None
        self.nuclei_loaded = self._load_model(
            model, custom_model_file, device, download_weights, install_dino, self.nuclei_settings
        )

    def _need_both(self) -> None:
        """Refuse a separate nuclei model unless the output chosen in cell 4 is Both."""
        self._need("channels", 4)
        if self.picker.output != cp.BOTH_OUTPUT:
            raise SegmentationError(
                "A separate nuclei model is for the output Both only; choose Both in cell 4, "
                "or untick the separate nuclei model in cell 5 (Advanced)."
            )

    def same_model_for_nuclei(self) -> None:
        """Segment the nuclei of Both with the model and the settings of the cells."""
        self.nuclei_loaded = self.nuclei_settings = None

    def _load_model(
        self,
        model: str,
        custom_model_file: str,
        device: str,
        download_weights: bool,
        install_dino: bool,
        settings: cp.Settings,
    ) -> cp.LoadedModel:
        """Load the model chosen, verified, and show what was loaded."""
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
        loaded = cp.load_model(chosen, device, allow_download=download_weights)
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
                    "Diameter: " + _diameter(settings),
                ),
            )
        )
        return loaded

    @stops
    def preview(self, image: str = "") -> None:
        """Segment one image and show it with the outlines; nothing is written (cell 6).

        For Both, the image is segmented twice, the cells and the nuclei, each shown.
        """
        self._need("model", 5)
        targets = self.picker.current_targets()
        fovs = self.inputs.fovs()
        by_path = {f.relative_path: f for f in fovs}
        if image.strip() and image.strip() not in by_path:
            raise SegmentationError(
                f"{image.strip()!r} is not an image of this run. Leave it blank, or use a path "
                f"such as {fovs[0].relative_path}."
            )
        fov = by_path[image.strip()] if image.strip() else (self.first_without_mask() or fovs[0])
        results, lines = [], []
        for target in targets:
            loaded, settings = self.model_for(target.output)
            stack = target.plan.read(fov)
            labels, flows = cp.evaluate(loaded, settings, stack)
            named = tuple(zip(target.channel_ids, target.roles, strict=True))
            result = MaskResult(OUTPUT_NAMES[target.output], stack, named, labels, flows)
            results.append(result)
            lines.append(f"{result.name}: {result.objects} objects; diameter {_diameter(settings)}")
        self._show(
            _card("info", f"Preview of {fov.relative_path} (nothing is written)", tuple(lines))
        )
        try:
            figures = preview_figures(results)
        except ValueError as exc:
            raise SegmentationError(str(exc)) from exc
        for figure in figures:
            display(figure)
        self.previewed = results

    def first_without_mask(self) -> Fov | None:
        """Return the run's first image that lacks a mask of a mask type to make, or None."""
        missing = {
            f.relative_path
            for t in self.mask_types.values()
            for f in images_to_segment(self.inputs, t, self.masks_root)
        }
        return next((f for f in self.inputs.fovs() if f.relative_path in missing), None)

    @stops
    def run(self, overwrite: bool = False) -> dict[str, cp.BatchResult]:
        """Segment the images, write the masks and the record, and show the table (cell 7).

        Each mask type is its own batch, one after the other (cells first): its own record,
        skip and overwrite, and the per-mask rollback of :func:`.segmentation_cellpose.run_batch`.
        A batch that stops with an error, or an :attr:`on_masks_written` hook that raises, does
        not stop the next; the first error is raised once every batch has run.

        Returns
        -------
        dict
            The :class:`.segmentation_cellpose.BatchResult` of each mask type that ran, by mask
            type; :attr:`result` is the first (the only one unless the output is Both).
        """
        self._need("model", 5)
        jobs = [self._job(target, overwrite) for target in self.picker.current_targets()]
        self.results, error = {}, None
        for job in jobs:
            try:
                self.results[job.mask_type] = result = self._run_job(job, titled=len(jobs) > 1)
                if self.on_masks_written is not None:
                    self.on_masks_written(result)
            except Exception as exc:  # the other mask type still runs; raised after it
                if len(jobs) > 1:
                    self._show(_card("error", f"Mask type {job.mask_type} stopped", (_named(exc),)))
                error = error or exc
        self.result = next(iter(self.results.values()), None)
        if error is not None:
            raise error
        return dict(self.results)

    def _job(self, target: MaskTarget, overwrite: bool) -> cp.SegmentationJob:
        """Return the batch of one mask type."""
        loaded, settings = self.model_for(target.output)
        return cp.SegmentationJob(
            self.inputs,
            target.plan,
            loaded,
            settings,
            self.mask_types[target.output],
            self.masks_root,
            overwrite,
            target.roles,
            target.output,
            target.numbers,
        )

    def _run_job(self, job: cp.SegmentationJob, *, titled: bool) -> cp.BatchResult:
        """Run one batch with a progress line, then show its summary and table."""
        bar = widgets.HTML()
        display(bar)
        started = time.monotonic()

        def progress(done: int, total: int, item: str) -> None:
            bar.value = progress_html(
                f"Segmenting {job.mask_type}" if titled else "Segmenting",
                done,
                total,
                item,
                time.monotonic() - started,
            )

        result = cp.run_batch(job, progress=progress)
        counts = result.counts()
        rows = [["Image", "Status", "Message"], *map(list, result.rows())]
        if result.stopped:
            self._show(_card("error", "The batch stopped early", (result.stopped,)))
        if strays := stray_backups(self.masks_root, job.mask_type):  # reported, never deleted
            names = ", ".join(p.name for p in strays)
            self._show(_card("warn", f"Copies of earlier masks left by a stopped run: {names}"))
        heading = f"<h4>Mask type {html.escape(job.mask_type)}</h4>" if titled else ""
        self._show(
            widgets.HTML(
                "<div class='mb-root'>"
                + heading
                + tiles(
                    [
                        ("✅", str(counts["written"]), "written"),
                        ("⏩", str(counts["skipped"]), "skipped"),
                        ("⚠️", str(counts[cp.NEEDS_ATTENTION]), "need attention"),
                        ("❌", str(counts["failed"]), "failed"),
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
        lines = cp.next_steps_lines(tuple(self.mask_types.values()), self.masks_root)
        self._show(_card("info", "Next steps", lines))


def _settings(
    diameter: str, flow_threshold: float, cellprob_threshold: float, batch_size: int
) -> cp.Settings:
    """Return the checked settings of the form's text and numbers."""
    return cp.Settings(
        cp.diameter_from_text(diameter),
        flow_threshold,
        cellprob_threshold,
        batch_size=batch_size,
    )


def _diameter(settings: cp.Settings) -> str:
    """Return the diameter as the cards say it: pixels, or the native scale."""
    if settings.diameter is not None:
        return f"{settings.diameter:g} pixels"
    return "blank = " + cp.NATIVE_SCALE


def _named(exc: BaseException) -> str:
    """Return an error as ``Type: message``."""
    return f"{type(exc).__name__}: {exc}"
