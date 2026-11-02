"""Step 3 of the notebook: "What will happen" before the run (PP-10).

The plan names the images to analyse, the files written, the planes read in blocks, the
mode and its effect, and the inputs that differ from the earlier run in the output folder,
with their effect, from the run's own function (:func:`.run.planned.planned_changes`).
:meth:`_RunPlan._given_inputs` is the one place the notebook's inputs to the run are
gathered, for the plan and for the run.
"""

from __future__ import annotations

import html
from pathlib import Path

from morphobricks_normalization.csv_io import OUTPUT_FILENAME

from . import notebook_style as ui
from .axes import AxisReview
from .discovery import default_output_folder
from .discovery_report import REPORT_TEXT_FILENAME
from .manifest import MANIFEST_FILENAME, ManifestFormatError, read_manifest
from .notebook_options import _PLAN_PATHS, IMAGE_TYPE_NAMES
from .notebook_render import change_lines
from .planes import PlaneLayout, PlaneReadError, format_bytes, plane_layout
from .run import FULL
from .run.planned import planned_changes
from .run.recorded import GivenInputs, InputChange
from .validation import plural


class _RunPlan:
    """Step 3's "What will happen" card, and the inputs given to the run."""

    def _plan_html(self, review: AxisReview | None = None) -> str:
        """Describe what the run will do: images, files, memory, mode and input changes."""
        folder = ui.clean_path(self.folder.value)
        if self.preflight is None or not folder:
            return "<div class='mb-help'>Check the experiment folder in step 1 first.</div>"
        output = Path(self._output_folder() or default_output_folder(folder))
        lines = self._plan_input_lines(output)
        # Which images are read in blocks depends on the axis roles, so it is only
        # worked out once they are confirmed (``review`` is None until then).
        blocks_line, over_card = self._memory_plan(review) if review is not None else ("", "")
        if blocks_line:
            lines.append(blocks_line)
        lines.extend(self._plan_mode_lines(output))
        return "<ul>" + "".join(f"<li>{line}</li>" for line in lines) + "</ul>" + over_card

    def _plan_input_lines(self, output: Path) -> list[str]:
        """Describe the images to analyse and the files written (HTML lines)."""
        files = self.preflight.metadata.files
        excluded = self.preflight.excluded
        lines = [
            f"<b>{plural(len(files), 'image')}</b> will be analysed"
            + (f" ({len(excluded):,} excluded)." if excluded else "."),
            f"Output folder: <b>{html.escape(str(output))}</b>",
            f"Files written: <b>{OUTPUT_FILENAME}</b> (one row per plane, its channels in "
            f"columns), <b>{MANIFEST_FILENAME}</b> (what was read, with which settings) and "
            f"the report, <b>{REPORT_TEXT_FILENAME}</b>.",
        ]
        if self.confirmed_image_type is not None:
            lines.append(
                f"Image type: <b>{IMAGE_TYPE_NAMES[self.confirmed_image_type]}</b>, recorded in "
                "the manifest for later bricks (the histograms are the same for either type)."
            )
        zarrs = sum(f.is_ome_zarr for f in files)
        if zarrs:
            lines.append(
                f"<b>{plural(zarrs, 'OME-Zarr image')}</b> will be read directly, with the axes "
                f"{'its' if zarrs == 1 else 'their'} metadata declares."
            )
        return lines

    def _plan_mode_lines(self, output: Path) -> list[str]:
        """Describe the mode, the earlier run and the inputs that differ from it (HTML lines)."""
        manifest = output / MANIFEST_FILENAME
        if self.mode.value == FULL:
            lines = [
                "Mode <b>full</b>: every image is read again and every row and check is "
                "recomputed; no row of an earlier run is reused."
            ]
        elif manifest.exists():
            lines = [self._earlier_run_line(manifest)]
        else:
            lines = ["No earlier run was found: every image will be read (mode <b>restart</b>)."]
        changes = change_lines(self._planned_changes(output))
        if changes:
            lines.append(
                "Inputs that differ from those of the earlier run, used by this run and named "
                "in its report:<ul>" + "".join(f"<li>{line}</li>" for line in changes) + "</ul>"
            )
        lines.append(
            "Your confirmations are kept in either mode. The table and manifest are written "
            "only when the whole run succeeds; until then the earlier results stay as they "
            "are. The report is written either way."
        )
        return lines

    def _earlier_run_line(self, manifest: Path) -> str:
        """Describe the earlier run a ``restart`` builds on."""
        try:
            earlier = read_manifest(manifest)
        except ManifestFormatError:
            return "An earlier run was found but cannot be read; it will be replaced."
        return (
            f"An earlier run was found ({plural(len(earlier.files), 'image')}, "
            f"{html.escape(earlier.written_at or 'unknown date')}). Mode <b>restart</b>: only "
            "new or changed images will be read; if the bin count, histogram range or axis "
            "roles changed, every image is read again."
        )

    def _planned_changes(self, output: Path) -> tuple[InputChange, ...]:
        """Return the input changes the run would report, from the run's own functions."""
        if not output.is_dir():
            return ()
        return planned_changes(
            self._given_inputs(),
            output,
            design=self.design,
            calibration=self._shown_calibration()[0].calibration,
        )

    def _given_inputs(self) -> GivenInputs:
        """Return the inputs the run is given: one source for the run and its plan."""
        return GivenInputs(
            experiment_root=Path(ui.clean_path(self.folder.value)).resolve(),
            mapping=self.confirmed_mapping,
            exclude=tuple(self._excluded()),
            image_type=self.confirmed_image_type,
            bin_count=self.bins.value or None,
            missing_as_singleton=self.missing_as_singleton.value,
            intensity_settings=self.run_checks.settings(),
            histogram_settings=self._histogram_settings_or_none(),
        )

    def _plan_layouts(self, review: AxisReview) -> dict[str, PlaneLayout]:
        """Return how each image's planes are read, from the headers only.

        Opening every header takes a moment on large experiments, and this runs on
        every settings edit, so the layouts are kept until the checked folder, the
        axis roles, the memory limit or the bin count change.
        """
        bins = self.bins.value or None
        histogram = self._histogram_settings_or_none()
        key = (
            self.confirmed_mapping,
            self.missing_as_singleton.value,
            self._memory_limit(),
            bins,
            histogram,
        )
        cached = self._layouts_cache
        # A new check of the folder gives a new Preflight, even for the same folder.
        if cached is not None and cached[0][0] is self.preflight and cached[0][1:] == key:
            return cached[1]
        root = self.preflight.discovery.root
        layouts = {}
        for rel, mapping in sorted(review.file_mappings.items()):
            try:
                layouts[rel] = plane_layout(
                    root / rel,
                    mapping,
                    memory_limit=self._memory_limit(),
                    bin_count=bins,
                    histogram_settings=histogram,
                )
            except PlaneReadError:
                continue  # the run reports these images as unsupported_layout
        self._layouts_cache = ((self.preflight, *key), layouts)
        return layouts

    def _memory_plan(self, review: AxisReview) -> tuple[str, str]:
        """Return the "read in blocks" bullet and the "cannot be read" card, if any."""
        layouts = self._plan_layouts(review)
        limit = format_bytes(self._memory_limit())
        blocked = {rel: lay for rel, lay in layouts.items() if lay.fits and lay.in_blocks}
        over = {rel: lay for rel, lay in layouts.items() if not lay.fits}
        line = card = ""
        if blocked:
            largest = format_bytes(max(lay.memory_estimate for lay in blocked.values()))
            line = (
                f"<b>{plural(len(blocked), 'image')}</b> {'has' if len(blocked) == 1 else 'have'} "
                f"planes too large to read whole within the memory limit of {limit} (they need "
                f"up to {largest}), so they will be read in blocks. The histograms are exactly the "
                "same; reading takes longer. To read them whole, raise the memory limit to "
                f"{largest} or more in <b>Settings</b> above (untick <b>Use the recommended "
                "settings</b>), "
                "if this computer has enough memory."
                + ui.path_list(list(blocked), limit=_PLAN_PATHS)
            )
        if over:
            need = format_bytes(max(lay.min_memory_estimate for lay in over.values()))
            card = ui.status(
                "error",
                f"{plural(len(over), 'image')} cannot be read within the memory limit; "
                "the run will stop.",
                [
                    f"The smallest part of a plane that can be read on its own needs up to "
                    f"{need}, more than the limit of {limit}. A plane stored compressed in "
                    "one piece has to be decoded whole."
                    + ui.path_list(list(over), limit=_PLAN_PATHS),
                    f"<b>What to do:</b> raise the memory limit to {need} or more in "
                    "<b>Settings</b> above, if this computer has enough memory, or exclude "
                    "these images in step 1. "
                    "Saving them again as tiled or uncompressed TIFF files, or as OME-Zarr, "
                    "also works.",
                ],
                raw=True,
            )
        return line, card
