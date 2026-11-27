"""Cell 5 of the combined notebook: declare the masks cell 4 wrote, rescan, describe them.

For each mask type cell 4 wrote, the user says what the mask is, with no answer chosen for
them (owner, 26 Nov 2026, #574 item 6): *each object has its own number* (a label image) or
*foreground / background only* (a binary mask). The two are measured differently and a file is
never guessed to be one or the other. Cellpose writes one number per object, so only the first
is registered: ``[masks.<type>]`` with ``source_type = "<type>"`` and ``representation =
"instances"`` (shown folded, under the question); the second is refused, saying why. Nothing is
declared until every mask type is answered and **Register the masks** is pressed. Then the
Preprocessing app's own code does the rest, the same code as its buttons (no second
implementation), each step shown in cell 5 as it goes (:mod:`.notebook_register_progress`):

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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import ipywidgets as widgets
from IPython import get_ipython
from IPython.display import display

from . import notebook_style as ui
from .content_memo import ContentMemo
from .folder_rules import masks_folder_issues
from .mask_answers import BINARY, INSTANCES
from .notebook_cellpose_app import app_folders
from .notebook_open_progress import opening
from .notebook_register_progress import RegisterWait
from .notebook_render import blocked_line
from .notebook_shown_first import ShownFirst
from .open_checks import Checking, quiet
from .register_checks import (
    CHANGED,
    _collision,
    _only_written,
    _other_experiment,
    _same_publication,
    changes_since_check,
)
from .run_inputs import RunInputsError, UnrecordedFilesError
from .segmentation_cellpose_run import manifest_sha256, open_run
from .segmentation_record import SegmentationError, check_mask_type_folder

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
REPUBLISHED = (
    "The run in {segmented} was published again since cell 4 segmented it (cell 3 run again): "
    "its masks were made on the run as cell 4 opened it. In cell 4 press Open the run again "
    "and Segment the whole experiment again (overwrite), then run this cell again. Nothing was "
    "declared."
)
AXES_AGAIN = (
    "The folder check with the masks folder asks you to confirm the axes again: in cell 3, "
    "step Axes, check the example and press Confirm these axes. Then press Register the masks "
    "again here."
)
CHANGED_SINCE_CHECK = "It was made again because {reasons}."
"""What follows :data:`AXES_AGAIN`: why the folders were checked again (v0.9.1)."""
PAIRING = "Pairing the masks folder (folders and image headers unchanged: your axes are kept)"
"""The step line when the masks are paired without a new folder check (v0.9.1)."""
CHOICES = [
    (
        "Each object has its own number (label image: every cell/nucleus is a separate object)",
        INSTANCES,
    ),
    ("Foreground / background only (binary mask)", BINARY),
]
"""The answers to "what is this mask?", in this order; none is chosen for the user."""
WHY_WE_ASK = (
    "We ask because the two are measured differently, and we never guess which one a file is."
)
BINARY_REFUSED_CODE = "cellpose_mask_not_binary"
"""The code of the refusal of a binary answer for a mask cell 4 wrote (v0.9.1, #578)."""
BINARY_REFUSED = (
    "{mask_type} cannot be registered as foreground / background only: cell 4 wrote it with "
    "Cellpose, which gives each object its own number, so it is a label image and is never "
    "taken for a binary mask (a binary mask from elsewhere is declared in cell 3, step Masks; "
    f"{BINARY_REFUSED_CODE})"
)
"""Why a binary answer is not registered (a phrase beside the disabled button)."""
CHECK_FAILED = (
    "The folder check of cell 3 with the masks folder did not finish: {reason} Nothing was "
    "declared and the run folder is unchanged. Fix what it names, press Check folder (Scan the "
    "folders) in cell 3, step Images, confirm the axes, then press Register the masks again."
)
STOPPED = (
    "Register the masks stopped on an unexpected problem ({problem}). The run records the "
    "masks only once this step says The masks are registered; cell 3 shows what it saved. "
    "Press Register the masks again."
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


def _app_run(app: object, rechecked: bool) -> tuple[str, str]:
    """Return the app's run and masks folder (:func:`.notebook_cellpose_app.app_folders`).

    ``rechecked``: this step ran the app's folder check, which clears the app's last run until
    it runs again; the run is then the output folder the app's own rule gives now.
    """
    run, masks = app_folders(app)
    pre = getattr(app, "preflight", None)
    if not run and rechecked and getattr(app, "result", None) is None and pre is not None:
        run = os.fspath(app._output_path(Path(pre.discovery.root)))
    return run, masks


def refusal(
    app: object,
    segmentation: object,
    *,
    rechecked: bool = False,
    checking: Checking = quiet,
    memo: ContentMemo | None = None,
) -> str:
    """Return why the masks of cell 4 cannot be registered in the app's run, or "".

    ``rechecked``: cell 5 made the app's folder check for this run (also read from the
    segmentation's ``rechecked_run``, so cell 5 run again after the axes stop continues).
    ``checking`` is told each phase of opening the run (:func:`.segmentation_cellpose_run.
    open_run`); it changes nothing checked or decided. ``memo``: the press's content SHA-256s
    (:mod:`.content_memo`).
    """
    types = written_types(segmentation)
    if not types:
        return NO_MASKS
    workflow = segmentation.workflow
    inputs, masks = workflow.inputs, Path(workflow.masks_root).resolve()
    segmented = Path(inputs.output_folder).resolve()
    rechecked = rechecked or getattr(segmentation, "rechecked_run", None) == segmented
    if rechecked and getattr(app, "preflight", None) is None:  # cell 5's folder check failed
        return CHECK_FAILED.format(reason=_plain(app.folder_status.value))
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
        open_run(os.fspath(segmented), checking=checking, memo=memo)
    except UnrecordedFilesError as exc:
        if not _only_written(exc, masks, types):
            return CHANGED.format(reason=exc)
    except (RunInputsError, SegmentationError) as exc:
        return CHANGED.format(reason=exc)
    collision = next(filter(None, (_collision(app, t) for t in types)), "")
    if collision:
        return collision
    for mask_type in types:  # a folder differing only in case appeared since cell 4 (#459)
        try:
            check_mask_type_folder(masks, mask_type)
        except SegmentationError as exc:
            return str(exc)
    return ""


class MaskRegistration(ShownFirst, widgets.VBox):
    """Cell 5's widget: what each mask type is, and **Register the masks**.

    It is built without reading a file; :meth:`check` then opens the run cell 4 segmented,
    with its wait on screen (the #575 show-first pattern, v0.9.1), and shows the refusal when
    the masks cannot be registered.
    """

    def __init__(self, app: object, segmentation: object) -> None:
        """Ask what each mask is; the run is checked by :meth:`check`."""
        super().__init__(layout=widgets.Layout(width="99%"))
        self.app, self.segmentation = app, segmentation
        self.status = widgets.HTML()
        self.opening = widgets.HTML()  # the wait of the run's checks when cell 5 is shown
        self.choices: dict[str, widgets.RadioButtons] = {}
        self.registered = False
        self.refused = ""
        self.skip_next_display = False
        self.button = ui.button("Register the masks", icon="check", main=True)
        self.button.on_click(lambda _: self.register())
        self.blocker = widgets.HTML()
        self.progress, self.progress_detail = widgets.HTML(), widgets.HTML()
        types = written_types(segmentation)
        if not types:
            self._show_refusal(NO_MASKS)
            return
        rows = [self._question(mask_type) for mask_type in types]
        self._update()
        text = (
            "For each mask type cell 4 wrote, say what the mask is. "
            f"{html.escape(WHY_WE_ASK)} Then press <b>Register the masks</b>: it checks the "
            "run cell 4 segmented, pairs the masks folder in the app of cell 3 (checking the "
            "folders again only when something changed since cell 3 checked them), saves the "
            "description, runs again (the histograms are reused) and describes the masks; each "
            "step is shown here as it goes."
        )
        body = [
            ui.help_text(text),
            *rows,
            widgets.HBox([self.button, self.blocker]),
            self.progress,
            self.progress_detail,
            self.status,
        ]
        self.children = [
            widgets.HTML(ui.STYLE_CSS),
            self.opening,
            ui.card("Register the masks", body),
        ]

    def check(self) -> str:
        """Open the run cell 4 segmented, showing the wait; return the refusal or ""."""
        if not self.choices:
            return self.refused
        with opening(self.opening) as checking:
            why = refusal(self.app, self.segmentation, checking=checking)
        if why:
            self._show_refusal(why)
        return why

    def _show_refusal(self, why: str) -> None:
        """Show why the masks cannot be registered, in place of the question."""
        self.refused, self.choices = why, {}
        self.status.value = ui.status("error", "The masks cannot be registered", [why])
        self.children = [widgets.HTML(ui.STYLE_CSS), self.opening, self.status]

    def _question(self, mask_type: str) -> widgets.VBox:
        """Return the question about ``mask_type``, with no answer chosen, and its declaration.

        The declaration it adds to the description is shown folded (``<details>``).
        """
        choice = widgets.RadioButtons(
            options=CHOICES, value=None, layout=widgets.Layout(width="auto")
        )
        choice.observe(lambda _: self._update(), names="value")
        self.choices[mask_type] = choice
        section = html.escape(toml_section(mask_type))
        declared = (
            "<details><summary class='mb-help'>What registering it as separate objects adds "
            f"to the experiment description</summary><pre>{section}</pre></details>"
        )
        title = f"<b>{html.escape(mask_type)}</b>: what is this mask?"
        return widgets.VBox([widgets.HTML(title), choice, widgets.HTML(declared)])

    def _update(self) -> None:
        """Enable the button once every mask type is answered as objects, saying what is left."""
        left = [t for t, choice in self.choices.items() if choice.value is None]
        reasons = [f"choose what {', '.join(left)} is"] if left else []
        reasons += [
            BINARY_REFUSED.format(mask_type=t)
            for t, choice in self.choices.items()
            if choice.value == BINARY
        ]
        self.button.disabled = bool(reasons)
        self.blocker.value = blocked_line(reasons)

    def _stop(self, text: str) -> bool:
        """Show ``text`` as the refusal and return False."""
        self.status.value = ui.status("error", "The masks are not registered yet", [text])
        return False

    def register(self) -> bool:
        """Declare the masks through the app, save, run and describe; True when done.

        Each step is shown under the button while it goes (:class:`.RegisterWait`), and the
        button is disabled meanwhile.
        """
        if self.button.disabled:
            return False
        self.button.disabled = True
        self.status.value = ""
        try:
            with RegisterWait(self.progress, self.progress_detail).running() as wait:
                return self._register(wait, ContentMemo())  # one memo per press, then gone
        except BaseException as exc:  # a click handler's error is never shown in this cell
            self._stop(STOPPED.format(problem=f"{type(exc).__name__}: {exc}"))
            raise
        finally:
            self._update()

    def _register(self, wait: RegisterWait, memo: ContentMemo) -> bool:
        """Do the steps of :meth:`register`, each named by ``wait``; True when done.

        ``memo`` holds the content SHA-256s this press takes: the run's content pass and its
        opening after it reuse one while the file's identity is unchanged; the run's check
        before it publishes never does (:mod:`.content_memo`, owner on #578).
        """
        with wait.step("run"):
            why = refusal(self.app, self.segmentation, checking=wait.checking, memo=memo)
        if why:
            return self._stop(why)
        reasons: list[str] = []
        if not self._check_folders(wait, reasons):  # its reason is drawn in cell 3: say it here
            return self._stop(CHECK_FAILED.format(reason=_plain(self.app.folder_status.value)))
        app = self.app
        if app.confirmed_mapping is None:  # only after a folder check made for a change
            because = CHANGED_SINCE_CHECK.format(reasons="; ".join(reasons)) if reasons else ""
            return self._stop(f"{AXES_AGAIN} {because}".strip())
        why = _other_experiment(app, self.segmentation.workflow.inputs)
        if why:  # the axes stop lets the user change the app: check it is still the run's
            return self._stop(why)
        with wait.step("save"):
            why = self._answer_masks()
            if why:
                return self._stop(why)
            if not app.save_experiment():
                return self._stop(NOT_SAVED)
        with wait.step("rerun", getattr(app, "run_status", None)), _pressed(app, memo):
            result = app.run()
        if result is None or not result.written:
            return self._stop("The run of cell 3 stopped: its step Run says why.")
        # Kept on cell 4's step: this publication is the registration's (cell 5 run again).
        self.segmentation.registered_manifest = manifest_sha256(Path(result.output_folder))
        with wait.step("describe", getattr(app, "mask_step_status", None)), _pressed(app, memo):
            app.mask_step_button.click()  # the app's Describe the masks
        self.registered = True
        self.status.value = self._registered_html(result)
        return True

    def _has_masks(self) -> bool:
        """Return whether the app's last check paired the masks folder with every mask type."""
        masks = Path(self.segmentation.workflow.masks_root).resolve()
        found = getattr(getattr(self.app, "preflight", None), "masks", None)
        checked = getattr(found, "folder", None)
        return (
            checked is not None
            and Path(checked).resolve() == masks
            and all(t in found.types for t in self.choices)  # else checked before cell 4 wrote
        )

    def _check_folders(self, wait: RegisterWait, reasons: list[str]) -> bool:
        """Pair the masks folder in the app; check the folders again only after a change.

        When nothing changed since the app's folder check (:func:`.register_unchanged.
        changes_since_check`), the app's own *Check the masks again* pairs the masks and keeps
        its confirmed axes (v0.9.1: one press). Otherwise the app's folder check is made again
        and ``reasons`` gets what changed. Returns False when that check failed (#580).
        """
        app, masks = self.app, Path(self.segmentation.workflow.masks_root).resolve()
        if self._has_masks():
            return True
        reasons[:] = changes_since_check(app, self.segmentation.workflow.inputs)
        if not reasons:
            with wait.step("folders", getattr(app, "masks_wait", None), PAIRING):
                app.masks_folder.value = os.fspath(masks)
                app._check_masks()  # the app's Check the masks again: only the masks' headers
            if self._has_masks():
                return True
            reasons.append("pairing the masks folder alone did not find every mask type")
        with wait.step("folders", getattr(app, "folder_status", None)):
            app.masks_folder.value = os.fspath(masks)
            pre = app.check_folder()  # the app's Scan the folders, now with the masks folder
        # Kept on cell 4's step, not this widget: cell 5 run again continues from here.
        self.segmentation.rechecked_run = Path(
            self.segmentation.workflow.inputs.output_folder
        ).resolve()
        return pre is not None

    def _answer_masks(self) -> str:
        """Answer each mask on the app's *Masks* question as objects; return a refusal or ""."""
        for mask_type in self.choices:
            question = self.app.mask_card.questions.get(mask_type)
            if question is None:
                return f"The folder check of cell 3 found no {mask_type} masks."
            question.id_box.value = mask_type
            question.choice.value = INSTANCES
            if question.saved is None or question.saved[:2] != (mask_type, INSTANCES):
                return f"{mask_type}: {_plain(question.message.value)}"
        return ""

    def _registered_html(self, result: object) -> str:
        """Return the card saying the masks are registered, with the app's mask step's."""
        return (
            ui.status(
                "ok",
                "The masks are registered",
                [
                    f"Declared: {', '.join(self.choices)}; the run in {result.output_folder} now "
                    "records them and is ready for MorphoFeature.",
                    f"Histograms reused for {len(result.unchanged)} images; images read again: "
                    f"{len(result.read)}"
                    + (f" ({result.rebuild_reason})" if result.rebuild_reason else "")
                    + ".",
                ],
            )
            + self.app.mask_step_status.value
        )


@contextmanager
def _pressed(app: object, memo: ContentMemo) -> Iterator[None]:
    """Give the app's run and its opening after it the press's ``memo`` while the block runs.

    Read by the app as ``_press_memo`` (:meth:`.notebook_run._RunStep._run_mode`, the tissue
    mask card's opening); removed when the block ends, so no later click of cell 3 uses it.
    """
    app._press_memo = memo
    try:
        yield
    finally:
        app._press_memo = None


def _plain(text: str) -> str:
    """Return the text of an HTML message on one line."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split()) or "not saved"


def _shown_first(registration: MaskRegistration) -> None:
    """Display cell 5's widget now; the cell's own ``display(registration)`` then adds nothing."""
    display(registration)
    registration.skip_next_display = True


_DEFAULT = object()
"""``show`` not given: :func:`_shown_first` in a kernel, else None."""


def register_step(
    app: object,
    segmentation: object,
    *,
    show: Callable[[MaskRegistration], object] | None | object = _DEFAULT,
) -> MaskRegistration:
    """Return cell 5's widget for the Preprocessing app of cell 3 and the segmentation of cell 4.

    ``show`` is called with the widget before the run is checked, so that the wait of its
    checks is on screen (#575's pattern for cell 4, applied to cell 5 in v0.9.1); None: the
    widget is returned checked, for the caller to show. By default, in a notebook, it is
    displayed first, and the cell's ``display(registration)`` that follows shows nothing more:
    cell 5 is the same for the release it pins and for this code.
    """
    registration = MaskRegistration(app, segmentation)
    if show is _DEFAULT:
        show = _shown_first if get_ipython() is not None else None
    if show is not None:
        show(registration)
    registration.check()
    return registration


__all__ = [
    "BINARY_REFUSED",
    "BINARY_REFUSED_CODE",
    "CHANGED_SINCE_CHECK",
    "PAIRING",
    "CHOICES",
    "WHY_WE_ASK",
    "MaskRegistration",
    "refusal",
    "register_step",
    "toml_section",
    "written_types",
]
