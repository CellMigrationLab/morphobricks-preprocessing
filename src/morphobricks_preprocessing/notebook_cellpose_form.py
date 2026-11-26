"""The Cellpose notebook's settings as widgets: one form per step, in every notebook environment.

``MorphoBricks_Cellpose.ipynb`` used Colab form fields (``# @param``), which are plain comments
in JupyterLab and LabConstrictor, so a user there had to edit Python. Each step's cell now
shows a form (:class:`StepForm`) built from the Preprocessing app's helpers in
:mod:`.notebook_style` (``page``, ``card``, ``help_text``, ``button``, ``clean_path``,
``ask_directory``, ``ask_open_file``, ``no_dialog_status``): the fields, one main button, and
under it what the step shows. The button calls the same :class:`.CellposeWorkflow` method a
cell called before, with the fields' values exactly as given (cell 3's paths tidied as pasted,
``clean_path``); a refusal that stopped the cell (:class:`.CellStop`) is shown with the same
message, verbatim, in a red card.

The defaults are those of the former form: overwrite, the weights download and the DINOv3
installation are off, the device is ``cuda`` (an unavailable GPU is refused, never replaced by
the CPU), the model ``cpsam_v2`` and a blank diameter (the model's native scale).
"""

from __future__ import annotations

from collections.abc import Callable

import ipywidgets as widgets
from IPython.display import display

from . import notebook_style as ui
from . import segmentation_cellpose as cp
from .notebook_cellpose import CellposeWorkflow, CellStop, output_mask_types
from .notebook_cellpose_channels import OUTPUT_NAMES, _card, _verbatim
from .notebook_cellpose_fields import (
    ADVANCED,
    DEVICE_OPTIONS,
    FIELD,
    SEPARATE_NUCLEI,
    STYLE,
    full_help,
    model_cards,
    model_choice,
    model_fields,
    output_cards,
    output_fields,
    path_row,
    show_nuclei_box,
    text_field,
)
from .notebook_cellpose_guard import check_current, check_folders, take_over
from .notebook_cellpose_summary import summary_html, summary_widget, used
from .notebook_cellpose_view import run_entries, run_viewer
from .notebook_open_progress import opening
from .notebook_stepper import field_rows

__all__ = [
    "ADVANCED",
    "DEVICE_OPTIONS",
    "MODEL_CHANGED",
    "STOPPED",
    "CellposeNotebook",
    "StepForm",
]

STOPPED = "This step stopped"
"""The title of the card that shows a refusal; its message is the refusal, unchanged."""
MODEL_CHANGED = (
    "The model, the model file or the device of the Load the model step (or the nuclei's own "
    "model) changed "
    "since the model was loaded: "
    "press Load the model again. A model is never reloaded without that."
)
"""The refusal of a preview or a run whose model choice no longer matches the loaded model."""


class StepForm:
    """One step of the notebook: its fields, its main button, and what the step shows.

    Parameters
    ----------
    workflow
        The notebook's workflow; its cards are shown under the button.
    title, purpose
        The step's page title and one-sentence purpose (HTML).
    cards
        The cards of fields, above the button.
    label, icon
        The main button's text and icon.
    action
        What the button does: one call on ``workflow`` with the fields' values.
    fields
        The fields by the name of the setting they hold (as the former form named it).

    Attributes
    ----------
    stopped
        The refusal of the last press (:class:`.CellStop`), or None.
    """

    def __init__(
        self,
        workflow: CellposeWorkflow,
        title: str,
        purpose: str,
        cards: list[widgets.Widget],
        label: str,
        icon: str,
        action: Callable[[], object],
        fields: dict[str, widgets.Widget],
    ) -> None:
        """Build the page; nothing runs until the button is pressed."""
        self.workflow, self.action, self.fields = workflow, action, fields
        self.button = ui.button(f" {label}", style="primary", icon=icon, main=True)
        self.output = widgets.Output(layout=FIELD)
        self.stopped: CellStop | None = None
        self.notice = widgets.HTML()  # says when the workflow was replaced (cell 3 run again)
        self.title, self.purpose = title, purpose
        # The step's controls without the page: the stepper of segmentation_app shows these
        # under its own one-line title; a notebook cell shows them on the page (``widget``).
        self.body = widgets.VBox([*cards, self.button, self.notice, self.output], layout=FIELD)
        self.widget = ui.page(title, purpose, [self.body])
        self.button.on_click(lambda _: self.submit())

    @property
    def refusal(self) -> str:
        """The message of the last refusal, or ``""``."""
        return "" if self.stopped is None else str(self.stopped)

    def submit(self, *, reraise: bool = False) -> None:
        """Run the step with the fields as they are now; show a refusal, unchanged, in a card.

        Any other error is left to the output area, which shows it with its traceback (in a
        kernel it goes no further: a button's error has nowhere else to go). ``reraise``: it
        is also raised again, for a cell that submits the form itself (cell 4, #574).
        """
        self.stopped = None
        self.output.clear_output()
        self.button.disabled = True
        unexpected: BaseException | None = None
        try:
            with self.output:
                try:
                    check_current(self.workflow)
                    self.action()
                except CellStop as exc:
                    self.stopped = exc
                    card = _card("error", STOPPED, (_verbatim(str(exc)),), raw=True)
                    self.workflow._show(card)
                except BaseException as exc:
                    unexpected = exc
                    raise
        finally:
            self.button.disabled = False
            self.workflow._after_step()
        if reraise and unexpected is not None:
            raise unexpected


class CellposeNotebook(CellposeWorkflow):
    """:class:`.CellposeWorkflow` with a form per step; each cell calls one ``*_step`` method.

    ``forms`` holds each step's :class:`StepForm` by name (``open``, ``channels``, ``model``,
    ``preview``, ``segment``) once its cell has shown it.
    """

    def __init__(self) -> None:
        """Start with nothing chosen and no form shown."""
        super().__init__()
        self.forms: dict[str, StepForm] = {}
        self._loaded_with: tuple[str, ...] | None = None
        self._nuclei_failed = False  # the last Load the model loaded the cells', not the nuclei's
        self.replaced = False
        self.load_on_preview = False  # segmentation_app: Preview and Segment load the model
        self._previewed_with: tuple[tuple[str, str], ...] | None = None  # the last Preview's
        self._summary: widgets.HTML | None = None
        self.on_change: list[Callable[[], object]] = []  # called after every step's button
        self.viewer = run_viewer(lambda: self.inputs)
        self.opening = widgets.HTML()  # the wait of Open the run (owner Colab test, #574)
        take_over(self)  # the forms of an earlier workflow in this kernel stop working

    def _keep(self, name: str, form: StepForm) -> StepForm:
        """Keep ``form`` as step ``name``, bring the forms in step and return it."""
        self.forms[name] = form
        self._after_step()
        return form

    # Each cell of the notebook mounts one step; segmentation_app mounts them all at once.

    def open_step(self) -> None:
        """Show cell 3's form: the run's output folder and the masks folder."""
        display(self.open_form().widget)

    def channels_step(self) -> None:
        """Show cell 4's form: the output, the mask type and the channels."""
        display(self.channels_form().widget)

    def model_step(self) -> None:
        """Show cell 5's form: the model, the device and the settings."""
        display(self.model_form().widget)

    def preview_step(self) -> None:
        """Show cell 6's form: the example result."""
        display(self.preview_form().widget)

    def segment_step(self) -> None:
        """Show cell 7's form: segment the whole experiment."""
        display(self.segment_form().widget)

    def open_form(self) -> StepForm:
        """Show the form of cell 3: the run's output folder and the masks folder.

        Under the button, once the run is open, its images in the preprocessing viewer
        (:mod:`.notebook_cellpose_view`).
        """
        status = widgets.HTML()
        run = text_field(placeholder="e.g. /content/drive/MyDrive/run_output")
        masks = text_field(placeholder="e.g. /content/drive/MyDrive/masks")
        cards = [
            ui.card(
                "Folders",
                [
                    field_rows(
                        [
                            ("Run folder", [path_row(run, status)]),
                            ("Masks folder", [path_row(masks, status)]),
                        ]
                    ),
                    full_help(
                        "The run folder is the preprocessing run's <b>output folder</b> (it holds "
                        "<b>run_manifest.json</b>). The masks folder is where the masks are "
                        "written: not the run folder or the experiment, and not inside either."
                    ),
                    status,
                ],
            ),
        ]
        form = StepForm(
            self,
            "Open the preprocessing run",
            "Open the run with preprocessing's own checks and list its channels.",
            cards,
            "Open the run",
            "folder-open",
            lambda: self._open_and_show(ui.clean_path(run.value), ui.clean_path(masks.value)),
            {"run_folder": run, "masks_folder": masks},
        )
        form.body.children = [*form.body.children, self.opening, self.viewer.widget]
        return self._keep("open", form)

    def _open_and_show(self, run_folder: str, masks_folder: str) -> None:
        """Open the run, then show its images (or none, when it was refused), with the wait."""
        with opening(self.opening) as checking:
            try:
                self.open(run_folder, masks_folder, checking=checking)
            finally:
                checking("viewer", 0, 0, "")
                self.viewer.set_entries(run_entries(self.inputs))

    def channels_form(self) -> StepForm:
        """Show the form of cell 4: the output and the mask type(s); the channels come after it.

        The mask type follows the output (``Cells`` or ``Nuclei``) until the user types another;
        it is taken once typed (Enter, or leaving the box), not at each keystroke, and so is the
        nuclei mask type of Both. Changing any of them to other than what the channels were
        chosen with drops that choice (and the loaded models), so nothing runs with an output or
        mask type the form no longer shows.
        """
        fields, changed = output_fields(OUTPUT_NAMES), widgets.HTML()
        output, mask_type, nuclei_type = fields.values()
        for box in (mask_type, nuclei_type):  # taken on Enter or leaving the box, not per key
            box.continuous_update = False

        def drop(_: object) -> None:
            self._drop_choice(fields, changed)

        for field in fields.values():
            field.observe(drop, names="value")

        def choose() -> None:
            changed.value = ""
            self.choose_channels(mask_type.value, output.value, nuclei_type.value)

        form = StepForm(
            self,
            "Choose the output and the channels",
            "Choose a cell channel, a nuclear channel or both, <b>by channel ID</b>.",
            output_cards(fields, changed),
            "Choose the channels",
            "list",
            choose,
            fields,
        )
        return self._keep("channels", form)

    def _drop_choice(self, fields: dict[str, widgets.Widget], changed: widgets.HTML) -> None:
        """Drop the channels and the model(s) when cell 4 now shows other mask types or output.

        For Both the two mask types are compared; a mask type committed again unchanged drops
        nothing.
        """
        output, mask_type, nuclei_type = (field.value for field in fields.values())
        shown = (output_mask_types(output, mask_type, nuclei_type), output)
        if self.picker is not None and (self.mask_types, self.picker.output) != shown:
            self.drop_choice()
            self._after_step()
            then = "load the model"
            if self.load_on_preview:  # no Load button: Preview loads it
                then = "press Preview (it loads the model)"
            changed.value = ui.status(
                "warn", f"Changed: press Choose the channels again, then {then}."
            )

    def drop_choice(self) -> None:
        """Forget the channels, the mask types and every loaded model (the choice changed)."""
        super().drop_choice()
        self._loaded_with, self._nuclei_failed = None, False
        self._previewed_with = None  # a preview of other channels says nothing of these

    def model_form(self) -> StepForm:
        """Show the form of cell 5: the model, the device, the settings; the button loads."""
        fields, status = model_fields(), widgets.HTML()
        found, line = cp.device_status()  # seen before loading; it chooses nothing
        device = widgets.HTML(ui.status("ok" if found else "warn", "Device", [line]))
        cards = model_cards(fields, status)
        self._nuclei_box = cards[-1]
        fields[SEPARATE_NUCLEI].observe(lambda _: self._show_nuclei_box(), names="value")
        for field in fields.values():  # display only: the summary and the strip, nothing kept
            field.observe(lambda _: self._model_fields_changed(), names="value")
        form = StepForm(
            self,
            "Choose the model and the settings, then load the model",
            "Load the model you choose, on the device you choose, and check it.",
            [device, *cards],
            "Load the model",
            "download",
            lambda: self._load_from(fields),
            fields,
        )
        return self._keep("model", form)

    def _load_from(self, fields: dict[str, widgets.Widget]) -> None:
        """Check the settings, then load the model(s), with the fields' values (cell 5).

        The models loaded before are dropped first: a refused load leaves Preview and Segment
        disabled, never active on a model the form no longer shows. A nuclei model that does
        not load drops the cells' model just loaded too, and a press then says so.
        """
        self.loaded = self.nuclei_loaded = self._loaded_with = None
        self._nuclei_failed = False
        value = {name: field.value for name, field in fields.items()}
        self._set_form_settings(fields)
        self.load(
            value["model"],
            value["custom_model_file"],
            value["device"],
            value["download_weights"],
            value["install_dinov3"],
        )
        if value[SEPARATE_NUCLEI]:
            try:
                self.load_nuclei(
                    value["nuclei_model"],
                    value["nuclei_custom_model_file"],
                    value["device"],
                    value["download_weights"],
                    value["install_dinov3"],
                )
            except BaseException:  # a refusal, any error or an interrupt: neither stays loaded
                self.loaded = self.nuclei_loaded = None
                self._nuclei_failed = True
                raise
        else:
            self.same_model_for_nuclei()
        self._loaded_with = model_choice(fields)

    def _set_form_settings(self, fields: dict[str, widgets.Widget]) -> None:
        """Check the settings cell 5's form shows, and the nuclei's own when ticked."""
        value = {name: field.value for name, field in fields.items()}
        steps = [("", self.set_settings)]
        if value[SEPARATE_NUCLEI]:
            steps.append(("nuclei_", self.set_nuclei_settings))
        for prefix, step in steps:
            step(
                value[f"{prefix}diameter"],
                value[f"{prefix}flow_threshold"],
                value[f"{prefix}cellprob_threshold"],
                value[f"{prefix}batch_size"],
            )

    def preview_form(self) -> StepForm:
        """Show the form of cell 6: a dropdown of the run's images and **Preview**.

        As in ZeroCostDL4Mic: choose an image, see Cellpose's result for it, choose another or
        change the settings in cell 5 and press **Preview** again. The model is not reloaded
        unless the model or the device changed. Nothing is written. The button is active
        once a model is loaded.
        """
        image = widgets.Dropdown(options=[], description="Image", layout=FIELD, style=STYLE)
        cards = [
            ui.card(
                "Example image",
                [
                    image,
                    ui.help_text(
                        "Every image of the run, by its path in the run; it starts on the first "
                        "image without a mask. Nothing is written."
                    ),
                ],
            )
        ]
        form = StepForm(
            self,
            "Preview one example result",
            "Segment one image with the loaded model and the settings of the Load the model "
            "step, and show "
            "Cellpose's result; nothing is written.",
            cards,
            "Preview",
            "eye",
            lambda: self._with_form_settings(lambda: self._preview_and_note(image.value or "")),
            {"preview_image": image},
        )
        return self._keep("preview", form)

    def segment_form(self) -> StepForm:
        """Show the form of cell 7: overwrite (off by default) and the button that segments."""
        overwrite = widgets.Checkbox(value=False, description="Replace masks that already exist")
        self._summary = summary_widget()
        cards = [
            self._summary,
            ui.card(
                "Existing masks",
                [
                    overwrite,
                    ui.help_text(
                        "Tick to replace masks that already exist. Otherwise images that "
                        "already have a mask are skipped."
                    ),
                ],
            ),
        ]
        form = StepForm(
            self,
            "Segment the whole experiment",
            "Segment every image of the run, write the masks and the record, and list every "
            "image. The button is active once a model is loaded.",
            cards,
            "Segment the whole experiment",
            "play",
            lambda: self._with_form_settings(lambda: self._segment(overwrite.value)),
            {"overwrite": overwrite},
        )
        return self._keep("segment", form)

    def _preview_and_note(self, image: str) -> None:
        """Preview ``image``, then keep what it used, for the Segment step's line."""
        self.preview(image)
        model = self.forms.get("model")
        self._previewed_with = None if model is None else used(model.fields)
        self._refresh_summary()

    def _model_fields_changed(self) -> None:
        """Update what shows the model form's values: Segment's line and the step strip."""
        self._refresh_summary()
        for callback in self.on_change:
            callback()

    def _refresh_summary(self) -> None:
        """Write the Segment step's line of what it will use (and what differs from Preview)."""
        model = self.forms.get("model")
        if model is None or getattr(self, "_summary", None) is None:
            return
        self._summary.value = summary_html(used(model.fields), self._previewed_with)

    def _segment(self, overwrite: bool) -> None:
        """Segment the whole experiment; each batch goes to :attr:`on_masks_written`."""
        self.run(overwrite)

    def _with_form_settings(self, step: Callable[[], object]) -> None:
        """Run ``step`` with the settings cell 5's form shows now, on the model as loaded.

        The settings are checked again (as **Load the model** does); a model, model file or
        device changed since loading is refused: the model is never reloaded silently. So are
        run and masks folders in cell 3 other than those opened (``RUN_CHANGED``).
        """
        check_folders(self)
        model = self.forms.get("model")
        if self.load_on_preview and model is not None:
            self._load_if_needed(model.fields)
            step()
            return
        if self._nuclei_failed:  # the cells' model loaded, the nuclei's did not: both dropped
            raise CellStop("The nuclei model did not load: press Load the model again.")
        if model is not None and self.loaded is not None:
            if model_choice(model.fields) != self._loaded_with:
                raise CellStop(MODEL_CHANGED)
            self._set_form_settings(model.fields)
        step()

    def _load_if_needed(self, fields: dict[str, widgets.Widget]) -> None:
        """Load the model(s) the form shows unless exactly those are loaded (``segmentation_app``).

        The model is loaded at the first Preview (or Segment), then reused: another image, or
        another diameter or threshold, never reloads it. It is loaded again only when what
        loading depends on changed (:func:`.notebook_cellpose_fields.model_choice`: the model,
        its file, the device, the nuclei's own model), or when the last load did not finish.
        The device is the one chosen; nothing falls back or is substituted (``load_model``).
        """
        if self.model_is_current():
            self._set_form_settings(fields)
            return
        self._load_from(fields)

    def model_is_current(self) -> bool:
        """Return True when the model loaded is the one the model form shows now (file included)."""
        model = self.forms.get("model")
        if self.loaded is None or self._nuclei_failed or model is None:
            return False
        return model_choice(model.fields) == self._loaded_with

    def _after_step(self) -> None:
        """Keep the later forms in step: the image list, and the buttons that need a model.

        With :attr:`load_on_preview` Preview and Segment need the channels only: they load the
        model themselves.
        """
        if self.replaced:
            for form in self.forms.values():
                form.button.disabled = True
            return
        ready = self.picker is not None if self.load_on_preview else self.loaded is not None
        for name in ("preview", "segment"):
            if name in self.forms:
                self.forms[name].button.disabled = not ready
        if "preview" in self.forms:
            self._fill_images(self.forms["preview"].fields["preview_image"])
        self._show_nuclei_box()
        self._refresh_summary()
        for callback in self.on_change:
            callback()

    def _show_nuclei_box(self) -> None:
        """Show the nuclei's own model and settings for the output Both (or while ticked)."""
        model = self.forms.get("model")
        if model is not None:
            both = self.picker is not None and self.picker.output == cp.BOTH_OUTPUT
            show_nuclei_box(self._nuclei_box, model.fields, both)

    def _fill_images(self, dropdown: widgets.Dropdown) -> None:
        """List the run's images in the preview's dropdown, on the first without a mask."""
        paths = [f.relative_path for f in self.inputs.fovs()] if self.inputs else []
        if list(dropdown.options) == paths:
            return
        first = paths[0] if paths else None
        if paths and self.mask_types:
            todo = self.first_without_mask()
            first = todo.relative_path if todo else first
        dropdown.options = paths
        dropdown.value = first
