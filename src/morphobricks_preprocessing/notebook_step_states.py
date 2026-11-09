"""What each step of the stepper says about itself: done or not, and one line.

Every line is read from the state the cards already keep (the folder check, the confirmed axis
roles, the draft answers, the channel and mask cards, the published file, the run); nothing is
checked or computed again here, and nothing is changed. A mixin of
:class:`.notebook_ui.PreprocessingNotebook`, used by :mod:`.notebook_steps`.
"""

from __future__ import annotations

from .channel_labels import remaining
from .mask_answers import mask_kinds, masks_unanswered
from .notebook_stepper import StepState
from .notebook_values import size_rows
from .validation import plural

ANSWER_STEPS = range(3, 11)
"""The steps (0-based) whose answers the published file holds: conditions to check and save."""


def _ids(wanted: dict[tuple[str, str], str], kind: str) -> list[str]:
    return [text for (k, _), text in sorted(wanted.items()) if k == kind]


class _StepStates:
    """The :class:`.notebook_stepper.StepState` of each step, from the cards' state."""

    def _step_states(self) -> list:
        """Return the state callables of the 13 steps, in order."""
        return [
            self._images_state,
            self._axes_state,
            self._sizes_state,
            self._conditions_state,
            self._replicates_state,
            self._references_state,
            self._image_type_state,
            self._stains_state,
            self._tags_state,
            self._masks_state,
            self._saved_state,
            self._run_state,
            self._describe_state,
        ]

    def _reopen_changed(self) -> bool:
        """Return True when a published file exists and the folders changed since it was saved."""
        state = self._published() if self.preflight is not None else None
        return state is not None and state.changed

    def _images_state(self) -> StepState:
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
        if not pre.ok:
            text += " · problems to fix"
        return StepState(pre.ok, text)

    def _axes_state(self) -> StepState:
        mapping = self.confirmed_mapping
        if mapping is None:
            return StepState(False, "not confirmed")
        return StepState(True, ", ".join(role for role in mapping.roles if role) + " confirmed")

    def _sizes_state(self) -> StepState:
        if self.preflight is None or not self.preflight.metadata.files:
            return StepState(False, "after the folder check")
        key = (id(self.preflight), self.confirmed_mapping, self.missing_as_singleton.value)
        if getattr(self, "_sizes_key", None) != key:  # the card's own sizes, read once
            shown, roles = self._shown_calibration()
            self._sizes_key, self._sizes_rows = key, size_rows(shown.calibration.sizes, roles)
        rows = self._sizes_rows
        if len(rows) == 1:
            x, y = rows[0][0][:2]
            return StepState(True, f"X {x} · Y {y}")
        return StepState(True, f"{len(rows)} different sets of sizes")

    def _changed(self, index: int, done: bool) -> bool:
        return not done and index in ANSWER_STEPS and self._reopen_changed()

    def _conditions_state(self) -> StepState:
        if self.draft is None:
            return StepState(False, "after the folder check")
        wanted = self._wanted_ids()
        ids = _ids(wanted, "conditions")
        shown = {
            wanted.get(("conditions", folder), ""): answer.display_name
            for folder, answer in self.draft_answers.conditions.items()
            if answer.display_name
        }
        text = ", ".join(f"{i} ({shown[i]})" if i in shown else i or "?" for i in ids)
        done = all(ids)
        return StepState(done, text or "none", self._changed(3, done))

    def _replicates_state(self) -> StepState:
        if self.draft is None:
            return StepState(False, "after the folder check")
        wanted = self._wanted_ids()
        repeats, technical = _ids(wanted, "repeats"), _ids(wanted, "technical_repeats")
        text = ", ".join(i or "?" for i in repeats)
        paired = [
            f"{condition}/{folder} = {wanted.get(('repeats', group)) or group}"
            for (condition, folder), group in sorted(self.draft_answers.pairing.items())
        ]
        if paired:
            text += f" ({'; '.join(paired)})"
        if [i for i in technical if i != "single"]:
            text += " · technical: " + ", ".join(i or "?" for i in technical)
        done = all(repeats) and all(technical)
        return StepState(done, text or "none", self._changed(4, done))

    def _references_state(self) -> StepState:
        if self.draft is None:
            return StepState(False, "after the folder check")
        conditions = sorted(self.draft.conditions)
        if len(conditions) < 2:
            return StepState(True, "one condition: nothing to compare")
        chosen = {c: self._chosen_reference(c) for c in conditions}
        text = " · ".join(f"{c} → {'?' if t is None else t or 'none'}" for c, t in chosen.items())
        done = None not in chosen.values()
        return StepState(done, text, self._changed(5, done))

    def _image_type_state(self) -> StepState:
        if self._channels_key is None:
            return StepState(False, "after the axes")
        types = self.channel_types
        usable = [u for u in self._channel_layouts if u.usable]
        saved = [types.saved(u.key) for u in usable]
        parts = [
            f"{word} ({plural(saved.count(value), 'folder')})"
            for word, value in (("separate channels", "channels"), ("colour", "rgb"))
            if value in saved
        ]
        done = None not in saved
        if not done:
            parts.append(f"{saved.count(None)} to choose")
        return StepState(done, ", ".join(parts), self._changed(6, done))

    def _stains_state(self) -> StepState:
        if self._channels_key is None:
            return StepState(False, "after the axes")
        done = self.channel_labels.confirmed
        text = ", ".join(self.channel_labels.labels) if done else "not confirmed"
        return StepState(done, text, self._changed(7, done))

    def _tags_state(self) -> StepState:
        if self._channels_key is None or not self.channel_labels.confirmed:
            return StepState(False, "after the stains")
        layouts = self._channel_layouts
        left = remaining(layouts, self.draft_answers.units, self.channel_labels.labels)
        done = not left.cells and not left.blocked
        text = (
            f"every channel of {plural(len(layouts), 'folder')} tagged"
            if done
            else f"{left.cells} channel(s) still to tag"
        )
        return StepState(done, text, self._changed(8, done))

    def _masks_state(self) -> StepState:
        types = self._mask_types()
        if not types:
            return StepState(self.preflight is not None, "no masks")
        if self.draft_answers is None:
            return StepState(False, "after the folder check")
        kinds = mask_kinds(self.draft_answers)
        text = " · ".join(f"{t} = {kinds.get(t, '?')}" for t in types)
        done = not masks_unanswered(self.draft_answers, types)
        return StepState(done, text, self._changed(9, done))

    def _saved_state(self) -> StepState:
        if self.draft is None:
            return StepState(False, "after the folder check")
        if self._saved_current():
            state = self._published()
            return StepState(True, f"saved {self._saved_on or state.saved_on}")
        return StepState(False, "not saved yet", self._changed(10, False))

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

    def _describe_state(self) -> StepState:
        if self.mask_step_result.value:
            return StepState(True, "masks described")
        return StepState(False, "optional")


__all__ = ["ANSWER_STEPS", "_StepStates"]
