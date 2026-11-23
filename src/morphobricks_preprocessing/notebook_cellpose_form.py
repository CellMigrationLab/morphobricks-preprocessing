"""The Cellpose notebook's settings as widgets: one form per step, in every notebook environment.

``MorphoBricks_Cellpose.ipynb`` used Colab form fields (``# @param``), which are plain comments
in JupyterLab and LabConstrictor, so a user there had to edit Python. Each step's cell now
shows a form (:class:`StepForm`) built from the Preprocessing app's helpers in
:mod:`.notebook_style` (``page``, ``card``, ``help_text``, ``button``, ``clean_path``,
``ask_directory``, ``ask_open_file``, ``no_dialog_status``): the fields, one main button, and
under it what the step shows. The button calls the same :class:`.CellposeWorkflow` method a
cell called before, with the fields' values exactly as given; a refusal that stopped the cell
(:class:`.CellStop`) is shown with the same message, verbatim, in a red card.

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
from .notebook_cellpose import CellposeWorkflow, CellStop
from .notebook_cellpose_channels import OUTPUT_NAMES, _card, _verbatim
from .notebook_cellpose_fields import (
    ADVANCED,
    DEVICE_OPTIONS,
    FIELD,
    SEPARATE_NUCLEI,
    STYLE,
    model_cards,
    model_choice,
    model_fields,
    output_cards,
    output_fields,
    path_row,
    text_field,
)

__all__ = [
    "ADVANCED",
    "DEVICE_OPTIONS",
    "MODEL_CHANGED",
    "STOPPED",
    "CellposeNotebook",
    "StepForm",
]

STOPPED = "This step stopped"
MODEL_CHANGED = (
    "The model, the model file or the device in cell 5 (or the nuclei's own model) changed "
    "since the model was loaded: "
    "press Load the model again. A model is never reloaded without that."
)
"""The refusal of a preview or a run whose model choice no longer matches the loaded model."""
"""The title of the card that shows a refusal; its message is the refusal, unchanged."""


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
        self.widget = ui.page(title, purpose, [*cards, self.button, self.output])
        self.button.on_click(lambda _: self.submit())

    @property
    def refusal(self) -> str:
        """The message of the last refusal, or ``""``."""
        return "" if self.stopped is None else str(self.stopped)

    def submit(self) -> None:
        """Run the step with the fields as they are now; show a refusal, unchanged, in a card.

        Any other error is left to the output area, which shows it with its traceback.
        """
        self.stopped = None
        self.output.clear_output()
        self.button.disabled = True
        try:
            with self.output:
                try:
                    self.action()
                except CellStop as exc:
                    self.stopped = exc
                    card = _card("error", STOPPED, (_verbatim(str(exc)),), raw=True)
                    self.workflow._show(card)
        finally:
            self.button.disabled = False
            self.workflow._after_step()


class CellposeNotebook(CellposeWorkflow):
    """:class:`.CellposeWorkflow` with a form per step; each cell calls one ``*_step`` method.

    ``forms`` holds each step's :class:`StepForm` by name (``open``, ``channels``, ``model``,
    ``preview``, ``segment``, ``next``) once its cell has shown it.
    """

    def __init__(self) -> None:
        """Start with nothing chosen and no form shown."""
        super().__init__()
        self.forms: dict[str, StepForm] = {}
        self._loaded_with: tuple[str, ...] | None = None

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

    def next_step(self) -> None:
        """Show the closing cell's form: what to do next."""
        display(self.next_form().widget)

    def open_form(self) -> StepForm:
        """Show the form of cell 3: the run's output folder and the masks folder."""
        status = widgets.HTML()
        run = text_field(placeholder="e.g. /content/drive/MyDrive/run_output")
        masks = text_field(placeholder="e.g. /content/drive/MyDrive/masks")
        cards = [
            ui.card(
                "Run folder",
                [
                    path_row(run, status),
                    ui.help_text(
                        "The run's <b>output folder</b>: the folder that holds "
                        "<b>run_manifest.json</b>. Paste the path or use <b>Browse…</b>."
                    ),
                ],
            ),
            ui.card(
                "Masks folder",
                [
                    path_row(masks, status),
                    ui.help_text(
                        "Where the masks are written: <b>outside</b> the experiment folder and "
                        "the run's output folder."
                    ),
                    status,
                ],
            ),
        ]
        form = StepForm(
            self,
            "3 · Open the preprocessing run",
            "Open the run with preprocessing's own checks and list its channels.",
            cards,
            "Open the run",
            "folder-open",
            lambda: self.open(run.value, masks.value),
            {"run_folder": run, "masks_folder": masks},
        )
        return self._keep("open", form)

    def channels_form(self) -> StepForm:
        """Show the form of cell 4: the output and the mask type; the channels come after it.

        The mask type follows the output (``Cells`` or ``Nuclei``) until the user types another.
        Changing either after the channels were chosen drops that choice (and a loaded model),
        so nothing runs with an output or mask type the form no longer shows.
        """
        fields, changed = output_fields(OUTPUT_NAMES), widgets.HTML()
        output, mask_type, nuclei_type = fields.values()

        def drop(_: object) -> None:
            if self.picker is not None:
                self.drop_choice()
                self._after_step()
                changed.value = ui.status(
                    "warn", "Changed: press Choose the channels again, then load the model."
                )

        for field in fields.values():
            field.observe(drop, names="value")

        def choose() -> None:
            changed.value = ""
            self.choose_channels(mask_type.value, output.value, nuclei_type.value)

        form = StepForm(
            self,
            "4 · Choose the output and the channels",
            "Choose a cell channel, a nuclear channel or both, <b>by channel ID</b>.",
            output_cards(fields, changed),
            "Choose the channels",
            "list",
            choose,
            fields,
        )
        return self._keep("channels", form)

    def model_form(self) -> StepForm:
        """Show the form of cell 5: the model, the device, the settings; the button loads."""
        fields, status = model_fields(), widgets.HTML()
        found, line = cp.device_status()  # seen before loading; it chooses nothing
        device = widgets.HTML(ui.status("ok" if found else "warn", "Device", [line]))
        form = StepForm(
            self,
            "5 · Choose the model and the settings, then load the model",
            "Load the model you choose, on the device you choose, and check it.",
            [device, *model_cards(fields, status)],
            "Load the model",
            "download",
            lambda: self._load_from(fields),
            fields,
        )
        return self._keep("model", form)

    def _load_from(self, fields: dict[str, widgets.Widget]) -> None:
        """Check the settings, then load the model, with the fields' values (cell 5)."""
        value = {name: field.value for name, field in fields.items()}
        self._set_form_settings(fields)
        self._loaded_with = None
        self.load(
            value["model"],
            value["custom_model_file"],
            value["device"],
            value["download_weights"],
            value["install_dinov3"],
        )
        if value[SEPARATE_NUCLEI]:
            self.load_nuclei(
                value["nuclei_model"],
                value["nuclei_custom_model_file"],
                value["device"],
                value["download_weights"],
                value["install_dinov3"],
            )
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
            "6 · Preview one example result",
            "Segment one image with the loaded model and the settings of cell 5, and show "
            "Cellpose's result; nothing is written.",
            cards,
            "Preview",
            "eye",
            lambda: self._with_form_settings(lambda: self.preview(image.value or "")),
            {"preview_image": image},
        )
        return self._keep("preview", form)

    def segment_form(self) -> StepForm:
        """Show the form of cell 7: overwrite (off by default) and the button that segments."""
        overwrite = widgets.Checkbox(value=False, description="Replace masks that already exist")
        cards = [
            ui.card(
                "Existing masks",
                [
                    overwrite,
                    ui.help_text(
                        "Tick to replace masks that already exist. Otherwise images that "
                        "already have a mask are skipped."
                    ),
                ],
            )
        ]
        form = StepForm(
            self,
            "7 · Segment the whole experiment",
            "Segment every image of the run, write the masks and the record, and list every "
            "image. The button is active once a model is loaded.",
            cards,
            "Segment the whole experiment",
            "play",
            lambda: self._with_form_settings(lambda: self._segment(overwrite.value)),
            {"overwrite": overwrite},
        )
        return self._keep("segment", form)

    def _segment(self, overwrite: bool) -> None:
        """Segment the whole experiment; each batch goes to :attr:`on_masks_written`."""
        self.run(overwrite)

    def _with_form_settings(self, step: Callable[[], object]) -> None:
        """Run ``step`` with the settings cell 5's form shows now, on the model as loaded.

        The settings are checked again (as **Load the model** does); a model, model file or
        device changed since loading is refused: the model is never reloaded silently.
        """
        model = self.forms.get("model")
        if model is not None and self.loaded is not None:
            if model_choice(model.fields) != self._loaded_with:
                raise CellStop(MODEL_CHANGED)
            self._set_form_settings(model.fields)
        step()

    def _after_step(self) -> None:
        """Keep the later forms in step: the image list, and the buttons that need a model."""
        for name in ("preview", "segment"):
            if name in self.forms:
                self.forms[name].button.disabled = self.loaded is None
        if "preview" in self.forms:
            self._fill_images(self.forms["preview"].fields["preview_image"])

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

    def next_form(self) -> StepForm:
        """Show the closing cell's button: what to do now that the masks are made."""
        form = StepForm(
            self,
            "8 · What to do next",
            "Declare the new mask type, then run preprocessing again with the masks folder.",
            [],
            "Show the next steps",
            "arrow-right",
            self.next_steps,
            {},
        )
        return self._keep("next", form)
