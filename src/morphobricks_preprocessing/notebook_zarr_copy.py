"""*Also save a copy as OME-Zarr*: asked on the first screen, written once the axes are confirmed.

Owner (11 November 2026): an option on the first screen, **Also save a copy as OME-Zarr (in
the output folder)**. Ticking it writes nothing. The copy can only be written faithfully once
the roles of the dimensions are known, so the Axes step offers it after the axis roles are
confirmed (and while no pixel-size statement has an error): one click writes every image that
is read as an OME-Zarr image into ``<output folder>/ome_zarr`` with
:func:`.conversion.convert_experiment` (``store="ome-zarr"``: the plan, the lock, the
conversion record and every refusal of the named-axis copies), then opens the copies as the
experiment, scanned and checked again; nothing is confirmed for the user. The originals are
only read.

With Z-stacks the projection card writes the copies (:mod:`.notebook_projection`); ticked, it
writes them as OME-Zarr, into ``<output folder>/projected_<method>..._zarr``.
"""

from __future__ import annotations

import html
import traceback
from importlib.util import find_spec
from pathlib import Path

import ipywidgets as widgets

from . import notebook_style as ui
from .conversion import ConversionResult, convert_experiment
from .notebook_calibration import CALIBRATION_ISSUE_CODES
from .notebook_progress import Wait
from .notebook_render import blocked_line, issues_brief_html
from .ome_zarr_copy import OME_TIFF, OME_ZARR
from .size_checks import Z_STACK_CODE

OPTION_LABEL = "Also save a copy as OME-Zarr (in the output folder)"
OPTION_HELP = (
    "Nothing is written now. Once the axis roles are confirmed (Axes, step 2), one click there "
    "writes the copy with its conversion record, and the later steps use it. Your images are "
    "only read."
)
NO_ZARR = 'the zarr package is not installed: pip install "morphobricks-preprocessing[zarr]"'
WRITE_LABEL = " Write the OME-Zarr copy and continue with it"
FOLDER_NAME = "ome_zarr"


class _ZarrCopyCard:
    """The first screen's OME-Zarr option and the Axes step's card that writes the copy."""

    def _build_zarr_copy(self) -> None:
        """Build the option (unticked) and the card (hidden until the option is ticked)."""
        readable = find_spec("zarr") is not None
        self.zarr_option = widgets.Checkbox(
            value=False,
            description=OPTION_LABEL,
            indent=False,
            disabled=not readable,
            layout=widgets.Layout(width="auto"),
        )
        self.zarr_option_row = widgets.VBox(
            [
                self.zarr_option,
                widgets.HTML(blocked_line([] if readable else [NO_ZARR])),
                ui.help_text(OPTION_HELP),
            ]
        )
        self.zarr_where = widgets.HTML()
        self.zarr_button = ui.button(WRITE_LABEL, style="success", icon="clone", main=True)
        self.zarr_blocker = widgets.HTML()
        self.zarr_status = widgets.HTML()
        self.zarr_card = ui.card(
            "OME-Zarr copy",
            [self.zarr_where, self.zarr_button, self.zarr_blocker, self.zarr_status],
            visible=False,
        )
        self.zarr_option.observe(lambda _: self._refresh_zarr_card(), names="value")
        self.zarr_option.observe(lambda _: self._projection_changed(), names="value")
        self.zarr_button.on_click(lambda _: self.write_zarr_copy())

    def _copy_store(self) -> str:
        """Return the store the copies are written in: OME-Zarr when the option is ticked."""
        option = getattr(self, "zarr_option", None)
        return OME_ZARR if option is not None and option.value else OME_TIFF

    def _zarr_reasons(self) -> list[str]:
        """Return what stops the OME-Zarr copy now, the first thing to do first."""
        if self.confirmed_mapping is None:
            return ["the axis roles are not confirmed: confirm them above first"]
        shown = self._shown_calibration()[0]
        wrong = [
            i for i in shown.metadata.issues if i.is_error and i.code in CALIBRATION_ISSUE_CODES
        ]
        if wrong:
            return ["a pixel size statement has an error: see Pixel size above"]
        return []

    def _refresh_zarr_card(self) -> None:
        """Offer the card for a checked folder of TIFF images while the option is ticked."""
        if getattr(self, "zarr_card", None) is None:
            return
        pre = self.preflight
        files = [] if pre is None else [f for f in pre.metadata.files if f.readable]
        stacks = any(
            c == Z_STACK_CODE.replace("_", " ") for c in getattr(self, "_review_errors", ())
        )
        offered = self.zarr_option.value and bool(files) and not stacks
        ui.set_visible(self.zarr_card, offered)
        if not offered:
            return
        if all(f.is_ome_zarr for f in files):
            self.zarr_where.value = ui.status(
                "ok", "These images are OME-Zarr already: there is nothing to write."
            )
            for widget in (self.zarr_button, self.zarr_blocker):
                ui.set_visible(widget, False)
            return
        for widget in (self.zarr_button, self.zarr_blocker):
            ui.set_visible(widget, True)
        dest = self._output_path(pre.discovery.root) / FOLDER_NAME
        self.zarr_where.value = (
            f"<div>You asked on Images for a copy as OME-Zarr. Every image is written, plane "
            f"for plane with the confirmed roles, to <b>{html.escape(str(dest))}</b>, with a "
            "conversion record; the later steps then use the copy. Your images are only "
            "read.</div>"
        )
        reasons = self._zarr_reasons()
        self.zarr_button.disabled = bool(reasons)
        self.zarr_blocker.value = blocked_line(reasons)

    def write_zarr_copy(self) -> ConversionResult | None:
        """Write the OME-Zarr copies with the confirmed roles, then open them as the experiment.

        Returns
        -------
        ConversionResult or None
            None when the click is not available or an unexpected problem stopped it.
        """
        if self.preflight is None or not self.zarr_option.value or self._zarr_reasons():
            return None
        root = Path(self.preflight.discovery.root)
        output = self._output_path(root)
        self._busy(self.zarr_button, "Writing the OME-Zarr copy…")
        wait = Wait(self.zarr_status)
        wait.start("Checking the images and the folder")

        def progressed(done: int, total: int, image: str) -> None:
            if image:
                wait.step(done, total, image)

        try:
            with self.log.attached():
                result = convert_experiment(
                    root,
                    self.confirmed_mapping,
                    output / FOLDER_NAME,
                    exclude=self.preflight.excluded,
                    missing_as_singleton=self.missing_as_singleton.value,
                    memory_limit=self._memory_limit(),
                    progress=progressed,
                    masks_folder=self._runs_masks_folder(),
                    store=OME_ZARR,
                )
        except Exception as exc:  # unexpected failures still get a readable message
            self.log.lines.append(traceback.format_exc())
            self.zarr_status.value = ui.status(
                "error",
                "The copy could not be written because of an unexpected problem.",
                [f"{type(exc).__name__}: {exc}", "Open Technical details for the full report."],
            )
            return None
        finally:
            self._idle(self.zarr_button, WRITE_LABEL, "clone")
            self._show_log()
        if not result.written:
            self.zarr_status.value = ui.status(
                "error", "No copy was written.", ["Nothing was written: see below."]
            ) + issues_brief_html(result.issues)
            return result
        self.zarr_status.value = ""
        self._use_copies(result.destination, output)
        return result


__all__ = ["FOLDER_NAME", "NO_ZARR", "OPTION_HELP", "OPTION_LABEL", "_ZarrCopyCard"]
