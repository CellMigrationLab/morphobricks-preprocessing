"""The axes of the quick scan's image: proposed from its header, changed right there.

Owner (11 November 2026, the new axes flow): the quick scan opens one image and proposes its
axes from its header; when the picture looks wrong the user changes a role right there and
sees it redrawn. What the user leaves there becomes the proposal for the whole experiment:
the folder check (the "deep scan") reads every header, the roles are checked against every
image (:func:`.axis_review.review_axes`, as always), and the Axes step confirms them for the
whole dataset, where they can still be changed.

The image type (separate channels or colour RGB) is **proposed** here too, display only
(owner, 12 November 2026): from the header of the image shown before the folder check, and from
every header after it. Nothing of it is recorded here; it is confirmed on the Channels step
(:mod:`.notebook_image_type_line`), where the same headers pre-fill it.

Nothing is confirmed here. The roles of this module are a proposal, held per arrangement of
axes (a header's letters) and only for an arrangement the user changed; an arrangement left
alone keeps the header's roles, and a header that does not name its axes gets no picture until
the user says what its dimensions are. The Axes step, its example, its tick and its
confirmation are unchanged: they start from this proposal instead of the header's.
"""

from __future__ import annotations

import html

import ipywidgets as widgets

from . import notebook_style as ui
from .axes import AxisMapping, axis_name, file_mapping_for, prefill_mapping
from .axis_review import check_mapping, review_axes
from .metadata import TiffMetadata
from .notebook_image_type_line import MIXED
from .notebook_options import ROLE_OPTIONS
from .notebook_viewers import CONFIRMED_NOTE, header_roles
from .validation import plural

CHOSEN_NOTE = "read with the roles chosen above, not confirmed yet"
AXES_NOTE = "read with the roles on Axes (step 2), not confirmed yet"
ROLES_HELP = (
    "<b>Axes of this image</b>, proposed from its header. If a picture looks wrong (a strip, "
    "stripes, channels that are slices), change a role here: the pictures are redrawn. What "
    "you leave here is the proposal for every image; it is checked against all of them when "
    "the headers are read, and confirmed on Axes (step 2), where it can still be changed."
)
CHANGE_NOTE = (
    "The axes can be changed: choose another role in a dimension's list (the pictures are "
    "redrawn). Once confirmed, <b>Change axis roles</b> takes the confirmation back."
)


TYPE_CONFIRMED_ON = "Display only: nothing is recorded here. It is confirmed on Channels (step 3)."


def header_type(meta: TiffMetadata) -> str:
    """Return what a header says the channels are: ``colour (RGB)`` or ``separate channels``."""
    return "colour (RGB)" if meta.rgb_samples else "separate channels"


def scan_type_html(metas: list[TiffMetadata], *, one: bool = False) -> str:
    """Return the image type the readable headers propose, display only (HTML).

    ``one`` says it of the single image shown before the folder check.
    """
    said = [header_type(meta) for meta in metas if meta.readable]
    if not said:
        return ""
    if one:
        return (
            f"<div class='mb-help'><b>Image type</b>, from this image's header: "
            f"{html.escape(said[0])}. {TYPE_CONFIRMED_ON}</div>"
        )
    kinds = sorted(set(said))
    if len(kinds) == 1:
        return ui.status(
            "info",
            f"Image type proposed from the headers: {kinds[0]} in all "
            f"{plural(len(said), 'image')}.",
            [TYPE_CONFIRMED_ON],
        )
    counts = ", ".join(f"{kind} in {plural(said.count(kind), 'image')}" for kind in kinds)
    return ui.status(
        "warn",
        f"The image headers differ: {counts}.",
        [
            "Nothing is proposed: the type of each folder is confirmed on Channels (step 3). "
            + MIXED
        ],
    )


def roles_words(mapping: AxisMapping) -> str:
    """Return the roles in the order of the file: ``C, Y, X`` (``-`` for none)."""
    return ", ".join(role or "-" for role in mapping.roles)


class _ScanAxes:
    """The role row of the quick scan, and the proposal it leaves for the Axes step."""

    def _build_scan_axes(self) -> None:
        """Build the role row (empty until an image is shown) and the line after the check."""
        self._scan_roles: dict[str, AxisMapping] = {}
        self._scan_boxes: list[widgets.Dropdown] = []
        self._scan_row_axes: str | None = None
        self._scan_applied: object | None = None
        self._scan_filling = False
        self.scan_roles_row = widgets.HBox(
            layout=widgets.Layout(flex_flow="row wrap", align_items="flex-end", width="99%")
        )
        self.scan_type_note = widgets.HTML()  # the shown image's type, display only
        self.scan_roles_card = widgets.VBox(
            [ui.help_text(ROLES_HELP), self.scan_roles_row, self.scan_type_note],
            layout=widgets.Layout(width="99%"),
        )
        ui.set_visible(self.scan_roles_card, False)
        self.scan_axes_line = widgets.HTML()
        self.scan_type_line = widgets.HTML()  # every header's type, display only
        self.axes_change_note = ui.help_text(CHANGE_NOTE)
        self.scan_viewer.on_draw(self._scan_roles_follow)

    def _reset_scan_axes(self) -> None:
        """Forget the roles chosen at an earlier scan: a new scan proposes afresh."""
        self._scan_roles, self._scan_row_axes = {}, None
        self.scan_axes_line.value = self.scan_type_line.value = ""

    # The row -------------------------------------------------------------------------------

    def _scan_roles_follow(self) -> None:
        """Show the role row of the image now in the viewer (before the headers are read)."""
        image = self.scan_viewer.image
        if image is None or self._checked_current():
            ui.set_visible(self.scan_roles_card, False)
            return
        _, meta, _ = self._header_of(image)
        if meta is None or not meta.readable:
            ui.set_visible(self.scan_roles_card, False)
            return
        if meta.axes != self._scan_row_axes:
            self._build_scan_row(meta)
        self.scan_type_note.value = scan_type_html([meta], one=True)
        ui.set_visible(self.scan_roles_card, True)

    def _build_scan_row(self, meta: TiffMetadata) -> None:
        """Build one list per dimension of the image, holding the role proposed or chosen."""
        mapping = self._scan_roles.get(meta.axes) or prefill_mapping(meta.axes)
        self._scan_row_axes = meta.axes
        self._scan_boxes, cells = [], []
        for axis, role, length in zip(meta.axes, mapping.roles, meta.shape, strict=True):
            box = widgets.Dropdown(
                options=ROLE_OPTIONS, value=role or "-", layout=widgets.Layout(width="210px")
            )
            box.observe(lambda _: self._scan_role_changed(), names="value")
            self._scan_boxes.append(box)
            label = widgets.HTML(
                f"<div class='mb-help'><b>{html.escape(axis_name(axis))}</b> in the file · "
                f"length {length}</div>"
            )
            cells.append(widgets.VBox([label, box], layout=widgets.Layout(margin="0 12px 0 0")))
        self.scan_roles_row.children = cells

    def _scan_role_changed(self) -> None:
        """Keep the roles the user chose for this arrangement and redraw the image with them."""
        if self._scan_filling or self._scan_row_axes is None:
            return
        roles = tuple(box.value for box in self._scan_boxes)
        self._scan_roles[self._scan_row_axes] = AxisMapping(self._scan_row_axes, roles)
        self.scan_viewer.refresh()

    # What the viewer reads with -------------------------------------------------------------

    def _scan_roles_for(self, meta: TiffMetadata) -> tuple[AxisMapping | None, str]:
        """Return the roles the Images viewer reads an image with, or why it draws nothing.

        After the folder check: the roles on Axes, when the image fits them. Before: the
        roles the user chose for the image's arrangement, else the header's.
        """
        if self._checked_current() and self.mapping is not None and meta.readable:
            fitted = file_mapping_for(meta, self.mapping)
            if fitted is not None:
                return fitted, ""
        chosen = self._scan_roles.get(meta.axes) if meta.readable else None
        if chosen is None:
            return header_roles(meta)
        errors = [issue for issue in check_mapping(chosen, [meta]) if issue.is_error]
        if errors:
            return None, errors[0].message
        return chosen, ""

    def _scan_note(self, meta: TiffMetadata) -> str:
        if self._checked_current() and self.mapping is not None:
            return CONFIRMED_NOTE if self.confirmed_mapping is not None else AXES_NOTE
        if meta.axes in self._scan_roles:
            return CHOSEN_NOTE
        return super()._scan_note(meta)

    # The proposal for the whole experiment --------------------------------------------------

    def _scan_proposal(self) -> AxisMapping | None:
        """Return the roles chosen at the scan for the experiment's arrangement, if any."""
        if self.mapping is None:
            return None
        chosen = self._scan_roles.get(self.mapping.axes)
        return None if chosen is None or chosen.roles == self.mapping.roles else chosen

    def _refresh_axis_step(self) -> None:
        """Start the Axes step of a new check from the roles chosen at the scan, once."""
        boxes = getattr(self, "_scan_roles", None)
        if boxes and self.preflight is not None and self._scan_applied is not self.preflight:
            self._scan_applied = self.preflight
            chosen = self._scan_proposal()
            if chosen is not None:
                self.mapping = chosen
                self._quick_seeded = self.preflight  # the user's roles, not an earlier run's
        super()._refresh_axis_step()

    def _show_scan_type(self) -> None:
        """Say, on Images, the image type every header the check read proposes (display only)."""
        pre = self.preflight
        files = list(pre.metadata.files) if pre is not None else []
        self.scan_type_line.value = scan_type_html(files)

    def _show_scan_axes(self) -> None:
        """Say, on Images, whether the proposed roles fit every image the check read."""
        self._show_scan_type()
        pre, mapping = self.preflight, self.mapping
        if pre is None or not pre.metadata.files:
            self.scan_axes_line.value = ""
            return
        if mapping is None:
            self.scan_axes_line.value = ui.status(
                "warn", "Axes: no roles can be proposed for all images. See Axes (step 2)."
            )
            return
        review = review_axes(
            pre.metadata, mapping, missing_as_singleton=self.missing_as_singleton.value
        )
        chosen = self._scan_roles.get(mapping.axes)
        suggested = pre.suggested_mapping
        source = (
            "proposed from the image headers"
            if suggested is not None and suggested.roles == mapping.roles
            else "chosen by you on the example"
            if chosen is not None and chosen.roles == mapping.roles
            else "as edited on Axes"
        )
        words = f"Axes {roles_words(mapping)} ({source})"
        count = plural(len([f for f in pre.metadata.files if f.readable]), "image")
        if not review.issues:
            self.scan_axes_line.value = ui.status(
                "ok",
                f"{words}: checked against all {count}, they fit every one. Confirm them on "
                "Axes (step 2); they can still be changed there.",
            )
            return
        errors = list(dict.fromkeys(i.code.replace("_", " ") for i in review.issues if i.is_error))
        what = f"they do not fit ({', '.join(errors)})" if errors else "there is a warning"
        self.scan_axes_line.value = ui.status(
            "warn",
            f"{words}: checked against all {count}, {what}. See Axes (step 2), where they can "
            "be changed.",
        )


__all__ = [
    "AXES_NOTE",
    "CHANGE_NOTE",
    "CHOSEN_NOTE",
    "ROLES_HELP",
    "TYPE_CONFIRMED_ON",
    "_ScanAxes",
    "header_type",
    "roles_words",
    "scan_type_html",
]
