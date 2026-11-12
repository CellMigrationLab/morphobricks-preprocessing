"""Z-stacks in the shipped notebook: save the dataset as recorded projections, and go on.

MorphoBricks v1 analyses 2-D planes, so an image with more than one depth slice is refused
(``z_stack_not_supported``). Owner (11 November 2026): when the roles on Axes give a depth
dimension with several slices, the app offers the path the refusal names, right there: the
user looks at the stack in the viewer (its z slider), **chooses the projection** (a method,
with no default, and the slices), and one click writes a projected 2-D copy of every image
with :func:`.conversion.convert_experiment` (issue #176: the copies, their conversion record
version 4, every refusal before anything is written). The later steps then use the copies:
the app opens the copy tree as the experiment (scanned and checked again, nothing confirmed for
the user), with the same output folder.

**The original stacks are kept.** They are only read: never changed, moved or deleted. The
conversion record in the copy tree names the folder they are in (``source_root``) and, per
image, the same relative path, the stack's fingerprint and content SHA-256, its depth axis
and size, and the slices used; a run on the copies records that record's SHA-256, and
``RunInputs.projection`` hands these to the bricks. A tool that analyses volumes can therefore
be given the original stack of any analysed image later, without any migration. Until a
consumer declares it (``Capabilities.volume_support``; today a volume request is refused with
``A_VOLUME_NOT_SUPPORTED``), the option *Keep the 3-D stacks for 3-D-capable tools* is shown
greyed out with its reason.

The copies are written into ``<output folder>/projected_<method>[_z<start>-<stop>]``: an
output folder may hold the experiment it describes (contract §15). Masks are never projected;
2-D masks pair with the copies by the same relative paths.
"""

from __future__ import annotations

import html
import traceback
from pathlib import Path

import ipywidgets as widgets

from . import notebook_style as ui
from .conversion import ConversionResult, convert_experiment
from .conversion_record import (
    RECORD_FILENAME,
    ConversionRecord,
    ConversionRecordError,
    read_record,
)
from .image_view import ViewRequest, read_projection_view
from .notebook_progress import Wait
from .notebook_render import blocked_line, issues_brief_html
from .notebook_viewer import READ_ERRORS
from .ome_zarr_copy import OME_TIFF, OME_ZARR, ZARR_COPY_SUFFIX
from .projection import METHODS, Projection
from .size_checks import Z_STACK_CODE
from .validation import plural

CHOOSE = "— choose a method —"
METHOD_OPTIONS = [
    (CHOOSE, ""),
    ("Maximum: the brightest value along Z (keeps the pixel type)", "max"),
    ("Mean: the average along Z (written as 64-bit floats)", "mean"),
    ("Sum: the total along Z (written in a wider type)", "sum"),
]
WRITE_LABEL = " Write the projected copies and continue with them"
KEEP_3D = "Keep the 3-D stacks for 3-D-capable tools (fiberfeatures)"
NOT_ENABLED = "3-D analysis is not enabled yet"
FOLDER_PREFIX = "projected_"
INTRO = (
    "MorphoBricks analyses 2-D planes, and these images are Z-stacks. Look at the stack in "
    "the example above (the <b>z</b> slider), then choose how each stack becomes one plane. "
    "One click writes a projected copy of every image, with a conversion record, into the "
    "output folder; the later steps use the copies. Nothing is chosen for you."
)
TICKED = "Example checked. Now click Confirm axis roles below."
TICKED_STACKS = "Example checked. Now choose the projection under Z-stacks below."
KEPT = (
    "The original Z-stacks are only read: never changed, moved or deleted. The conversion "
    "record lists each one (folder, path, fingerprint, SHA-256, slices used), so a tool that "
    "analyses volumes can be given them later."
)


def copies_folder(output: Path, projection: Projection, store: str = OME_TIFF) -> Path:
    """Return the folder of the projected copies inside the output folder."""
    name = FOLDER_PREFIX + projection.method
    if projection.z_range is not None:
        name += f"_z{projection.z_range[0]}-{projection.z_range[1]}"
    return output / (name + ("_zarr" if store == OME_ZARR else ""))


def projection_words(method: str, z_range: tuple[int, int] | None) -> str:
    """Return a projection in words: ``max projection of all slices``."""
    slices = "all slices" if z_range is None else f"slices {z_range[0]} to {z_range[1] - 1}"
    return f"{method} projection of {slices}"


def earlier_projections(root: Path, output: Path) -> list[tuple[Path, Projection]]:
    """Return the projected copy trees of ``root`` already in the output folder, by name."""
    return [
        (folder, read.projection)
        for folder, read in _projected_trees(output)
        if isinstance(read, ConversionRecord)
        and read.projection is not None
        and Path(read.source_root) == root
    ]


def unusable_projections(output: Path) -> list[tuple[Path, str]]:
    """Return the projected copy trees in the output folder whose record cannot be used, why.

    They are never offered; the user is told (no hidden refusal), since writing the same
    projection into such a folder again is refused (``conversion_record_unreadable``).
    """
    return [
        (folder, str(read))
        for folder, read in _projected_trees(output)
        if isinstance(read, Exception)
    ]


def _projected_trees(output: Path) -> list[tuple[Path, ConversionRecord | Exception]]:
    """Return every ``projected_*`` folder with a record in the output folder: read, or why not."""
    trees: list[tuple[Path, ConversionRecord | Exception]] = []
    if not output.is_dir():
        return trees
    for record_file in sorted(output.glob(f"{FOLDER_PREFIX}*/{RECORD_FILENAME}")):
        try:
            read = read_record(record_file.parent)
        except (ConversionRecordError, OSError) as exc:
            trees.append((record_file.parent, exc))
            continue
        if read is not None:
            trees.append((record_file.parent, read[0]))
    return trees


class _ProjectionCard:
    """The Axes step's card for Z-stacks: choose the projection, write the copies, go on."""

    def _build_projection_card(self) -> None:
        """Build the card, hidden until the roles give a depth dimension with several slices."""
        self._z_slices = 0
        self._projection_check: object | None = None  # the folder check the choice is for
        self.projection_method = widgets.Dropdown(
            options=METHOD_OPTIONS, value="", layout=widgets.Layout(width="440px")
        )
        number = widgets.Layout(width="90px")
        self.projection_first = widgets.BoundedIntText(value=0, min=0, max=0, layout=number)
        self.projection_last = widgets.BoundedIntText(value=0, min=0, max=0, layout=number)
        self.projection_look = ui.button(
            " Show this projection of the image above",
            icon="eye",
            tooltip="Project the image shown in the example, reduced, for looking only",
        )
        self.projection_tiles = widgets.HBox(layout=widgets.Layout(width="99%"))
        self.projection_tiles.add_class("mb-channel-row")
        self.projection_caption = widgets.HTML()
        self.projection_keep = widgets.Checkbox(
            value=False,
            disabled=True,
            description=KEEP_3D,
            indent=False,
            layout=widgets.Layout(width="auto"),
        )
        self.projection_where = widgets.HTML()
        self.projection_button = ui.button(WRITE_LABEL, style="success", icon="clone", main=True)
        self.projection_blocker = widgets.HTML()
        self.projection_status = widgets.HTML()
        self.projection_earlier = widgets.VBox()
        self._lay_out_projection_card()
        self.copy_note = widgets.HTML()
        self.copy_note_images = widgets.HTML()  # the same sentence, on Images
        self.copy_note.observe(
            lambda change: setattr(self.copy_note_images, "value", change["new"]), names="value"
        )
        for box in (self.projection_method, self.projection_first, self.projection_last):
            box.observe(lambda _: self._projection_changed(), names="value")
        self.preview_checked.observe(lambda _: self._projection_changed())
        self.projection_look.on_click(lambda _: self._show_projection())
        self.projection_button.on_click(lambda _: self._write_projection())

    def _lay_out_projection_card(self) -> None:
        """Put the card's widgets in one column: the choice, the look, what is kept, the click."""
        row = widgets.Layout(align_items="center", flex_flow="row wrap")
        self.projection_card = ui.card(
            "Z-stacks: save the dataset as projections",
            [
                widgets.HTML(f"<div>{INTRO}</div>"),
                self.projection_earlier,
                widgets.HBox([widgets.HTML("<b>Projection</b>&nbsp;"), self.projection_method]),
                widgets.HBox(
                    [
                        widgets.HTML("<b>Slices used: from z =</b>&nbsp;"),
                        self.projection_first,
                        widgets.HTML("&nbsp;<b>to z =</b>&nbsp;"),
                        self.projection_last,
                        widgets.HTML("&nbsp;(both included; all slices unless you change them)"),
                    ],
                    layout=row,
                ),
                self.projection_look,
                self.projection_tiles,
                self.projection_caption,
                widgets.HBox(
                    [self.projection_keep, widgets.HTML(blocked_line([NOT_ENABLED]))], layout=row
                ),
                widgets.HTML(f"<div>{KEPT}</div>"),
                self.projection_where,
                self.projection_button,
                self.projection_blocker,
                self.projection_status,
            ],
            visible=False,
        )

    # What is asked ---------------------------------------------------------------------------

    def _projection(self) -> Projection | None:
        """Return the projection chosen, None while no method is; ValueError for bad slices."""
        method = self.projection_method.value
        if method not in METHODS:
            return None
        first, last = self.projection_first.value, self.projection_last.value
        whole = first == 0 and last == self._z_slices - 1
        return Projection(method, None if whole else (first, last + 1))

    def _refresh_projection_card(self, review: object) -> None:
        """Offer the card while the roles shown give Z-stacks; forget an earlier choice's view."""
        if getattr(self, "projection_card", None) is None:
            return
        self._forget_projection_of_another_check()
        stacks = [i for i in getattr(review, "issues", ()) if i.code == Z_STACK_CODE]
        offered = bool(stacks) and self.preflight is not None and self.confirmed_mapping is None
        ui.set_visible(self.projection_card, offered)
        # With Z-stacks the tick leads to the projection, not to Confirm axis roles.
        self.preview_checked.confirmed_text = TICKED_STACKS if offered else TICKED
        if not offered:
            return
        slices = self._stack_slices()
        if slices != self._z_slices:
            self._z_slices = slices
            for box, value in ((self.projection_first, 0), (self.projection_last, slices - 1)):
                box.max = max(slices - 1, 0)
                box.value = value
        self._list_earlier()
        self._projection_changed()

    def _forget_projection_of_another_check(self) -> None:
        """Choose no method and every slice again after a new folder check.

        A method or slices chosen for one folder are never offered for another folder, or for
        the same folder checked again (its files may differ): the choice has no default. A
        pixel size or masks stated on the same check keep it (the same discovery).
        """
        check = None if self.preflight is None else self.preflight.discovery
        if check is self._projection_check:
            return
        self._projection_check = check
        self.projection_method.value = ""
        self._z_slices = 0  # the slices are set again to the whole stack below

    def _stack_slices(self) -> int:
        """Return the smallest number of depth slices among the images, under the roles shown."""
        dim = self.mapping.index_of("Z")
        sizes = [
            f.shape[dim]
            for f in self.preflight.metadata.files
            if f.readable and len(f.shape) == len(self.mapping.axes)
        ]
        return min(sizes, default=1)

    def _projection_changed(self) -> None:
        """Say where the copies go and what still stops the click; clear an earlier look."""
        if getattr(self, "projection_card", None) is None or self.preflight is None:
            return
        self.projection_tiles.children, self.projection_caption.value = [], ""
        reasons: list[str] = []
        chosen = None
        try:
            chosen = self._projection()
        except ValueError:
            reasons.append("the first slice is after the last one")
        if chosen is None and not reasons:
            reasons.append("no projection method is chosen")
        if not self.preview_checked.value:
            reasons.append("the example is not ticked: click Yes, the example looks right above")
        self.projection_button.disabled = bool(reasons)
        self.projection_look.disabled = chosen is None
        self.projection_blocker.value = blocked_line(reasons)
        if chosen is None:
            self.projection_where.value = ""
            return
        store = self._copy_store()
        dest = copies_folder(self._output_path(self.preflight.discovery.root), chosen, store)
        kind = " as OME-Zarr (asked on Images)" if store == OME_ZARR else ""
        self.projection_where.value = (
            f"<div>The {html.escape(projection_words(chosen.method, chosen.z_range))} of every "
            f"image is written{kind} to <b>{html.escape(str(dest))}</b>.</div>"
        )

    # Looking -------------------------------------------------------------------------------

    def _show_projection(self) -> None:
        """Draw the chosen projection of the example's image, reduced, for looking only."""
        image = self.axes_viewer.image
        try:
            chosen = self._projection()
        except ValueError:
            chosen = None
        if image is None or chosen is None:
            return
        source = self._axes_source(image)
        if source.mapping is None:
            self.projection_caption.value = ui.status("warn", "No picture.", [source.problem])
            return
        request = ViewRequest(
            t=self.axes_viewer.sliders["T"].value,
            memory_limit=source.memory_limit,
            expected=source.expected,
        )
        try:
            view = read_projection_view(source.path, source.mapping, source.shape, chosen, request)
        except READ_ERRORS as exc:
            self.projection_tiles.children = []
            self.projection_caption.value = ui.status(
                "warn", "This projection cannot be shown.", [str(exc)]
            )
            return
        self.projection_tiles.children = self.axes_viewer._tile_widgets(view, source)
        self.projection_caption.value = ui.help_text(
            f"<b>{html.escape(image)}</b> · "
            f"{html.escape(projection_words(chosen.method, chosen.z_range))}, drawn from the "
            "reduced planes of the example: for looking only, the copies are computed from "
            "every pixel."
        ).value

    # Writing -------------------------------------------------------------------------------

    def _write_projection(self) -> ConversionResult | None:
        """Write the projected copies with the roles shown, then open them as the experiment.

        Returns
        -------
        ConversionResult or None
            None when the click is not available or an unexpected problem stopped it.
        """
        if self.projection_button.disabled or self.preflight is None:
            return None
        chosen = self._projection()
        root = Path(self.preflight.discovery.root)
        output = self._output_path(root)
        self._busy(self.projection_button, "Writing the projected copies…")
        wait = Wait(self.projection_status)
        wait.start("Checking the images and the folder")

        def progressed(done: int, total: int, image: str) -> None:
            if image:
                wait.step(done, total, image)

        try:
            with self.log.attached():
                result = convert_experiment(
                    root,
                    self.mapping,
                    copies_folder(output, chosen, self._copy_store()),
                    exclude=self.preflight.excluded,
                    missing_as_singleton=self.missing_as_singleton.value,
                    memory_limit=self._memory_limit(),
                    progress=progressed,
                    masks_folder=self._runs_masks_folder(),
                    projection=chosen,
                    store=self._copy_store(),
                )
        except Exception as exc:  # unexpected failures still get a readable message
            self.log.lines.append(traceback.format_exc())
            self.projection_status.value = ui.status(
                "error",
                "The copies could not be written because of an unexpected problem.",
                [f"{type(exc).__name__}: {exc}", "Open Technical details for the full report."],
            )
            return None
        finally:
            self._idle(self.projection_button, WRITE_LABEL, "clone")
            self._show_log()
        if not result.written:
            self.projection_status.value = ui.status(
                "error", "No copies were written.", ["Nothing was written: see below."]
            ) + issues_brief_html(result.issues)
            return result
        self._use_copies(result.destination, output)
        return result

    def _list_earlier(self) -> None:
        """Offer the projected copies of these images already in the output folder."""
        root = Path(self.preflight.discovery.root)
        output = self._output_path(root)
        buttons = []
        for folder, projection in earlier_projections(root, output):
            words = projection_words(projection.method, projection.z_range)
            if folder.name.endswith("_zarr"):
                words += f", as OME-Zarr ({ZARR_COPY_SUFFIX})"
            button = ui.button(
                f" Continue with the copies written earlier: {words}",
                icon="folder-open",
                tooltip=str(folder),
            )
            button.on_click(lambda _, f=folder, o=output: self._use_copies(f, o))
            buttons.append(button)
        notes = [
            widgets.HTML(
                ui.status(
                    "warn",
                    f"Not offered: the copies written earlier in {folder}. Their conversion "
                    f"record cannot be used ({why}); to write them again, choose another "
                    "output folder.",
                )
            )
            for folder, why in unusable_projections(output)
        ]
        self.projection_earlier.children = [*buttons, *notes]

    def _use_copies(self, copies: Path, output: Path) -> None:
        """Open the copy tree as the experiment (scan and check again), the output folder kept."""
        self.output.value = str(output)  # an empty box would mean the copies' own default
        self.folder.value = str(copies)
        self._quick_scan()
        self.check_folder()
        self.projection_status.value = ""

    def _show_copy_note(self) -> None:
        """Say, while a copy tree is the experiment, what it is a copy of."""
        if getattr(self, "copy_note", None) is None:
            return
        pre = self.preflight
        conversion = None if pre is None else pre.conversion
        if conversion is None or conversion.source_root is None:
            self.copy_note.value = ""
            return
        made = conversion.projection
        what = (
            "recorded copies"
            if made is None
            else f"recorded 2-D copies ({projection_words(made.method, made.z_range)})"
        )
        count = plural(len(pre.discovery.images), "image")
        self.copy_note.value = ui.status(
            "info",
            f"You are working on {what} of {conversion.source_root}: {count}.",
            [
                f"Conversion record: {Path(pre.discovery.root) / conversion.record}",
                "The originals are kept where they are, unchanged."
                + (f" They are recorded for 3-D-capable tools; {NOT_ENABLED}." if made else ""),
            ],
        )


__all__ = [
    "CHOOSE",
    "FOLDER_PREFIX",
    "KEEP_3D",
    "NOT_ENABLED",
    "_ProjectionCard",
    "copies_folder",
    "earlier_projections",
    "projection_words",
    "unusable_projections",
]
