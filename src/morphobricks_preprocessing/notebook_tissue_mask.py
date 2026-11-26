"""The notebook's optional **Make an H&E tissue mask** card (Spec ADR 0005 BM15; design §5).

Beside **Convert a mask**, under **Describe the masks**, once the output folder holds a run with at
least one unit declared ``rgb``. It calls the same functions as the command line's
``tissue-mask``:

1. **Choose** the RGB positions (grouped by unit, with "all of this unit"); positions that cannot
   be used are listed greyed with their reason (a Z or T stack, not 8-bit, not three channels).
2. **Preview** one chosen image in memory (``RunInputs.preview_he_tissue_mask``, nothing is
   written): the image with each excluded component tinted (nuclei blue, red cells orange,
   background grey), the kept share and each component's share from the full-resolution mask
   (the picture may be downsampled), the note that sizes are in pixels and the rule is fixed and
   provisional, and "this mask will not be reused on retry" when a code identity is unverified.
3. **New mask ID:** typed by the user, never proposed.
4. **Write** every chosen position, only on this click; then the files written, the retries that
   changed nothing, and the next steps (declare the mask binary, run again, Describe the masks).

A disabled button names what blocks it; a refusal during the write is a red card naming the
position. Nothing is computed until the user clicks.
"""

from __future__ import annotations

import html
import importlib.util
import io
import traceback
from pathlib import Path

import ipywidgets as widgets
import numpy as np

from . import notebook_style as ui
from .content_memo import ContentMemo
from .fingerprint import current_fingerprint
from .manifest import MANIFEST_FILENAME
from .mask_conversion import _check_case_collision, _check_output_id
from .notebook_render import blocked_line
from .plane_errors import PlaneReadError
from .run_inputs import RunInputs, open_checked
from .run_inputs_tissue import NOT_REUSED, PROVISIONAL_NOTE, tissue_next_steps

INSTALL_HINT = 'pip install "morphobricks-preprocessing[he-mask]"'
TINTS = (("background", (150, 150, 150)), ("red_cells", (255, 140, 0)), ("nuclei", (40, 90, 220)))
"""The preview's tint of each excluded component, drawn in this order (the proof of concept's)."""
PREVIEW_SIDE = 512
"""The largest side of the preview picture; the numbers come from the full-resolution mask."""
REFUSALS = (ValueError, ImportError, PlaneReadError, OSError)
"""The refusals a preview or a write shows as its message: a plane that cannot be read too."""


def unusable_reason(fov: object) -> str | None:
    """Return why a position of a unit declared ``rgb`` cannot be used, or None."""
    if fov.z_count > 1 or fov.t_count > 1:
        return f"a Z or T stack ({fov.z_count} Z planes, {fov.t_count} time points)"
    if fov.dtype != "uint8":
        return f"not 8-bit ({fov.dtype})"
    if fov.c_count != 3:
        return f"{fov.c_count} channels, not R, G, B"
    return None


def tinted(rgb: np.ndarray, components: object, side: int = PREVIEW_SIDE) -> np.ndarray:
    """Return the ``(Y, X, 3)`` picture with each component tinted, every n-th pixel kept."""
    step = max(1, -(-max(rgb.shape[1:]) // side))
    picture = np.moveaxis(rgb, 0, -1)[::step, ::step].astype(np.float32)
    for name, colour in TINTS:
        where = getattr(components, name)[::step, ::step]
        picture[where] = 0.45 * picture[where] + 0.55 * np.array(colour, np.float32)
    return picture.round().astype(np.uint8)


def png_bytes(picture: np.ndarray) -> bytes:
    """Return a ``(Y, X, 3)`` uint8 picture as PNG bytes."""
    from matplotlib import image

    buffer = io.BytesIO()
    image.imsave(buffer, picture, format="png")
    return buffer.getvalue()


def shares_line(fractions: object) -> str:
    """Return "kept 69.0 % · nuclei 25.7 % · red cells 0.4 % · empty 4.6 %"."""
    parts = [
        ("kept", "kept"),
        ("nuclei", "nuclei"),
        ("red cells", "red_cells"),
        ("empty", "background"),
    ]
    return " · ".join(f"{label} {100 * fractions[key]:.1f} %" for label, key in parts)


class TissueMaskCard:
    """The optional card that writes the H&E tissue mask of chosen RGB positions.

    Attributes
    ----------
    card
        The card widget.
    unit, add_unit, positions, unusable, preview_image, preview_button, picture, numbers,
    name, button, blocker, status, result
        Its controls, the line saying what blocks the button while it is disabled, and its
        outputs.
    """

    def __init__(self) -> None:
        self.output_folder: Path | None = None
        self.has_rgb = False
        self._run: RunInputs | None = None
        self._units: dict[str, list[str]] = {}
        self._problem = ""
        self._seen: tuple | None = None
        layout = widgets.Layout(width="99%")
        self.hint = widgets.HTML()
        self.unit = widgets.Dropdown(options=[], layout=layout)
        self.add_unit = ui.button(" Choose all of this unit", style="info", icon="check-square")
        self.positions = widgets.SelectMultiple(options=[], rows=6, layout=layout)
        self.unusable = widgets.HTML()
        self.preview_image = widgets.Dropdown(options=[], layout=layout)
        self.preview_button = ui.button(" Preview", style="info", icon="eye")
        self.picture = widgets.Image(format="png", layout=widgets.Layout(max_width="99%"))
        ui.set_visible(self.picture, False)
        self.numbers = widgets.HTML()
        self.name = widgets.Text(placeholder="new mask ID", layout=layout)
        self.button = ui.button(
            " Write the tissue mask",
            style="success",
            icon="tint",
            tooltip="Write the tissue mask of every chosen position into the masks folder",
            main=True,
        )
        self.blocker = widgets.HTML()
        self.status = widgets.HTML()
        self.result = widgets.HTML()
        self.card = ui.card("Make an H&E tissue mask (optional)", self._children())
        self.add_unit.on_click(lambda _: self._choose_unit())
        self.positions.observe(lambda _: self._positions_changed(), names="value")
        self.preview_button.on_click(lambda _: self.preview())
        self.name.observe(lambda _: self._update_button(), names="value")
        self.button.on_click(lambda _: self.write())
        self.refresh(None)

    def _children(self) -> list[widgets.Widget]:
        """Return the card's widgets in their order."""
        return [
            ui.help_text(
                "Writes a <b>new</b> region mask that leaves out nuclei, red blood cells and "
                "empty space of H&E images, with a fixed rule (<b>he_tissue_v1</b>, "
                "provisional; not a trained segmentation). The images are only read and nothing "
                "is overwritten. Each file gets a line in <b>mask_lineage.jsonl</b>. Declare the "
                "new mask as binary, run again and describe the masks before a brick uses it."
            ),
            self.hint,
            self.unit,
            self.add_unit,
            self.positions,
            ui.help_text("The RGB positions to write a tissue mask for (Ctrl-click for more)."),
            self.unusable,
            self.preview_image,
            self.preview_button,
            ui.help_text("Computes the mask of one image in memory; nothing is written."),
            self.picture,
            self.numbers,
            self.name,
            ui.help_text(
                "The ID of the new mask (letters, digits and single underscores, starting with a "
                "letter), e.g. <b>HETissue</b>. Its files go to the masks folder under that name."
            ),
            self.button,
            self.blocker,
            self.status,
            self.result,
        ]

    def refresh(self, output_folder: Path | None, *, memo: ContentMemo | None = None) -> None:
        """Offer the RGB positions of the run in ``output_folder``; clear the card for another.

        ``memo``: the content SHA-256s the press that published the run already took
        (:mod:`.content_memo`), reused while a file's identity is unchanged.
        """
        manifest = None if output_folder is None else output_folder / MANIFEST_FILENAME
        seen = (output_folder, None if manifest is None else current_fingerprint(manifest))
        if seen == self._seen:  # the same run: keep the last preview and result shown
            return
        self._seen = seen
        self.output_folder, self._run, self._units, self._problem = output_folder, None, {}, ""
        greyed = []
        if output_folder is not None and manifest.exists():
            try:
                self._run = open_checked(output_folder, memo=memo)
            except (OSError, ValueError) as exc:
                self._problem = str(exc)
        for fov in () if self._run is None else self._run.fovs():
            if fov.image_type != "rgb":
                continue
            unit = fov.relative_path.rpartition("/")[0] or "."
            reason = unusable_reason(fov)
            if reason is None:
                self._units.setdefault(unit, []).append(fov.relative_path)
            else:
                greyed.append(f"{html.escape(fov.relative_path)}: {html.escape(reason)}")
        self.has_rgb = bool(self._units or greyed)
        self.unit.options = sorted(self._units)
        self.unit.value = self.unit.options[0] if self.unit.options else None
        self.positions.options = sorted(p for paths in self._units.values() for p in paths)
        self.unusable.value = (
            "<div class='mb-help' style='color:#888'>Cannot be used: "
            + "; ".join(greyed)
            + "</div>"
            if greyed
            else ""
        )
        self.hint.value = "" if self.has_rgb else (
            "<div class='mb-help'>Available after a run with a unit declared rgb.</div>"
        )  # fmt: skip
        self.status.value = self.result.value = self.numbers.value = ""
        ui.set_visible(self.picture, False)
        self._positions_changed()

    def _choose_unit(self) -> None:
        """Add every usable position of the chosen unit to the selection."""
        chosen = set(self.positions.value) | set(self._units.get(self.unit.value, ()))
        self.positions.value = tuple(sorted(chosen))

    def _positions_changed(self) -> None:
        """Offer the chosen positions (or every usable one) for the preview."""
        options = list(self.positions.value) or list(self.positions.options)
        self.preview_image.options = options
        self.preview_image.value = options[0] if options else None
        self._update_button()

    def reasons(self) -> list[str]:
        """Return what blocks the write, in the order the user fixes it (empty when nothing)."""
        if self._run is None:
            return [self._problem or "no run in the output folder"]
        if not self.has_rgb:
            return ["the run has no unit declared rgb"]
        found = []
        if self._run.masks_root is None:
            found.append("the run has no masks folder (run preprocessing with one)")
        if not self.positions.value:
            found.append("choose at least one position")
        found += self._name_reasons(self.name.value)  # as typed: the write uses it unchanged
        if importlib.util.find_spec("skimage") is None:
            found.append(f"scikit-image is not installed: {INSTALL_HINT}")
        return found

    def _name_reasons(self, name: str) -> list[str]:
        """Return why the typed mask ID cannot be used (empty when it can); never trimmed."""
        if not name.strip():
            return ["type the new mask ID"]
        declared = self._run.description().masks
        if name in declared:
            return [f"the mask ID {name} is declared already; choose another"]
        try:
            _check_output_id(name, "output mask ID")
            _check_case_collision(name, declared)
        except ValueError as exc:
            return [str(exc)]
        return []

    def _update_button(self) -> None:
        """Enable the write once everything is chosen; else say what blocks it."""
        reasons = self.reasons()
        self.button.disabled = bool(reasons)
        self.blocker.value = blocked_line(reasons)
        self.preview_button.disabled = self._run is None or not self.preview_image.value

    def preview(self) -> object | None:
        """Compute the chosen image's mask in memory and show it; nothing is written."""
        if self._run is None or not self.preview_image.value:
            return None
        self.numbers.value = ui.working("Computing the preview…")
        ui.set_visible(self.picture, False)  # an earlier image's preview is not this one's
        try:
            found = self._run.preview_he_tissue_mask(self.preview_image.value)
        except REFUSALS as exc:
            self.numbers.value = ui.status("error", "No preview.", [str(exc)])
            return None
        self.picture.value = png_bytes(tinted(found.rgb, found.components))
        ui.set_visible(self.picture, True)
        notes = [shares_line(found.fractions), PROVISIONAL_NOTE if found.provisional else ""]
        if found.fractions["od_floored"] > 0:
            notes.append(
                f"{100 * found.fractions['od_floored']:.2f} % of pixels have a channel at 0"
            )
        if not found.reusable:
            notes.append(f"A code identity is unverified: {NOT_REUSED}.")
        kind = "warn" if found.fractions["kept"] == 0 or not found.reusable else "info"
        if found.fractions["kept"] == 0:
            notes.insert(0, "Nothing is kept (0 %): the mask would be an empty region.")
        self.numbers.value = ui.status(kind, "Preview (nothing written)", [n for n in notes if n])
        return found

    def write(self) -> list | None:
        """Write the tissue mask of every chosen position; show what was written."""
        if self.button.disabled or self._run is None:
            return None
        self.button.disabled = True
        self.status.value, self.result.value = ui.working("Writing the tissue masks…"), ""
        results: list = []
        try:
            for image in self.positions.value:
                results.append(self._run.he_tissue_mask(image, output_mask_id=self.name.value))
        except REFUSALS as exc:
            self.status.value = ui.status(
                "error",
                f"No tissue mask was written for {image}.",
                [str(exc), f"{len(results)} position(s) before it stay written."],
            )
            return None
        except Exception as exc:  # unexpected failures still get a readable message
            self.status.value = ui.status(
                "error",
                "The tissue mask could not be written because of an unexpected problem.",
                [f"{type(exc).__name__}: {exc}", traceback.format_exc(limit=1)],
            )
            return None
        finally:
            self._update_button()
        self._show(results)
        return results

    def _show(self, results: list) -> None:
        """Show the files written, the retries that changed nothing and the next steps."""
        written = [r.path for r in results if r.published]
        kept = [r.path for r in results if not r.published]
        lines = [f"Written: {len(written)} file(s); already written before: {len(kept)}."]
        if not all(r.reusable for r in results):
            lines.append(f"A code identity is unverified: {NOT_REUSED}.")
        lines += tissue_next_steps((r.mask_id for r in results), self._run.masks_root).splitlines()
        self.status.value = ui.status("ok", "The tissue mask is written.", lines)
        rows = "".join(
            f"<li>{html.escape(r.path)}: kept {100 * r.fractions['kept']:.1f} %</li>"
            for r in results
        )
        self.result.value = f"<ul>{rows}</ul>"
