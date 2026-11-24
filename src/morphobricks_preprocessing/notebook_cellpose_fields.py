"""The fields of the Cellpose notebook's forms: paths with **Browse…**, the model and settings.

Split from :mod:`.notebook_cellpose_form` to keep each file small; built from the Preprocessing
app's helpers in :mod:`.notebook_style`. The defaults are those of the former Colab form:
the weights download and the DINOv3 installation off, the device ``cuda`` (an unavailable GPU
is refused, never replaced by the CPU), the model ``cpsam_v2`` and a blank diameter.

The output Both (#440) segments the cells and the nuclei with **one model and one set of
settings** by default. Under **Advanced Cellpose settings**, a tick gives the nuclei a model
and settings of their own (:data:`NUCLEI_FIELDS`); the device and the download and install
ticks stay shared.
"""

from __future__ import annotations

import ipywidgets as widgets

from . import notebook_style as ui
from . import segmentation_cellpose as cp
from .segmentation_cellpose_model import BUILTIN_MODELS

DEVICE_OPTIONS = ("cuda", "mps", "cpu")
"""The devices offered, in the former form's order; the default is the first (never chosen)."""
ADVANCED = "Advanced Cellpose settings"
"""The collapsed section of cell 5 with the diameter, the thresholds and the batch size."""
SEPARATE_NUCLEI = "separate_nuclei"
"""The tick that gives the nuclei of the output Both a model and settings of their own."""
NUCLEI_FIELDS = (
    "model",
    "custom_model_file",
    "diameter",
    "flow_threshold",
    "cellprob_threshold",
    "batch_size",
)
"""The fields the nuclei may have of their own, each named ``nuclei_<field>`` in the form."""
FIELD = widgets.Layout(width="99%")
STYLE = {"description_width": "11em"}


def text_field(value: str = "", placeholder: str = "", description: str = "") -> widgets.Text:
    """Return a full-width text field."""
    return widgets.Text(
        value=value,
        placeholder=placeholder,
        description=description,
        layout=widgets.Layout(flex="1 1 auto", width="auto"),
        style=STYLE,
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


def path_row(field: widgets.Text, status: widgets.HTML, *, folder: bool = True) -> widgets.HBox:
    """Return a text field with its **Browse…** button."""
    return widgets.HBox([field, _browse(field, status, folder=folder)], layout=FIELD)


def output_fields(names: dict[str, str]) -> dict[str, widgets.Widget]:
    """Return cell 4's fields: the output (``names`` by key) and the mask type(s).

    The mask type follows the output (``Cells`` or ``Nuclei``) until the user types another.
    For Both (#440) it names the cell masks and a second field, shown then, the nuclei masks.
    """
    output = widgets.RadioButtons(
        options=[(name, key) for key, name in names.items()],
        value=cp.CELL_OUTPUT,
        description="Output",
        style=STYLE,
    )
    mask_type = text_field(
        cp.OUTPUT_MASK_TYPES[cp.CELL_OUTPUT], "e.g. Cells or Nuclei", "Mask type"
    )
    nuclei_type = text_field(
        cp.OUTPUT_MASK_TYPES[cp.NUCLEI_OUTPUT], "e.g. Nuclei", "Nuclei mask type"
    )
    nuclei_type.layout.display = "none"  # shown for the output Both

    def follow(change: dict) -> None:
        both = change["new"] == cp.BOTH_OUTPUT
        if mask_type.value in ("", *cp.OUTPUT_MASK_TYPES.values()):
            mask_type.value = cp.OUTPUT_MASK_TYPES[cp.CELL_OUTPUT if both else change["new"]]
        mask_type.description = "Cell mask type" if both else "Mask type"
        nuclei_type.layout.display = "" if both else "none"

    output.observe(follow, names="value")
    return {"output": output, "mask_type": mask_type, "nuclei_mask_type": nuclei_type}


def output_cards(fields: dict[str, widgets.Widget], changed: widgets.HTML) -> list[widgets.Widget]:
    """Return cell 4's cards: the output, then the mask type(s) with ``changed`` under them."""
    return [
        ui.card(
            "Output",
            [
                fields["output"],
                ui.help_text(
                    "<b>Cell mask</b>: Cellpose reads the cell channel, then the nuclear "
                    "channel if one is chosen. <b>Nuclei mask</b>: it reads the nuclear "
                    "channel only. <b>Both</b>: each image is segmented twice, the cells "
                    "(cell and nuclear channels) and the nuclei (nuclear channel), as two "
                    "mask types; it needs both channels. Cells and nuclei are never paired "
                    "here."
                ),
            ],
        ),
        ui.card(
            "Mask type",
            [
                fields["mask_type"],
                fields["nuclei_mask_type"],
                ui.help_text(
                    "The mask type to make: one folder name. It follows the output "
                    "(<b>Cells</b> or <b>Nuclei</b>); type another if you like. For "
                    "<b>Both</b>, one name for the cell masks and one for the nuclei masks. "
                    "The channel dropdowns appear below once it is checked."
                ),
                changed,
            ],
        ),
    ]


def model_choice(fields: dict[str, widgets.Widget]) -> tuple[str, ...]:
    """Return the model, the model file and the device cell 5's form shows.

    With the separate nuclei model ticked, the nuclei model and its file follow: changing any
    of them, or the tick, after loading means loading again.
    """
    chosen = (
        fields["model"].value,
        fields["custom_model_file"].value.strip(),
        fields["device"].value,
    )
    if not fields[SEPARATE_NUCLEI].value:
        return chosen
    return (*chosen, fields["nuclei_model"].value, fields["nuclei_custom_model_file"].value.strip())


def _settings_fields(prefix: str = "") -> dict[str, widgets.Widget]:
    """Return a model, a model file and the settings, each named ``<prefix><setting>``."""
    fields = {
        "model": widgets.Dropdown(
            options=list(BUILTIN_MODELS), value=BUILTIN_MODELS[0], description="Model", style=STYLE
        ),
        "custom_model_file": text_field(placeholder="blank = the model above"),
        "diameter": text_field(placeholder="blank = native scale", description="Diameter (pixels)"),
        "flow_threshold": widgets.FloatText(value=0.4, description="Flow threshold", style=STYLE),
        "cellprob_threshold": widgets.FloatText(
            value=0.0, description="Cell probability threshold", style=STYLE
        ),
        "batch_size": widgets.IntText(value=8, description="Batch size", style=STYLE),
    }
    return {f"{prefix}{name}": field for name, field in fields.items()}


def model_fields() -> dict[str, widgets.Widget]:
    """Return cell 5's fields by setting name, with the former form's defaults.

    The nuclei's own model and settings (``nuclei_*``) start as the cells' defaults and are
    used only when :data:`SEPARATE_NUCLEI` is ticked, for the output Both.
    """
    main = _settings_fields()
    return {
        "model": main["model"],
        "custom_model_file": main["custom_model_file"],
        "device": widgets.Dropdown(
            options=list(DEVICE_OPTIONS),
            value=DEVICE_OPTIONS[0],
            description="Device",
            style=STYLE,
        ),
        "download_weights": widgets.Checkbox(
            value=False, description="Allow the weights download from Hugging Face"
        ),
        "install_dinov3": widgets.Checkbox(value=False, description="Install DINOv3 now"),
        **{name: main[name] for name in NUCLEI_FIELDS[2:]},
        SEPARATE_NUCLEI: widgets.Checkbox(
            value=False, description="Output Both: a different model or settings for the nuclei"
        ),
        **_settings_fields("nuclei_"),
    }


def _settings_box(fields: dict[str, widgets.Widget], prefix: str = "") -> list[widgets.Widget]:
    """Return the diameter, the thresholds and the batch size named ``<prefix><setting>``."""
    return [
        fields[f"{prefix}diameter"],
        ui.help_text(
            "Diameter in pixels. Blank = the model's native scale (Cellpose 4.2.1.1 does not "
            "estimate it). Never 0."
        ),
        fields[f"{prefix}flow_threshold"],
        fields[f"{prefix}cellprob_threshold"],
        fields[f"{prefix}batch_size"],
        ui.help_text(
            "Flow threshold 0.4, cell probability threshold 0 and batch size 8 are "
            "Cellpose's defaults."
        ),
    ]


def _nuclei_box(fields: dict[str, widgets.Widget], status: widgets.HTML) -> widgets.VBox:
    """Return the tick for the nuclei's own model and, shown once ticked, its fields."""
    own = widgets.VBox(
        [
            fields["nuclei_model"],
            path_row(fields["nuclei_custom_model_file"], status, folder=False),
            *_settings_box(fields, "nuclei_"),
        ],
        layout=widgets.Layout(display="none"),
    )

    def shown(change: dict) -> None:
        own.layout.display = "" if change["new"] else "none"

    fields[SEPARATE_NUCLEI].observe(shown, names="value")
    return widgets.VBox(
        [
            fields[SEPARATE_NUCLEI],
            ui.help_text(
                "Only for the output <b>Both</b>. By default the cells and the nuclei use the "
                "model and the settings above. Tick to give the nuclei their own (for example a "
                "smaller diameter); the device and the download and install ticks are shared."
            ),
            own,
        ]
    )


def model_cards(fields: dict[str, widgets.Widget], status: widgets.HTML) -> list[widgets.Widget]:
    """Return cell 5's cards: the model and device, the downloads, the settings.

    ``status`` shows, under a model file, how to paste its path where no dialog opens.
    """
    advanced = widgets.VBox([*_settings_box(fields), _nuclei_box(fields, status)])
    return [
        ui.card(
            "Model and device",
            [
                fields["model"],
                path_row(fields["custom_model_file"], status, folder=False),
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
                ui.help_text(
                    "Normalisation is always Cellpose's own, per image: each channel to its "
                    "1st-99th percentile. Images are never sent to Cellpose unnormalised."
                ),
                ui.accordion(ADVANCED, [advanced]),  # collapsed: the defaults suit most
            ],
        ),
    ]
