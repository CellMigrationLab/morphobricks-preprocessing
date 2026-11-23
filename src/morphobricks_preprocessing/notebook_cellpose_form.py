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
from .notebook_cellpose import CellposeWorkflow, CellStop, _card, _verbatim
from .segmentation_cellpose_model import BUILTIN_MODELS

DEVICE_OPTIONS = ("cuda", "mps", "cpu")
"""The devices offered, in the former form's order; the default is the first (never chosen)."""
STOPPED = "This step stopped"
"""The title of the card that shows a refusal; its message is the refusal, unchanged."""
_FIELD = widgets.Layout(width="99%")
_STYLE = {"description_width": "11em"}


def _text(value: str = "", placeholder: str = "", description: str = "") -> widgets.Text:
    """Return a full-width text field."""
    return widgets.Text(
        value=value,
        placeholder=placeholder,
        description=description,
        layout=widgets.Layout(flex="1 1 auto", width="auto"),
        style=_STYLE,
    )


def _browse(target: widgets.Text, status: widgets.HTML, *, folder: bool = True) -> widgets.Button:
    """Return a **Browse…** button that fills ``target`` from the system's dialog.

    As in the Preprocessing app: where no dialog can open (Google Colab, a server), the amber
    card says how to paste the path instead.
    """
    kind = "folder" if folder else "file"
    browse = ui.button("Browse…", icon="folder-open", tooltip=f"Choose the {kind} in a dialog")

    def chosen(_: object) -> None:
        start = ui.clean_path(target.value)
        path = ui.ask_directory(start) if folder else ui.ask_open_file(start)
        if path is None:
            status.value = ui.no_dialog_status(kind, f"the {kind}")
        elif path:
            status.value, target.value = "", path

    browse.on_click(chosen)
    return browse


def _path_row(field: widgets.Text, status: widgets.HTML, *, folder: bool = True) -> widgets.HBox:
    """Return a text field with its **Browse…** button."""
    return widgets.HBox([field, _browse(field, status, folder=folder)], layout=_FIELD)


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
        self.output = widgets.Output(layout=_FIELD)
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


class CellposeNotebook(CellposeWorkflow):
    """:class:`.CellposeWorkflow` with a form per step; each cell calls one ``*_step`` method.

    ``forms`` holds each step's :class:`StepForm` by name (``open``, ``channels``, ``model``,
    ``preview``, ``segment``, ``next``) once its cell has shown it.
    """

    def __init__(self) -> None:
        """Start with nothing chosen and no form shown."""
        super().__init__()
        self.forms: dict[str, StepForm] = {}

    def _form(self, name: str, form: StepForm) -> None:
        """Keep ``form`` as step ``name`` and display it."""
        self.forms[name] = form
        display(form.widget)

    def open_step(self) -> None:
        """Show the form of cell 3: the run's output folder and the masks folder."""
        status = widgets.HTML()
        run = _text(placeholder="e.g. /content/drive/MyDrive/run_output")
        masks = _text(placeholder="e.g. /content/drive/MyDrive/masks")
        cards = [
            ui.card(
                "Run folder",
                [
                    _path_row(run, status),
                    ui.help_text(
                        "The run's <b>output folder</b>: the folder that holds "
                        "<b>run_manifest.json</b>. Paste the path or use <b>Browse…</b>."
                    ),
                ],
            ),
            ui.card(
                "Masks folder",
                [
                    _path_row(masks, status),
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
        self._form("open", form)

    def channels_step(self) -> None:
        """Show the form of cell 4: the mask type; the channel dropdowns come after it."""
        mask_type = _text("Cells", "e.g. Cells or Nuclei")
        cards = [
            ui.card(
                "Mask type",
                [
                    mask_type,
                    ui.help_text(
                        "The mask type to make: one folder name, for example <b>Cells</b> or "
                        "<b>Nuclei</b>. The channel dropdowns appear below once it is checked."
                    ),
                ],
            )
        ]
        form = StepForm(
            self,
            "4 · Choose the mask type and the channels",
            "Choose one to three channels <b>by channel ID</b>, in the order Cellpose gets them.",
            cards,
            "Choose the channels",
            "list",
            lambda: self.choose_channels(mask_type.value),
            {"mask_type": mask_type},
        )
        self._form("channels", form)

    def model_step(self) -> None:
        """Show the form of cell 5: the model, the device, the settings; the button loads."""
        fields, status = _model_fields(), widgets.HTML()
        form = StepForm(
            self,
            "5 · Choose the model and the settings, then load the model",
            "Load the model you choose, on the device you choose, and check it.",
            _model_cards(fields, status),
            "Load the model",
            "download",
            lambda: self._load_from(fields),
            fields,
        )
        self._form("model", form)

    def _load_from(self, fields: dict[str, widgets.Widget]) -> None:
        """Check the settings, then load the model, with the fields' values (cell 5)."""
        value = {name: field.value for name, field in fields.items()}
        self.set_settings(
            value["diameter"],
            value["flow_threshold"],
            value["cellprob_threshold"],
            value["batch_size"],
        )
        self.load(
            value["model"],
            value["custom_model_file"],
            value["device"],
            value["download_weights"],
            value["install_dinov3"],
        )

    def preview_step(self) -> None:
        """Show the form of cell 6: the image to preview (blank = the first without a mask)."""
        image = _text(placeholder="blank = the first image without a mask")
        cards = [
            ui.card(
                "Image to preview",
                [
                    image,
                    ui.help_text(
                        "The image's path in the run, as the run lists it (for example "
                        "<b>control/rep1/image.tif</b>). Blank: the first image without a mask. "
                        "Nothing is written."
                    ),
                ],
            )
        ]
        form = StepForm(
            self,
            "6 · Preview one image",
            "Segment one image and show its outlines; nothing is written.",
            cards,
            "Preview",
            "eye",
            lambda: self.preview(image.value),
            {"preview_image": image},
        )
        self._form("preview", form)

    def segment_step(self) -> None:
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
            "7 · Segment all images",
            "Segment every image, write the masks and the record, and list every image.",
            cards,
            "Segment all images",
            "play",
            lambda: self.run(overwrite.value),
            {"overwrite": overwrite},
        )
        self._form("segment", form)

    def next_step(self) -> None:
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
        self._form("next", form)


def _model_fields() -> dict[str, widgets.Widget]:
    """Return cell 5's fields by setting name, with the former form's defaults."""
    return {
        "model": widgets.Dropdown(
            options=list(BUILTIN_MODELS), value=BUILTIN_MODELS[0], description="Model", style=_STYLE
        ),
        "custom_model_file": _text(placeholder="blank = the model above"),
        "device": widgets.Dropdown(
            options=list(DEVICE_OPTIONS),
            value=DEVICE_OPTIONS[0],
            description="Device",
            style=_STYLE,
        ),
        "download_weights": widgets.Checkbox(
            value=False, description="Allow the weights download from Hugging Face"
        ),
        "install_dinov3": widgets.Checkbox(value=False, description="Install DINOv3 now"),
        "diameter": _text(placeholder="blank = native scale", description="Diameter (pixels)"),
        "flow_threshold": widgets.FloatText(value=0.4, description="Flow threshold", style=_STYLE),
        "cellprob_threshold": widgets.FloatText(
            value=0.0, description="Cell probability threshold", style=_STYLE
        ),
        "batch_size": widgets.IntText(value=8, description="Batch size", style=_STYLE),
    }


def _model_cards(fields: dict[str, widgets.Widget], status: widgets.HTML) -> list[widgets.Widget]:
    """Return cell 5's cards: the model and device, the downloads, the settings.

    ``status`` shows, under the model file, how to paste its path where no dialog opens.
    """
    custom = fields["custom_model_file"]
    return [
        ui.card(
            "Model and device",
            [
                fields["model"],
                _path_row(custom, status, folder=False),
                ui.help_text(
                    "A model file of your own (leave blank to use the model above). A file that "
                    "does not exist is refused."
                ),
                status,
                fields["device"],
                ui.help_text(
                    "The device is never chosen for you: a GPU that is not available is "
                    "refused, not replaced by the CPU."
                ),
            ],
        ),
        ui.card(
            "Downloads and installs",
            [
                fields["download_weights"],
                ui.help_text(
                    "Built-in weights are downloaded from Hugging Face on first use. Tick to "
                    "allow that download; nothing downloads without it."
                ),
                fields["install_dinov3"],
                ui.help_text(
                    "The cpdino models need DINOv3 (installed at a pinned commit). Tick to "
                    "install it now; then <b>restart the runtime</b> and run cells 1 to 4 again."
                ),
            ],
        ),
        ui.card(
            "Settings",
            [
                fields["diameter"],
                ui.help_text(
                    "Diameter in pixels. Blank = the model's native scale (Cellpose 4.2.1.1 "
                    "does not estimate it). Never 0."
                ),
                fields["flow_threshold"],
                fields["cellprob_threshold"],
                ui.help_text(
                    "Normalisation is always Cellpose's own, per image: each channel to its "
                    "1st-99th percentile. Images are never sent to Cellpose unnormalised."
                ),
                fields["batch_size"],
            ],
        ),
    ]
