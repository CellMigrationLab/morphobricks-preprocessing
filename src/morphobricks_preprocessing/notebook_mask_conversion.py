"""The notebook's optional **Convert a mask** card: the conversion helpers (Spec ADR 0005 BM12).

Under **Describe the masks**, once the output folder holds a run with declared masks, the card
offers the two helpers of ``RunInputs`` with the same functions as the command line's
``convert-mask``: the objects of a ``binary`` mask as a new ``instances`` mask
(``binary_to_instances``), or one ``binary`` mask per class value of a class image declared
``parent_labels`` or ``instances`` (``classes_to_binary``). The source is chosen among the masks
declared with a representation the helper converts; the new mask ID (or the prefix of the class
masks) is typed, never proposed. One click converts every image with a file of the mask and shows
the files written and what to do next (declare them, run again, then Describe the masks). A
refusal is shown as a red card naming it; the source is never changed and nothing is
overwritten.
"""

from __future__ import annotations

import html
import traceback
from pathlib import Path

import ipywidgets as widgets

from . import notebook_style as ui
from .description_consume import read_published_description
from .description_file import DESCRIPTION_FILENAME
from .fingerprint import current_fingerprint
from .mask_conversion import (
    BINARY_TO_INSTANCES,
    CLASSES_TO_BINARY,
    MaskConversionResult,
    conversion_next_steps,
    images_with_mask,
)
from .notebook_render import blocked_line
from .run_inputs import RunInputs

_LABEL = " Convert the mask"
_ICON = "clone"
OPERATIONS = (
    ("A binary mask's connected objects, as a new objects (instances) mask", BINARY_TO_INSTANCES),
    ("A class image, as one binary mask per class value", CLASSES_TO_BINARY),
)
_SOURCES = {BINARY_TO_INSTANCES: ("binary",), CLASSES_TO_BINARY: ("parent_labels", "instances")}
_NAME_HELP = {
    BINARY_TO_INSTANCES: (
        "The ID of the new objects mask (letters, digits and single underscores, starting with "
        "a letter), e.g. <b>TumorObjects</b>. Its files go to the masks folder under that name."
    ),
    CLASSES_TO_BINARY: (
        "The start of the new masks' IDs: each class value v gives <b>&lt;prefix&gt;_classv</b>, "
        "e.g. <b>Tissue_class1</b>, <b>Tissue_class2</b>. Class 0 is the background."
    ),
}
_NAME_MISSING = {
    BINARY_TO_INSTANCES: "type the new mask ID",
    CLASSES_TO_BINARY: "type the prefix of the new masks",
}


class MaskConversionCard:
    """The optional card that converts one declared mask of a run into new mask files.

    Attributes
    ----------
    card
        The card widget.
    operation, source, name, button, blocker, status, result
        Its controls, the line saying what blocks the button while it is disabled, and its
        outputs.
    """

    def __init__(self) -> None:
        self.output_folder: Path | None = None
        self._declared: dict[str, str] = {}
        self._seen: tuple | None = None
        self.operation = widgets.Dropdown(
            options=list(OPERATIONS), value=BINARY_TO_INSTANCES, layout=widgets.Layout(width="99%")
        )
        self.source = widgets.Dropdown(options=[], layout=widgets.Layout(width="99%"))
        self.name = widgets.Text(placeholder="new mask ID", layout=widgets.Layout(width="99%"))
        self.name_help = ui.help_text(_NAME_HELP[BINARY_TO_INSTANCES])
        self.button = ui.button(
            _LABEL,
            style="success",
            icon=_ICON,
            tooltip="Write the new mask files into the masks folder (the source is only read)",
            main=True,
        )
        self.blocker = widgets.HTML()
        self.hint = widgets.HTML()
        self.status = widgets.HTML()
        self.result = widgets.HTML()
        self.card = ui.card(
            "Convert a mask (optional)",
            [
                ui.help_text(
                    "Writes <b>new</b> mask files from a mask the masks step has checked; the "
                    "mask itself is only read and nothing is overwritten. Each file gets a line "
                    "in <b>mask_lineage.jsonl</b> naming its source. Declare the new masks, run "
                    "again and describe the masks before a brick uses them."
                ),
                self.hint,
                self.operation,
                ui.help_text("What to write."),
                self.source,
                ui.help_text("The mask to convert, among the masks declared for this."),
                self.name,
                self.name_help,
                self.button,
                self.blocker,
                self.status,
                self.result,
            ],
        )
        self.operation.observe(lambda _: self._operation_changed(), names="value")
        self.name.observe(lambda _: self._update_button(), names="value")
        self.button.on_click(lambda _: self.convert())
        self.refresh(None)

    def refresh(self, output_folder: Path | None) -> None:
        """Offer the masks the run in ``output_folder`` declares; disable the card without one."""
        description = None if output_folder is None else output_folder / DESCRIPTION_FILENAME
        seen = (output_folder, None if description is None else current_fingerprint(description))
        if seen == self._seen:  # the same published description: keep the last result shown
            return
        self._seen = seen
        self.output_folder = output_folder
        self._declared = {}
        if output_folder is not None:
            try:
                published, _ = read_published_description(description)
                self._declared = {k: m.representation for k, m in published.masks.items()}
            except (OSError, ValueError):
                self._declared = {}
        self.hint.value = (
            "<div class='mb-help'>Available after a run whose description declares masks.</div>"
            if not self._declared
            else ""
        )
        self.status.value = self.result.value = ""
        self._operation_changed()

    def _operation_changed(self) -> None:
        """Offer the sources the chosen helper converts, and its help line."""
        wanted = _SOURCES[self.operation.value]
        options = sorted(k for k, rep in self._declared.items() if rep in wanted)
        self.source.options = options
        self.source.value = options[0] if options else None
        self.name_help.value = f"<div class='mb-help'>{_NAME_HELP[self.operation.value]}</div>"
        self._update_button()

    def _update_button(self) -> None:
        """Enable the button once a source is chosen and a name typed; else say what blocks it."""
        reasons = []
        if not self._declared:
            reasons.append("no run in the output folder declares masks")
        elif not self.source.value:
            wanted = " or ".join(_SOURCES[self.operation.value])
            reasons.append(f"the description declares no {wanted} mask for this conversion")
        if not self.name.value.strip():
            reasons.append(_NAME_MISSING[self.operation.value])
        self.button.disabled = bool(reasons)
        self.blocker.value = blocked_line(reasons)

    def convert(self) -> list[MaskConversionResult] | None:
        """Convert the chosen mask of every image that has one; show what was written.

        Returns
        -------
        list of MaskConversionResult or None
            None when the card is disabled or a refusal stopped it (the card says why).
        """
        if self.button.disabled or self.output_folder is None:
            return None
        self.button.disabled = True
        self.status.value, self.result.value = ui.working("Converting the mask…"), ""
        try:
            return self._convert(self.operation.value, self.source.value, self.name.value)
        except ValueError as exc:  # every refusal of the helpers (RunInputsError included)
            self.status.value = ui.status(
                "error",
                "The mask was not converted.",
                [str(exc), "Images converted before this one stay converted."],
            )
        except Exception as exc:  # unexpected failures still get a readable message
            self.status.value = ui.status(
                "error",
                "The mask could not be converted because of an unexpected problem.",
                [f"{type(exc).__name__}: {exc}", traceback.format_exc(limit=1)],
            )
        finally:
            self._update_button()
        return None

    def _convert(self, operation: str, mask_id: str, name: str) -> list[MaskConversionResult]:
        """Run the helper on every image with a file of the mask; show the outputs."""
        run = RunInputs.open(self.output_folder)
        results = []
        for image in images_with_mask(run, mask_id):
            if operation == BINARY_TO_INSTANCES:
                results.append(run.binary_to_instances(image, mask_id, output_mask_id=name))
            else:
                results.append(run.classes_to_binary(image, mask_id, output_prefix=name))
        written = [o.path for r in results for o in r.outputs if o.published]
        kept = [o.path for r in results for o in r.outputs if not o.published]
        lines = [f"Written: {len(written)} file(s); already written before: {len(kept)}."]
        lines += conversion_next_steps(results, run.masks_root).splitlines()
        self.status.value = ui.status("ok", "The mask is converted.", lines)
        rows = "".join(
            f"<li>{html.escape(r.image)}: {html.escape(_written(r))}</li>" for r in results
        )
        self.result.value = f"<ul>{rows}</ul>"
        return results


def _written(result: MaskConversionResult) -> str:
    """Return one image's output files, or its note when it has none."""
    return ", ".join(o.path for o in result.outputs) if result.outputs else result.note
