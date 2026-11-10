"""Step 2 of the notebook: the axis roles and the axis check."""

from __future__ import annotations

import html

import ipywidgets as widgets

from . import notebook_style as ui
from .axes import axis_length_ranges, axis_name, file_mapping_for
from .axis_check import AXIS_CHECK_CODES
from .axis_review import review_axes
from .description_layouts import declared_layouts, is_heterogeneous, layout_kinds
from .image_type import FLUORESCENCE
from .notebook_axes import axis_check_html, header_marks
from .notebook_options import IMAGE_TYPE_OPTIONS, ROLE_OPTIONS
from .notebook_render import (
    blocked_line,
    issues_brief_html,
    plane_summary_html,
    role_effect,
)
from .run import Preflight
from .validation import plural

ROLES_HELP = (
    "One box per dimension, in the order stored in the files. Exactly one Y and one X; a "
    "dimension with more than one plane needs Z, C or T."
)


class _AxisStep:
    """Step 2's axis roles: the role dropdowns, the axis check and the missing-axes card."""

    # Step 2 ------------------------------------------------------------------------------

    def _build_axis_step(self) -> None:
        """Build step 2: the role boxes, the missing-axes card, the preview, the confirmation."""
        self.axis_box = widgets.VBox()
        self.role_dropdowns: list[widgets.Dropdown] = []
        self.role_notes: list[widgets.HTML] = []
        self.plane_summary = widgets.HTML()
        self.axis_status = widgets.HTML()
        self._build_missing_card()
        self._build_preview_card()
        self.confirm_button = ui.button(
            " Confirm axis roles",
            style="success",
            icon="check",
            tooltip="Use these roles for every image of the experiment",
            main=True,
        )
        self.confirm_blocker = widgets.HTML()
        self.confirmed_status = widgets.HTML()
        self.axis_next = widgets.HTML()
        self.missing_as_singleton.observe(lambda _: self._mapping_changed())
        self.preview_button.on_click(lambda _: self.show_preview())
        self.preview_file.observe(lambda _: self._preview_file_changed(), names="value")
        for slider in self.preview_sliders.values():
            slider.observe(lambda _: self._render_preview_if_shown(), names="value")
        self.preview_checked.observe(lambda _: self._update_confirm_button())
        self.confirm_button.on_click(lambda _: self._toggle_confirmation())
        self._build_image_type_card()

    def _build_missing_card(self) -> None:
        """Build the card asking whether images lacking an axis have one plane along it."""
        self.missing_note = widgets.HTML()
        self.missing_as_singleton = ui.ConfirmToggle(
            "Do these images really have a single plane along the missing axes?",
            "Usually the file leaves out a dimension of length 1, for example when an image "
            "has only one channel. If these images were exported differently, export them "
            "again instead.",
            "Yes, they have a single plane there",
            "Confirmed: the missing axes are read as length 1 (their coordinate is 0).",
            "Undo: they are different",
        )
        self.missing_card = ui.card(
            "Images with fewer axes",
            [self.missing_note, self.missing_as_singleton.widget],
            visible=False,
        )

    def _build_preview_card(self) -> None:
        """Build the preview card: image, sliders, the picture and its confirmation."""
        self.preview_file = widgets.Dropdown(layout=widgets.Layout(width="99%"))
        self.preview_sliders = {
            role: widgets.IntSlider(
                description=f"{role.lower()} ({name})",
                min=0,
                max=0,
                continuous_update=False,
                style={"description_width": "90px"},
                layout=widgets.Layout(width="420px"),
            )
            for role, name in (("Z", "depth"), ("C", "channel"), ("T", "time"))
        }
        self.preview_button = ui.button(
            " Show preview",
            style="info",
            icon="eye",
            tooltip="Read one plane with the roles above and show it",
        )
        self.preview_image = widgets.Image(format="png", layout=widgets.Layout(max_width="99%"))
        ui.set_visible(self.preview_image, False)  # no broken-picture icon before a preview
        self.preview_caption = widgets.HTML()
        self.preview_checked = ui.ConfirmToggle(
            "Does the example show your sample as you expect?",
            "Each channel, time point or slice should be a normal picture of the right size. "
            "Stripes or a thin strip mean a role is on the wrong dimension: change it.",
            "Yes, the example looks right",
            "Example checked. Now click Confirm axis roles below.",
            "Undo: the example does not look right",
        )
        self.preview_checked.disabled = True
        self.preview_blocker = widgets.HTML()
        ui.set_visible(self.preview_button, False)  # the example is drawn by itself
        self.preview_more = ui.accordion(
            "Another image, or other planes",
            [
                widgets.VBox(
                    [
                        self.preview_file,
                        *self.preview_sliders.values(),
                        ui.help_text(
                            "The sliders choose the time point or slice of the row of channels, "
                            "and the channel of the other rows."
                        ),
                    ]
                )
            ],
        )
        self.preview_card = ui.card(
            "Example: one image as these roles read it",
            [
                widgets.HBox([self.preview_image]),
                self.preview_caption,
                self.preview_more,
                self.preview_button,
                self.preview_checked.widget,
                self.preview_blocker,
            ],
        )
        self.axis_sizes_slot = widgets.VBox()  # the pixel size card, in the shipped notebook
        self.roles_help = ui.help_text(ROLES_HELP)  # folded into About this step there

    def _build_image_type_card(self) -> None:
        """Build the image type card, hidden until the axis roles are confirmed."""
        self.image_type = widgets.RadioButtons(
            options=IMAGE_TYPE_OPTIONS, value=FLUORESCENCE, layout=widgets.Layout(width="99%")
        )
        self.image_type_note = widgets.HTML()
        self.image_type_checked = ui.ConfirmToggle(
            "Is this the right image type for your images?",
            "Fluorescence images have one stain or marker per channel, and later bricks "
            "normalise each channel on its own. RGB images are colour pictures whose red, "
            "green and blue channels are normalised together, so their colours are kept. "
            "The type does not change the histograms.",
            "Yes, this is the right image type",
            "Image type confirmed.",
            "Undo: choose another image type",
        )
        self.image_type_card = ui.card(
            "Image type",
            [
                ui.help_text(
                    "Suggested from the image metadata once the axis roles are confirmed: RGB "
                    "when the images are stored as RGB colour, fluorescence otherwise. It is "
                    "recorded in <b>run_manifest.json</b> for later bricks."
                ),
                self.image_type,
                self.image_type_note,
                self.image_type_checked.widget,
            ],
            visible=False,
        )
        self.image_type.observe(lambda _: self._show_image_type_note(), names="value")
        self.image_type_checked.observe(self._image_type_answered)

    def _refresh_axis_step(self) -> None:
        """Rebuild step 2 for the checked folder: one role box per axis, reset the preview."""
        pre = self.preflight
        self.axis_next.value = ""
        self._preview_shown = False
        self._preview_drawn = None
        self._close_image_type()
        waiting = self._axis_waiting()
        if waiting:
            self.axis_box.children = [widgets.HTML(waiting)]
            return
        grid = self._role_grid()
        self._refresh_missing_card(pre)
        self._reset_preview()
        self._set_locked(False)
        roles = ui.card(
            "Axis roles",
            [self.roles_help, grid, self.plane_summary],
        )
        left = widgets.VBox(
            [roles, self.missing_card, self.axis_status, self.axis_sizes_slot],
            layout=widgets.Layout(flex="1 1 0", min_width="0"),
        )
        right = widgets.VBox(
            [self.preview_card], layout=widgets.Layout(flex="1 1 0", min_width="0")
        )
        columns = widgets.HBox([left, right], layout=widgets.Layout(width="99%"))
        columns.add_class("mb-columns")
        self.axis_box.children = [
            columns,
            self.convert_card,
            self.confirm_button,
            self.confirm_blocker,
            self.confirmed_status,
            self.image_type_card,
            self.axis_next,
        ]
        self._fill_preview_files()
        self._mapping_changed()

    def _axis_waiting(self) -> str:
        """Return why no roles can be shown yet (HTML), or an empty string."""
        if self.preflight is None:
            return ui.status("info", "Waiting for step 1.", ["Check the experiment folder first."])
        if self.mapping is None:
            kinds = self._layout_kinds()
            if kinds:
                return ui.status(
                    "error",
                    "No axis roles are suggested: the units declare different layouts.",
                    [
                        f"The saved description declares different layouts ({kinds}). "
                        "This version runs one layout per experiment, so the "
                        "run stops with R_HETEROGENEOUS_LAYOUT_NOT_ENABLED (see Check and "
                        "save). Exporting the images again would not change that."
                    ],
                )
            return ui.status(
                "error",
                "No axis roles can be suggested.",
                [
                    "The images have no readable headers, or their dimensions are "
                    "arranged differently.",
                    "See the messages in step 1.",
                ],
            )
        return ""

    def _layout_kinds(self) -> str:
        """Return the kinds of layout the saved description declares when they differ, else ''."""
        state = self._published()
        if state is None:
            return ""
        layouts = declared_layouts(state.description)
        return layout_kinds(layouts) if is_heterogeneous(layouts) else ""

    def _role_grid(self) -> widgets.GridBox:
        """Build one box per axis, with its length, the suggested role and a dropdown."""
        ranges = axis_length_ranges(self.mapping, self.preflight.metadata.files)
        self.role_dropdowns, self.role_notes, axis_cards = [], [], []
        for axis, role, (low, high) in zip(
            self.mapping.axes, self.mapping.roles, ranges, strict=True
        ):
            dropdown = widgets.Dropdown(
                options=ROLE_OPTIONS, value=role or "-", layout=widgets.Layout(width="99%")
            )
            note = widgets.HTML()
            dropdown.observe(lambda _: self._roles_edited(), names="value")
            self.role_dropdowns.append(dropdown)
            self.role_notes.append(note)
            length = f"{low}" if low == high else f"{low} to {high}"
            box = widgets.VBox(
                [
                    widgets.HTML(
                        f"<b>{html.escape(axis_name(axis))}</b> in the file"
                        f"<div class='mb-help'>Length {length} · suggested: "
                        f"{html.escape(role or 'no role')}</div>"
                    ),
                    dropdown,
                    note,
                ]
            )
            box.add_class("mb-card")
            axis_cards.append(box)
        return widgets.GridBox(
            axis_cards,
            layout=widgets.Layout(
                grid_template_columns="repeat(auto-fill, minmax(170px, 1fr))", grid_gap="8px"
            ),
        )

    def _reset_preview(self) -> None:
        """Clear the preview and its answer: new roles need a new look."""
        self.preview_checked.set(False, notify=False)
        self.preview_checked.disabled = True
        for slider in self.preview_sliders.values():
            ui.set_visible(slider, False)
        self.preview_image.value = b""
        ui.set_visible(self.preview_image, False)
        self.preview_caption.value = ""
        self.confirmed_status.value = ""

    def _refresh_missing_card(self, pre: Preflight) -> None:
        """Show the images lacking some of the axes, and ask about them."""
        # OME-Zarr images may store the same roles in another order; only roles that
        # an image lacks need the user's confirmation.
        lacking: dict[str, str] = {}
        for f in pre.metadata.files:
            resolved = file_mapping_for(f, self.mapping) if f.readable else None
            if resolved is not None and resolved.singleton_roles:
                lacking[f.relative_path] = "".join(resolved.singleton_roles)
        ui.set_visible(self.missing_card, bool(lacking))
        if not lacking:
            return
        by_axes: dict[tuple[str, str], list[str]] = {}
        headers = {f.relative_path: f for f in pre.metadata.files}
        for rel, missing in lacking.items():
            by_axes.setdefault((headers[rel].axes, missing), []).append(rel)
        lines = []
        for (axes, missing), paths in sorted(by_axes.items()):
            names = ", ".join(axis_name(a) for a in missing)
            lines.append(
                f"{plural(len(paths), 'image')} with axes <b>{html.escape(axes)}</b> "
                f"lack {html.escape(names)}" + ui.path_list(paths, limit=5)
            )
        self.missing_note.value = ui.status(
            "warn",
            "Some images have fewer axes than the others.",
            [
                *lines,
                "This usually means they have a single channel or slice, which the file "
                "does not store. Confirm below only if that is the case.",
            ],
            raw=True,
        )

    def _roles_edited(self) -> None:
        # A preview checked with other roles says nothing about the new ones.
        if self.preview_checked.value:
            self.preview_checked.set(False, notify=False)
        self._mapping_changed()

    def _mapping_changed(self) -> None:
        """Check the roles as edited and show what they mean for every image."""
        if self.preflight is None or self.mapping is None or not self.role_dropdowns:
            return
        try:
            self.mapping = self._current_mapping()
        except ValueError as exc:
            self.axis_status.value = ui.status("error", str(exc))
            self.preview_checked.disabled = True  # the preview shown is not of these roles
            self._review_ok, self._review_errors = False, ()
            self.preview_image.value, self._preview_drawn = b"", None
            ui.set_visible(self.preview_image, False)
            self.preview_caption.value = ui.status(
                "warn", "No example: these roles cannot be used together."
            )
            self._gate_preview_tick()
            return
        ranges = axis_length_ranges(self.mapping, self.preflight.metadata.files)
        marks = header_marks(self.mapping, self.preflight.metadata.files)
        for index, (note, role, (low, high)) in enumerate(
            zip(self.role_notes, self.mapping.roles, ranges, strict=True)
        ):
            note.value = (
                f"<div class='mb-help'>{role_effect(role, low, high)}</div>{marks.get(index, '')}"
            )
        self.plane_summary.value = plane_summary_html(self.mapping, ranges)
        review = review_axes(
            self.preflight.metadata,
            self.mapping,
            missing_as_singleton=self.missing_as_singleton.value,
        )
        if review.issues:
            others = [i for i in review.issues if i.code not in AXIS_CHECK_CODES]
            self.axis_status.value = axis_check_html(review) + (
                issues_brief_html(others) if others else ""
            )
            self.axis_next.value = ""
        else:
            self.axis_status.value = ui.status(
                "ok",
                "These roles are valid for every image, and agree with the axes their "
                "metadata names.",
            )
            self.axis_next.value = ui.next_steps(
                "Look at the example in Step 2, then confirm the axis roles.",
                "It is redrawn whenever a role changes; other planes are one click away.",
            )
        self._review_ok = review.ok
        self._review_errors = tuple(
            dict.fromkeys(i.code.replace("_", " ") for i in review.issues if i.is_error)
        )
        self._refresh_convert_card(review)
        # The example is drawn by itself and redrawn for every change of the roles.
        self._configure_sliders(reset=not self._preview_shown)
        self._preview_shown = True
        self._render_preview()
        self._update_confirm_button()

    def _update_confirm_button(self) -> None:
        """Enable *Confirm axis roles* when nothing blocks it; say beside it what does."""
        if self.confirmed_mapping is not None:
            self.confirm_button.disabled = False
            self.confirm_blocker.value = ""
            return
        blockers = self._confirm_blockers()
        self.confirm_button.disabled = bool(blockers)
        self.confirm_blocker.value = blocked_line(blockers)
        self.confirm_button.tooltip = (
            "Fix the axis messages and check the preview first"
            if blockers
            else "Use these roles for every image of the experiment"
        )

    def _confirm_blockers(self) -> list[str]:
        """Return what stops *Confirm axis roles* now, the first thing to do first."""
        blockers = []
        if not getattr(self, "_review_ok", False):
            codes = getattr(self, "_review_errors", ())
            named = f" ({', '.join(codes)})" if codes else ""
            blockers.append(f"these roles have an error{named}: see the red message above")
        if not self._preview_of(self.mapping):
            blockers.append("no example of these roles is drawn: see the message under it")
        elif not self.preview_checked.value:
            blockers.append("the example is not ticked: click Yes, the example looks right")
        return blockers
