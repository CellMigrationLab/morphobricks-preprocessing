"""The notebook's inputs start from those the earlier run recorded (audit I4).

A run given no value for an input keeps the one its output folder's manifest records
(design v0.4 §1). The notebook gives the run every input, so its widgets start from the
recorded inputs, worked out by the run's own rule (:func:`.run.planned.recorded_inputs`):
an untouched widget gives the recorded value, and an edit is a change the plan and the
run's report name. The exclusions, the histogram settings and the intensity limits are
filled when the output folder changes (as the design is read again, issue #98); the bin
count and the missing-axes answer with each check, and the image type is preselected in
step 2. An output folder without an earlier run sets back the values filled from another
folder that were not edited. The masks folder and mask axes are kept by leaving them
empty (:mod:`.notebook_masks`).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from morphobricks_normalization.checks import IntensityCheckSettings
from morphobricks_normalization.ranges import HistogramSettings

from . import notebook_style as ui
from .discovery import default_output_folder
from .run.planned import recorded_inputs
from .run.recorded import ResolvedInputs


class _Prefill:
    """Fill steps 1 and 3 from the earlier run in the output folder."""

    def _init_prefill(self) -> None:
        self._recorded: ResolvedInputs | None = None
        self._prefill_for: Path | None = None
        self._prefilled: dict[str, object] = {}
        # The inputs filled at each check that the user changed: a check keeps them.
        self._edited: set[str] = set()
        # The recorded histogram settings without a widget (``min_central_bins``) are kept.
        self._histogram_base = HistogramSettings()

    def _prefill_from_output(self) -> None:
        """Fill the inputs from the earlier run, when the output folder changed."""
        if not hasattr(self, "run_checks"):
            return  # still building the pages
        output = self._prefill_target()
        if output == self._prefill_for:
            return
        self._prefill_for = output
        self._edited = set()
        folder = ui.clean_path(self.folder.value)
        root = Path(folder).resolve() if folder else None
        self._recorded = None if output is None else recorded_inputs(output, root)
        if self._recorded is None:
            self._unfill()
        else:
            self._fill(self._recorded)
        self._prefill_bins()
        self._prefilled = self._prefill_values()

    def _prefill_target(self) -> Path | None:
        """Return the output folder, resolved, or None while no folder is given."""
        folder = ui.clean_path(self.folder.value)
        output = self._output_folder()
        if not folder and not output or self._output_file_in_the_way() is not None:
            return None
        return Path(output or default_output_folder(folder)).resolve()

    def _fill(self, recorded: ResolvedInputs) -> None:
        """Show the recorded exclusions, histogram settings and intensity limits."""
        self.exclude.value = "\n".join(recorded.exclude)
        if recorded.exclude:
            self.exclude_box.selected_index = 0  # an exclusion is never hidden
        histogram = recorded.histogram_settings
        # Ticking the recommended settings sets them back, so it goes first.
        self.recommended.value = histogram == HistogramSettings() and self._recorded_bins() in (
            None,
            getattr(self, "_default_bins", None),
        )
        self._histogram_base = histogram
        self.histogram_range.value = histogram.histogram_range
        self.robust_low.value, self.robust_high.value = histogram.robust_quantiles
        self.run_checks.use(recorded.intensity_settings or IntensityCheckSettings())
        self.verify_content.value = recorded.verify_content

    def _unfill(self) -> None:
        """Set back the values filled from another output folder that were not edited."""
        self._histogram_base = HistogramSettings()
        if not self._prefilled:
            return
        now = self._prefill_values()
        if now["exclude"] == self._prefilled["exclude"]:
            self.exclude.value = ""
        if now["intensity"] == self._prefilled["intensity"]:
            self.run_checks.restore_defaults()
        if now["verify_content"] == self._prefilled["verify_content"]:
            self.verify_content.value = False
        kept = ("recommended", "bins", "histogram")
        unedited = "bins" not in self._edited
        unedited = unedited and all(now[key] == self._prefilled[key] for key in kept)
        if unedited and not now["recommended"]:
            self.recommended.value = True  # sets the bins and the histogram range back

    def _note_edits(self) -> None:
        """Note the inputs filled at a check that were changed since, before checking again."""
        if not self._prefilled:
            return
        now = self._prefill_values()
        if (now["bins"], now["recommended"]) != (
            self._prefilled["bins"],
            self._prefilled["recommended"],
        ):
            self._edited.add("bins")
        if now["singleton"] != self._prefilled["singleton"]:
            self._edited.add("singleton")

    def _prefill_check(self) -> None:
        """Show the recorded bin count after a check, which sets the pixel type's default.

        A bin count or "recommended" choice changed by the user is not filled again: the
        check sets it as it would without an earlier run.
        """
        if "bins" not in self._edited:
            self._prefill_bins()
        if self._prefilled:
            self._prefilled.update(
                bins=self.bins.value,
                recommended=self.recommended.value,
                singleton=self.missing_as_singleton.value,
            )

    def _prefill_bins(self) -> None:
        """Show the recorded bin count, when it was recorded for the checked pixel type."""
        bins = self._recorded_bins()
        if bins is None or bins == getattr(self, "_default_bins", None):
            return
        self.recommended.value = False
        self.bins.value = bins

    def _recorded_bins(self) -> int | None:
        """Return the recorded bin count the run would reuse for the checked images."""
        recorded, pre = self._recorded, self.preflight
        if recorded is None or recorded.bin_count is None or pre is None:
            return None
        dtype = pre.metadata.dtype
        # The run reuses a bin count only for the pixel type it was recorded for.
        if dtype is None or recorded.bins_recorded_for != dtype.name:
            return None
        return recorded.bin_count if self.bins.min <= recorded.bin_count <= self.bins.max else None

    def _recorded_missing_as_singleton(self) -> bool:
        """Return the missing-axes answer the earlier run recorded (False without one).

        Once the user changed it, a check asks again (False), as without an earlier run.
        """
        if "singleton" in self._edited:
            return False
        return self._recorded is not None and self._recorded.missing_as_singleton

    def _recorded_image_type(self, rgb_allowed: bool) -> str | None:
        """Return the image type the earlier run recorded, if it can be chosen now."""
        kind = None if self._recorded is None else self._recorded.image_type
        return None if kind == "rgb" and not rgb_allowed else kind

    def _recorded_after_run(self) -> None:
        """Take the inputs the run just recorded as those of the output folder."""
        if self._prefill_for is not None:
            folder = ui.clean_path(self.folder.value)
            root = Path(folder).resolve() if folder else None
            self._recorded = recorded_inputs(self._prefill_for, root)
            self._prefilled = self._prefill_values()
            self._edited = set()  # what was run is now what is recorded

    def _prefill_values(self) -> dict[str, object]:
        """Return the values of the filled widgets, to tell later which were edited."""
        return {
            "exclude": self.exclude.value,
            "recommended": self.recommended.value,
            "bins": self.bins.value,
            "histogram": (
                self.histogram_range.value,
                self.robust_low.value,
                self.robust_high.value,
            ),
            "intensity": self.run_checks.settings(),
            "singleton": self.missing_as_singleton.value,
            "verify_content": self.verify_content.value,
        }

    def _histogram_from(
        self, histogram_range: str, quantiles: tuple[float, float]
    ) -> HistogramSettings:
        """Return the histogram settings chosen, with the recorded ones without a widget."""
        return replace(
            self._histogram_base, histogram_range=histogram_range, robust_quantiles=quantiles
        )
