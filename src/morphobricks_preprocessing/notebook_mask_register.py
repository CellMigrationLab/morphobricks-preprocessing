"""Cell 5 of the combined notebook: declare the masks cell 4 wrote, rescan, describe them.

One declaration is offered per mask type cell 4 wrote, prefilled from the segmentation:
``[masks.<type>]`` with ``source_type = "<type>"`` and ``representation = "instances"``
(Cellpose writes one label per object). Nothing is declared until the user ticks each one and
presses **Register the masks**. Then the Preprocessing app's own code does the rest, the same
code as its buttons (no second implementation):

1. the app's folder check with the masks folder (``check_folder``, *Scan the folders*);
2. each mask answered on the app's *Masks* question (its ID box and its choice, which save the
   answer through the app's draft and its refusals);
3. the app's *Check and save* (``save_experiment``), which publishes the description;
4. the app's run (``run``): the histograms of unchanged images are reused;
5. the app's *Describe the masks* button.

Where the app needs the user (axes to confirm again after the folder check, an answer still
missing, a refusal), the step stops and names the step of cell 3 to use; nothing is clicked
for the user there. Refused before anything changes: no masks written in cell 4, a run other
than the app's, a masks folder inside the experiment (or the output folder), a masks folder
other than the one the app's run uses, images changed since the run, and a mask type that
collides with a declaration the description already has. Before any mask is declared, the
app's checked experiment must still be the segmented run's and its confirmed axes those the
run's manifest records (the axes stop lets the user change both in cell 3).

The masks cell 4 wrote into the masks folder the run recorded are not a change: they are what
is registered (any other file the run did not record still is). The folder check is made again
whenever the app's last check did not find a mask type cell 4 wrote, even in the same folder.
The run must still be the publication cell 4 opened (its pinned manifest) or the one cell 5
made registering the masks: a run cell 3 published again since is refused.
"""

from __future__ import annotations

import html
import os
import re
from pathlib import Path

import ipywidgets as widgets

from . import notebook_style as ui
from .description_identity import nfc
from .folder_rules import masks_folder_issues
from .manifest import MANIFEST_FILENAME, ManifestFormatError, read_manifest
from .mask_answers import INSTANCES
from .notebook_cellpose_app import app_folders
from .notebook_render import blocked_line
from .report import mapping_summary
from .run_inputs import RunInputsError, UnrecordedFilesError
from .segmentation_cellpose_run import manifest_sha256, open_run
from .segmentation_record import SegmentationError

NO_MASKS = (
    "Cell 4 wrote no masks in this session: run cell 4, press Segment the whole experiment, "
    "then run this cell again."
)
OTHER_RUN = (
    "Cell 4 segmented the run in {segmented}, but the app of cell 3 holds {app_run}. Open "
    "that experiment and output folder in cell 3 and run it (the histograms are reused), then "
    "run this cell again."
)
OTHER_MASKS = (
    "Cell 4 wrote the masks to {segmented}, but the run of cell 3 uses the masks folder "
    "{app_masks}. Segment into that folder in cell 4, then run this cell again."
)
COLLISION = (
    "The mask type {mask_type} collides with the mask {mask_id} the description already "
    "declares (source type {source_type}). Choose another mask type in cell 4 and segment "
    "again; nothing is declared twice."
)
REPUBLISHED = (
    "The run in {segmented} was published again since cell 4 segmented it (cell 3 run again): "
    "its masks were made on the run as cell 4 opened it. In cell 4 press Open the run again "
    "and Segment the whole experiment again (overwrite), then run this cell again. Nothing was "
    "declared."
)
CHANGED = "The images changed since the run, or the run cannot be opened: {reason}"
AXES_AGAIN = (
    "The folder check with the masks folder asks you to confirm the axes again: in cell 3, "
    "step Axes, check the example and press Confirm these axes. Then press Register the masks "
    "again here."
)
OTHER_EXPERIMENT = (
    "The app of cell 3 now holds another experiment ({app_root}) than the one cell 4 "
    "segmented ({segmented}). Check {segmented} again in cell 3 and confirm its axes, then "
    "press Register the masks again. Nothing was declared."
)
OTHER_AXES = (
    "The axes confirmed in cell 3 ({confirmed}) are not those the segmented run recorded "
    "({recorded}): its masks were made on the images read with the recorded axes. In cell 3, "
    "step Axes, confirm the recorded axes, then press Register the masks again. Nothing was "
    "declared."
)
NOT_SAVED = (
    "Check and save did not save the description: open cell 3, step Check and save, answer "
    "what it lists, then press Register the masks again here."
)


def toml_section(mask_type: str) -> str:
    """Return the declaration offered for ``mask_type``, as the description file writes it."""
    return (
        f"[masks.{mask_type}]\nsource_type = {_quoted(mask_type)}\n"
        f"representation = {_quoted(INSTANCES)}"
    )


def _quoted(text: str) -> str:
    """Return ``text`` as a TOML basic string (mask types are folder-name tokens)."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def written_types(segmentation: object) -> tuple[str, ...]:
    """Return the mask types cell 4's segmentation has masks of (written or verified)."""
    workflow = getattr(segmentation, "workflow", None)
    results = getattr(workflow, "results", None) or {}
    return tuple(
        mask_type
        for mask_type, batch in results.items()
        if batch.counts()["written"] + batch.counts()["skipped"] > 0
    )


def _collision(app: object, mask_type: str) -> str:
    """Return the refusal of ``mask_type`` when the app's description declares it, else "".

    A declaration whose mask ID or source type is ``mask_type`` (ignoring case) collides,
    unless it is exactly the one offered (an earlier press of **Register the masks** that
    stopped later, at the save or the run, already gave it).
    """
    answers = getattr(app, "draft_answers", None)
    masks = getattr(answers, "masks", None) or {}
    folded = nfc(mask_type).casefold()
    for mask_id, mask in masks.items():
        offered = (mask_id, mask.source_type, mask.representation)
        if offered == (mask_type, mask_type, INSTANCES):
            continue
        if folded in (nfc(mask_id).casefold(), nfc(mask.source_type).casefold()):
            return COLLISION.format(
                mask_type=mask_type, mask_id=mask_id, source_type=mask.source_type
            )
    return ""


def _app_run(app: object, rechecked: bool) -> tuple[str, str]:
    """Return the app's run and masks folder (:func:`.notebook_cellpose_app.app_folders`).

    ``rechecked``: this step ran the app's folder check, which clears the app's last run until
    it runs again; the run is then the output folder the app's own rule gives now.
    """
    run, masks = app_folders(app)
    if not run and rechecked and getattr(app, "result", None) is None:
        run = os.fspath(app._output_path(Path(app.preflight.discovery.root)))
    return run, masks


def refusal(app: object, segmentation: object, *, rechecked: bool = False) -> str:
    """Return why the masks of cell 4 cannot be registered in the app's run, or "".

    ``rechecked``: cell 5 made the app's folder check for this run (also read from the
    segmentation's ``rechecked_run``, so cell 5 run again after the axes stop continues).
    """
    types = written_types(segmentation)
    if not types:
        return NO_MASKS
    workflow = segmentation.workflow
    inputs, masks = workflow.inputs, Path(workflow.masks_root).resolve()
    segmented = Path(inputs.output_folder).resolve()
    rechecked = rechecked or getattr(segmentation, "rechecked_run", None) == segmented
    app_run, app_masks = _app_run(app, rechecked)
    if not app_run or Path(app_run).resolve() != segmented:
        held = app_run or "no finished run"
        return OTHER_RUN.format(segmented=segmented, app_run=held)
    if not _same_publication(segmentation, segmented):
        return REPUBLISHED.format(segmented=segmented)
    issues = masks_folder_issues(masks, inputs.experiment_root, segmented)
    if issues:
        return f"{issues[0].message} {issues[0].action}".strip()
    if app_masks and Path(app_masks).resolve() != masks:
        return OTHER_MASKS.format(segmented=masks, app_masks=app_masks)
    try:
        open_run(os.fspath(segmented))
    except UnrecordedFilesError as exc:
        if not _only_written(exc, masks, types):
            return CHANGED.format(reason=exc)
    except (RunInputsError, SegmentationError) as exc:
        return CHANGED.format(reason=exc)
    return next(filter(None, (_collision(app, t) for t in types)), "")


def _same_publication(segmentation: object, segmented: Path) -> bool:
    """Return whether the run's manifest is the one cell 4 segmented, or cell 5's own.

    Cell 4 pins the manifest's SHA-256 when it opens the run (``run_manifest_sha256``,
    :func:`.segmentation_cellpose_run.open_pinned`); a run published again since (cell 3 run
    again, perhaps on changed images or other axes) is not the run the masks were made on. The
    only other publication accepted is the one cell 5 made registering them
    (``registered_manifest``), so that cell 5 run again after it is not refused.
    """
    now = manifest_sha256(segmented)
    pinned = getattr(segmentation.workflow, "run_manifest_sha256", None)
    ours = getattr(segmentation, "registered_manifest", None)
    return now is not None and now in {pinned, ours}


def _only_written(exc: UnrecordedFilesError, masks: Path, types: tuple[str, ...]) -> bool:
    """Return whether the run left out only masks cell 4 wrote: files in ``masks/<type>/``.

    Those are the masks to register (the run before them could not record them). An image,
    a label image or any other mask the run did not record is still refused.
    """
    return not exc.files.images and all(
        len(Path(path).parts) > 1 and Path(path).parts[0] in types and (masks / path).is_file()
        for path in exc.files.masks
    )


def _mapping_text(mapping: object) -> str:
    """Return an axis mapping on one line (:func:`.report.mapping_summary`'s words)."""
    return "; ".join(line.strip() for line in mapping_summary(mapping).splitlines())


def _other_experiment(app: object, inputs: object) -> str:
    """Return why the app's experiment or confirmed axes are not the segmented run's, or "".

    The app's checked experiment must be the run's experiment, and the axes confirmed in the
    app must be the axis mapping the run's manifest records: the masks were made on the images
    read with those axes. Read before anything is declared, saved or run.
    """
    app_root = Path(app.preflight.discovery.root).resolve()
    segmented = Path(inputs.experiment_root).resolve()
    if app_root != segmented:
        return OTHER_EXPERIMENT.format(app_root=app_root, segmented=segmented)
    try:
        recorded = read_manifest(Path(inputs.output_folder) / MANIFEST_FILENAME).mapping
    except (OSError, ManifestFormatError) as exc:
        return CHANGED.format(reason=exc)
    confirmed = app.confirmed_mapping
    if (confirmed.axes, tuple(confirmed.roles)) != (recorded.axes, tuple(recorded.roles)):
        return OTHER_AXES.format(
            confirmed=_mapping_text(confirmed), recorded=_mapping_text(recorded)
        )
    return ""


class MaskRegistration(widgets.VBox):
    """Cell 5's widget: one declaration to tick per mask type, and **Register the masks**."""

    def __init__(self, app: object, segmentation: object) -> None:
        """Offer the declarations, or show why the masks cannot be registered."""
        super().__init__(layout=widgets.Layout(width="99%"))
        self.app, self.segmentation = app, segmentation
        self.status = widgets.HTML()
        self.ticks: dict[str, widgets.Checkbox] = {}
        self.registered = False
        why = refusal(app, segmentation)
        if why:
            self.status.value = ui.status("error", "The masks cannot be registered", [why])
            self.children = [widgets.HTML(ui.STYLE_CSS), self.status]
            return
        rows = []
        for mask_type in written_types(segmentation):
            tick = widgets.Checkbox(
                value=False,
                description=f"Declare {mask_type} as separate objects",
                indent=False,
                layout=widgets.Layout(width="auto"),
            )
            tick.observe(lambda _: self._update(), names="value")
            self.ticks[mask_type] = tick
            section = html.escape(toml_section(mask_type))
            rows += [widgets.HTML(f"<pre>{section}</pre>"), tick]
        self.button = ui.button("Register the masks", icon="check", main=True)
        self.button.on_click(lambda _: self.register())
        self.blocker = widgets.HTML()
        self._update()
        text = (
            "Each mask type cell 4 wrote, as it would be declared in the experiment description. "
            "Tick each one you confirm, then press <b>Register the masks</b>: the app of cell 3 "
            "checks the folders again with the masks folder, saves the description, runs again "
            "(the histograms are reused) and describes the masks."
        )
        body = [ui.help_text(text), *rows, widgets.HBox([self.button, self.blocker]), self.status]
        self.children = [widgets.HTML(ui.STYLE_CSS), ui.card("Register the masks", body)]

    def _update(self) -> None:
        """Enable the button once every declaration is ticked, saying which are not."""
        left = [t for t, tick in self.ticks.items() if not tick.value]
        self.button.disabled = bool(left)
        reasons = [f"tick the declaration of {', '.join(left)}"] if left else []
        self.blocker.value = blocked_line(reasons)

    def _stop(self, text: str) -> bool:
        """Show ``text`` as the refusal and return False."""
        self.status.value = ui.status("error", "The masks are not registered yet", [text])
        return False

    def register(self) -> bool:
        """Declare the ticked masks through the app, save, run and describe; True when done."""
        if self.button.disabled:
            return False
        why = refusal(self.app, self.segmentation)
        if why:
            return self._stop(why)
        app, masks = self.app, Path(self.segmentation.workflow.masks_root).resolve()
        found = getattr(app.preflight, "masks", None)
        checked = getattr(found, "folder", None)
        if (
            checked is None
            or Path(checked).resolve() != masks
            or any(t not in found.types for t in self.ticks)  # checked before cell 4 wrote
        ):
            app.masks_folder.value = os.fspath(masks)
            app.check_folder()  # the app's Scan the folders, now with the masks folder
            # Kept on cell 4's step, not this widget: cell 5 run again continues from here.
            self.segmentation.rechecked_run = Path(
                self.segmentation.workflow.inputs.output_folder
            ).resolve()
        if app.confirmed_mapping is None:
            return self._stop(AXES_AGAIN)
        why = _other_experiment(app, self.segmentation.workflow.inputs)
        if why:  # the axes stop lets the user change the app: check it is still the run's
            return self._stop(why)
        for mask_type in self.ticks:
            question = app.mask_card.questions.get(mask_type)
            if question is None:
                return self._stop(f"The folder check of cell 3 found no {mask_type} masks.")
            question.id_box.value = mask_type
            question.choice.value = INSTANCES
            if question.saved is None or question.saved[:2] != (mask_type, INSTANCES):
                return self._stop(f"{mask_type}: {_plain(question.message.value)}")
        if not app.save_experiment():
            return self._stop(NOT_SAVED)
        result = app.run()
        if result is None or not result.written:
            return self._stop("The run of cell 3 stopped: its step Run says why.")
        # Kept on cell 4's step: this publication is the registration's (cell 5 run again).
        self.segmentation.registered_manifest = manifest_sha256(Path(result.output_folder))
        app.mask_step_button.click()  # the app's Describe the masks
        self.registered = True
        self.status.value = (
            ui.status(
                "ok",
                "The masks are registered",
                [
                    f"Declared: {', '.join(self.ticks)}; the run in {result.output_folder} now "
                    "records them and is ready for MorphoFeature.",
                    f"Histograms reused for {len(result.unchanged)} images; images read again: "
                    f"{len(result.read)}"
                    + (f" ({result.rebuild_reason})" if result.rebuild_reason else "")
                    + ".",
                ],
            )
            + app.mask_step_status.value
        )
        return True


def _plain(text: str) -> str:
    """Return the text of an HTML message on one line."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split()) or "not saved"


def register_step(app: object, segmentation: object) -> MaskRegistration:
    """Return cell 5's widget for the Preprocessing app of cell 3 and the segmentation of cell 4."""
    return MaskRegistration(app, segmentation)


__all__ = ["MaskRegistration", "refusal", "register_step", "toml_section", "written_types"]
