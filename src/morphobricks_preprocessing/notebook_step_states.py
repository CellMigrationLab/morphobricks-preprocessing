"""What each step of the stepper says about itself: done or not, and one line.

Every line is read from the state the cards already keep (the folder check, the confirmed axis
roles, the draft answers, the channel and mask cards, the published file, the run); nothing is
checked or computed again here, and nothing is changed. A mixin of
:class:`.notebook_ui.PreprocessingNotebook`, used by :mod:`.notebook_steps`.
"""

from __future__ import annotations

from .channel_labels import remaining
from .description_reconcile import CHANNELS_CHANGED
from .mask_answers import mask_kinds, masks_unanswered
from .notebook_stepper import StepState
from .notebook_values import natural_key, size_rows
from .validation import plural

UNCALIBRATED = ("uncalibrated", "pixels (stated)")
"""The X/Y size texts (:func:`.notebook_values.size_text`) that give no physical size."""
ANSWER_STEPS = range(1, 6)
"""The steps (0-based) whose answers the published file holds: channels (the image type) to save."""
TYPE_WORDS = (("separate channels", "channels"), ("colour", "rgb"))


def _ids(wanted: dict[tuple[str, str], str], kind: str) -> list[str]:
    """Return the IDs of one kind in natural order of their folder (``rep2`` before ``rep10``)."""
    found = sorted(((natural_key(f), t) for (k, f), t in wanted.items() if k == kind))
    return [text for _, text in found]


class _StepStates:
    """The :class:`.notebook_stepper.StepState` of each step, from the cards' state."""

    def _step_states(self) -> list:
        """Return the state callables of the 7 steps, in order."""
        return [
            self._images_state,
            self._axes_state,
            self._channels_state,
            self._masks_state,
            self._design_state,
            self._saved_state,
            self._run_state,
        ]

    def _reopen_changed(self) -> bool:
        """Return True when a published file exists and the folders changed since it was saved."""
        state = self._published() if self.preflight is not None else None
        return state is not None and state.changed

    def _images_state(self) -> StepState:
        """Return step 1: images and folders found, the masks, and changes since the save."""
        pre = self.preflight
        if pre is None:
            return StepState(False, "not checked yet")
        text = (
            f"{plural(len(pre.discovery.images), 'image')} · "
            f"{plural(len(pre.discovery.leaves), 'folder')}"
        )
        types = self._mask_types()
        if types:
            text += " · masks " + ", ".join(types)
        state = self._published()
        if state is not None and state.changed:
            rec = state.reconciliation
            text += f" · since saved: {len(rec.new)} new, {len(rec.removed)} gone"
            headers = sum(CHANNELS_CHANGED in u.reasons for u in rec.unchanged)
            if headers:
                text += f", {plural(headers, 'folder')} with changed headers"
        if not pre.ok:
            text += " · problems to fix"
        return StepState(pre.ok, text)

    def _axes_state(self) -> StepState:
        """Return step 2: the confirmed roles and the pixel size.

        Done once the roles are confirmed (the image type is on Channels since 12 November
        2026); the chip notes an uncalibrated experiment.
        """
        mapping = self.confirmed_mapping
        sizes, note = self._sizes_words()
        if mapping is None:
            return StepState(False, "not confirmed")
        roles = ", ".join(role for role in mapping.roles if role) + " confirmed"
        text = " · ".join(part for part in (roles, sizes) if part)
        return StepState(True, text, note=note)

    def _image_type_words(self) -> tuple[str, bool]:
        """Return the image types answered, by kind, and whether none is left to answer."""
        if self._channels_key is None:
            return "image type: after the folder check", False
        types = self.channel_types
        saved = [types.saved(u.key) for u in self._channel_layouts if u.usable]
        parts = [
            f"{word} ({plural(saved.count(value), 'folder')})"
            for word, value in TYPE_WORDS
            if value in saved
        ]
        done = None not in saved
        if not done:
            parts.append(f"image type: {saved.count(None)} to choose")
        return ", ".join(parts), done

    def _sizes_words(self) -> tuple[str, str]:
        """Return the pixel sizes as the calibration card shows them, and the chip's note."""
        if self.preflight is None or not self.preflight.metadata.files:
            return "", ""
        key = (id(self.preflight), self.confirmed_mapping, self.missing_as_singleton.value)
        if getattr(self, "_sizes_key", None) != key:  # the card's own sizes, read once
            shown, roles = self._shown_calibration()
            self._sizes_key, self._sizes_rows = key, size_rows(shown.calibration.sizes, roles)
        rows = self._sizes_rows
        bare = [r for r in rows if all(t in UNCALIBRATED for t in r[0][:2])]
        if len(bare) == len(rows):
            stated = all(t == "pixels (stated)" for r in rows for t in r[0][:2])
            why = "stated as pixels" if stated else "no pixel size in the headers or stated"
            return f"uncalibrated (pixels): {why}", "uncalibrated"
        note = "partly uncalibrated" if bare else ""
        if len(rows) == 1:
            x, y = rows[0][0][:2]
            return f"X {x} · Y {y}", note
        text = f"{len(rows)} different sets of sizes"
        if bare:
            text += f", {len(bare)} of them uncalibrated"
        return text, note

    def _changed(self, index: int, done: bool) -> bool:
        return not done and index in ANSWER_STEPS and self._reopen_changed()

    def _design_state(self) -> StepState:
        """Return step 5: the conditions, the replicates and the comparisons, once confirmed."""
        if self.draft is None:
            return StepState(False, "after the folder check")
        wanted = self._wanted_ids()
        conditions = sorted(self.draft.conditions)
        chosen = {c: self._chosen_reference(c) for c in conditions}
        named = []
        for folder in conditions:
            text = wanted.get(("conditions", folder)) or "?"
            target = chosen[folder]
            named.append(f"{text} → {wanted.get(('conditions', target)) or target}"
                         if target else text)  # fmt: skip
        repeats, technical = _ids(wanted, "repeats"), _ids(wanted, "technical_repeats")
        text = f"{', '.join(named)} × {', '.join(i or '?' for i in repeats)}"
        paired = [
            f"{condition}/{folder} = {wanted.get(('repeats', group)) or group}"
            for (condition, folder), group in sorted(self.draft_answers.pairing.items())
        ]
        if paired:
            text += f" ({'; '.join(paired)})"
        named_folders = [i or "?" for i in technical if i != "single"]
        if named_folders:
            text += " · technical: " + ", ".join(named_folders)
        done = self._design_confirmed()
        if not done:
            text += " · not confirmed"
        return StepState(done, text, self._changed(4, done))

    def _channels_state(self) -> StepState:
        """Return step 3: the image type, the stains named and every channel tagged."""
        if self._channels_key is None:
            return StepState(False, "after the axes")
        types, typed = self._image_type_words()
        if not typed:
            return StepState(False, types, self._changed(2, False))
        layouts = self._channel_layouts
        if self._stains_not_applicable:
            stains = "no stains to name: every folder is colour (RGB)"
        elif self.channel_labels.confirmed:
            stains = ", ".join(self.channel_labels.labels)
        else:
            return StepState(False, f"{types} · stains not confirmed", self._changed(2, False))
        left = remaining(layouts, self.draft_answers.units, self.channel_labels.labels)
        done = not left.cells and not left.blocked
        tags = (
            f"every channel of {plural(len(layouts), 'folder')} tagged"
            if done
            else f"{left.cells} channel(s) still to tag"
        )
        return StepState(done, f"{types} · {stains} · {tags}", self._changed(2, done))

    def _masks_state(self) -> StepState:
        """Return step 4: what each mask type is, and whether the relationships are described."""
        types = self._mask_types()
        if not types:
            return StepState(self.preflight is not None, "no masks")
        if self.draft_answers is None:
            return StepState(False, "after the folder check")
        kinds = mask_kinds(self.draft_answers)
        text = " · ".join(f"{t} = {kinds.get(t, '?')}" for t in types)
        if self.mask_step_result.value:
            text += " · relationships described"
        done = not masks_unanswered(self.draft_answers, types)
        return StepState(done, text, self._changed(3, done))

    def _saved_state(self) -> StepState:
        if self.draft is None:
            return StepState(False, "after the folder check")
        if self._saved_current():
            state = self._published()
            return StepState(True, f"saved {self._saved_on or state.saved_on}")
        return StepState(False, "not saved yet", self._changed(5, False))

    def _run_state(self) -> StepState:
        result = self.result
        if result is None:
            return StepState(False, "not run yet")
        if not result.written:
            return StepState(False, "stopped: see the messages")
        warnings = [i for i in result.issues if not i.is_error]
        return StepState(
            True, f"{plural(result.plane_count, 'plane')} · {plural(len(warnings), 'warning')}"
        )


__all__ = ["ANSWER_STEPS", "UNCALIBRATED", "_StepStates"]
