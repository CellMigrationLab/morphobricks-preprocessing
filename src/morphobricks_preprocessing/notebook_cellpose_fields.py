"""The fields of the Cellpose notebook's forms: paths with **Browse…**, the model and settings.

Split from :mod:`.notebook_cellpose_form` to keep each file small; built from the Preprocessing
app's helpers in :mod:`.notebook_style`. The defaults are those of the former Colab form:
the weights download and the DINOv3 installation off, the device ``cuda`` (an unavailable GPU
is refused, never replaced by the CPU), the model ``cpsam_v2`` and a blank diameter.

The output Both (#440) segments the cells and the nuclei with **one model and one set of
settings** by default. In the box **Output Both: the nuclei**, a tick gives the nuclei a model
and settings of their own (:data:`NUCLEI_FIELDS`); the device and the download and install
ticks stay shared.
"""

from __future__ import annotations

from pathlib import Path

import ipywidgets as widgets

from . import notebook_style as ui
from . import segmentation_cellpose as cp
from .segmentation_cellpose_model import BUILTIN_MODELS

DEVICE_OPTIONS = ("cuda", "mps", "cpu")
"""The devices offered, in the former form's order; the default is the first (never chosen)."""
ADVANCED = "Cellpose settings"
"""The card of cell 5 with the diameter, the thresholds and the batch size, each with its help."""
SETTING_HELP = {
    "diameter": (
        "The typical object size in pixels (blank = the model's native scale, never 0). "
        "Increase if objects are split, decrease if they are merged."
    ),
    "flow_threshold": (
        "Increase to keep more objects; decrease if you get badly shaped ones; "
        "0 keeps every object. Cellpose's default 0.4."
    ),
    "cellprob_threshold": (
        "Decrease to find more and larger objects, increase to find fewer and smaller; "
        "usually between -6 and 6. Cellpose's default 0."
    ),
    "batch_size": (
        "Tiles run at once on the device: lower it if the GPU runs out of memory, raise it "
        "for speed. Cellpose's default 8."
    ),
}
"""One line per setting: what it does and which way to move it (Cellpose 4.2.1.1's own
docstrings and GUI tooltips: ``CellposeModel.eval``, ``gui/guiparts.py``)."""
OUTPUT_HELP = (
    ("Cell mask", "one mask type (Cells): Cellpose reads the cell channel, plus the nuclear "
     "channel if you choose one."),
    ("Nuclei mask", "one mask type (Nuclei): Cellpose reads the nuclear channel only."),
    ("Both", "two mask types, Cells and Nuclei, each written on its own: the cells from the "
     "cell and nuclear channels, the nuclei from the nuclear channel. Both use the same "
     "model and settings unless, in step 3, you tick a different model or settings for the "
     "nuclei. Needs both channels."),
)  # fmt: skip
"""What each output writes and reads, shown in full next to the choice (owner, 25 Nov 2026)."""
HINT = "<div style='font-size:12px;color:#475569;line-height:1.35;margin:0 0 0 8px'>{}</div>"
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
        style={"description_width": "5em"},
        layout=widgets.Layout(width="auto", flex="0 0 auto"),
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
    """Return cell 4's card: the output with what each one writes, then the mask type(s).

    The explanation is plain text beside the choice, never folded or cut to one line.
    """
    lines = "".join(f"<li><b>{name}</b>: {text}</li>" for name, text in OUTPUT_HELP)
    explained = widgets.HTML(
        "<ul style='margin:2px 0 4px 0;padding-left:18px;font-size:12.5px;color:#334155;"
        f"line-height:1.4'>{lines}</ul>"
    )
    explained.add_class("mb-output-help")
    return [
        ui.card(
            "Output and mask type",
            [
                widgets.HBox(
                    [fields["output"], explained],
                    layout=widgets.Layout(width="99%", align_items="flex-start"),
                ),
                fields["mask_type"],
                fields["nuclei_mask_type"],
                full_help(
                    "The mask type is the masks' folder name; it follows the output, type "
                    "another if you like. Cells and nuclei are never paired here."
                ),
                changed,
            ],
        ),
    ]


def full_help(text: str) -> widgets.HTML:
    """Return a help line shown in full: never cut to one line by the stepper's style."""
    return widgets.HTML(
        f"<div class='mb-help' style='display:block;-webkit-line-clamp:unset'>{text}</div>"
    )


def setting_row(field: widgets.Widget, name: str) -> widgets.HBox:
    """Return a setting with its one-line help beside it, always shown."""
    field.layout.width, field.layout.flex = "24em", "0 0 auto"
    hint = widgets.HTML(HINT.format(SETTING_HELP[name]), layout=widgets.Layout(flex="1 1 auto"))
    hint.add_class("mb-setting-help")
    return widgets.HBox([field, hint], layout=widgets.Layout(width="99%", align_items="center"))


def model_choice(fields: dict[str, widgets.Widget]) -> tuple[str, ...]:
    """Return the model, the model file (and its size and time) and the device cell 5 shows.

    With the separate nuclei model ticked, the nuclei model and its file follow: changing any
    of them, or the tick, after loading means loading again.
    """
    own = fields["custom_model_file"].value.strip()
    chosen = (fields["model"].value, own, file_identity(own), fields["device"].value)
    if not fields[SEPARATE_NUCLEI].value:
        return chosen
    nuclei = fields["nuclei_custom_model_file"].value.strip()
    return (*chosen, fields["nuclei_model"].value, nuclei, file_identity(nuclei))


def file_identity(path: str) -> str:
    """Return a model file's size and modification time (ns) now, ``""`` without a file.

    Part of :func:`model_choice`: a model file overwritten at the same path is another model,
    so the one loaded is not reused (review of #548, M1). The file is hashed only when it is
    loaded (``load_model``); this is a ``stat``, cheap enough for every Preview.
    """
    if not path:
        return ""
    try:
        found = Path(path).expanduser().stat()
    except OSError:
        return "missing"  # load_model refuses it, with its own message
    return f"{found.st_size}:{found.st_mtime_ns}"


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
    return [setting_row(fields[f"{prefix}{name}"], name) for name in SETTING_HELP]


def _nuclei_box(fields: dict[str, widgets.Widget], status: widgets.HTML) -> widgets.VBox:
    """Return the tick for the nuclei's own model and, shown once ticked, its fields.

    The box is shown for the output Both (:func:`show_nuclei_box`), or while ticked.
    """
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
    box = ui.card(
        "Output Both: the nuclei",
        [
            full_help(
                "<b>Both</b> writes the cells and the nuclei. By default the nuclei use the "
                "model and the settings above. Tick to give the nuclei their own model and "
                "settings (for example a smaller diameter); the device and the download and "
                "install ticks are shared."
            ),
            fields[SEPARATE_NUCLEI],
            own,
        ],
    )
    box.add_class("mb-nuclei-box")
    return box


def show_nuclei_box(box: widgets.Widget, fields: dict[str, widgets.Widget], both: bool) -> None:
    """Show the nuclei's box for the output Both, and whenever its tick is on (never hidden)."""
    ui.set_visible(box, both or bool(fields[SEPARATE_NUCLEI].value))


def model_cards(fields: dict[str, widgets.Widget], status: widgets.HTML) -> list[widgets.Widget]:
    """Return cell 5's cards: the model, device and downloads; the settings; the nuclei's box.

    ``status`` shows, under a model file, how to paste its path where no dialog opens. The
    nuclei's box is the last card, hidden until the output is Both (:func:`show_nuclei_box`).
    """
    nuclei = _nuclei_box(fields, status)
    ui.set_visible(nuclei, False)
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
                fields["download_weights"],
                fields["install_dinov3"],
                ui.help_text(
                    "Built-in weights are downloaded from Hugging Face on first use, only with "
                    "the download ticked. The cpdino models need DINOv3: tick to install it, then "
                    "<b>restart the runtime</b> and run the notebook again from its first cell."
                ),
            ],
        ),
        ui.card(
            ADVANCED,
            [
                *_settings_box(fields),
                ui.help_text(
                    "Normalisation is always Cellpose's own, per image: each channel to its "
                    "1st-99th percentile."
                ),
            ],
        ),
        nuclei,
    ]
