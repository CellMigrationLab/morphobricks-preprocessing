"""The strict page's draft, kept beside the draft while fast mode tags units with no click (pure).

*Tag channels* fast mode (owner decisions of 16 November 2026) tags a unit whose header names
every channel with no click; unticking the statement must give back the strict page exactly:
the draft byte-equal to the one the strict page holds after the same user actions. The strict
page's aliases depend on the order of the saves (:func:`.channel_answers.synced` keeps an alias
only while some tagged cell holds it), so no formula over the current draft gives them back.

Instead the strict page's draft is kept (:class:`StrictShadow`, the *shadow*), correct by
construction:

- the shadow starts as the draft just before the first save made with no click
  (:meth:`StrictShadow.hold`); the no-click saves go to the draft only;
- every user save applies the same change to both: a change of the *Your channels* cards is a
  function of the answers it is applied to (:data:`Change`), run on the draft and on the shadow;
  any other answer (names, design, masks, a confirmation) is the same edit, field by field
  (:func:`patched`);
- unticking writes the shadow back (:meth:`StrictShadow.restore`) and drops it.

A unit the user clicked on in fast mode has, in the shadow too, the tags the click saved for
it: a change that tags units sets their whole tags (:func:`.channel_answers.tagged`), so a hand
tag is never taken back. Nothing here reads a file or a widget.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import fields, is_dataclass, replace
from typing import cast

from .draft_answers import DraftAnswers

Change = Callable[[DraftAnswers], DraftAnswers]
"""A user's change of the answers: the same function is run on the draft and on the shadow."""

Settle = Callable[[DraftAnswers, DraftAnswers], DraftAnswers | None]
"""What a save does to new answers given the ones before (the notebook's reask and check):
the settled answers, or None when the strict page would refuse them."""


def patched(before: object, after: object, target: object) -> object:
    """Return ``target`` with the edit that made ``after`` from ``before``, field by field.

    Dataclass fields and mapping entries are compared one by one: what ``after`` changed is
    set in ``target``, what it removed is removed, and everything else of ``target`` is kept.
    Any other value (a text, a tuple of tags, None) is replaced whole when it changed.
    """
    if before == after:
        return target
    if is_dataclass(after) and type(before) is type(after) is type(target):
        names = [f.name for f in fields(after) if getattr(before, f.name) != getattr(after, f.name)]
        changed = {
            n: patched(getattr(before, n), getattr(after, n), getattr(target, n)) for n in names
        }
        return replace(target, **changed)
    if isinstance(before, Mapping) and isinstance(after, Mapping) and isinstance(target, Mapping):
        out = dict(target)
        for key in before.keys() - after.keys():
            out.pop(key, None)
        for key, value in after.items():
            if key not in before:
                out[key] = value
            elif before[key] != value:
                out[key] = patched(before[key], value, out[key]) if key in out else value
        return out
    return after


class StrictShadow:
    """The strict page's draft while it differs from the draft (None: they are the same).

    One per notebook session; never saved. The draft's owner calls :meth:`hold` before a save
    made with no click, :meth:`follow` after every other save, and :meth:`restore` to untick.
    """

    def __init__(self) -> None:
        self.draft: DraftAnswers | None = None

    def strict(self, answers: DraftAnswers) -> DraftAnswers:
        """Return the strict page's draft when the draft is ``answers``."""
        return answers if self.draft is None else self.draft

    def hold(self, before: DraftAnswers) -> None:
        """Note a save with no click: the strict page keeps ``before`` (the draft until then)."""
        if self.draft is None:
            self.draft = before

    def follow(
        self,
        before: DraftAnswers,
        after: DraftAnswers,
        change: Change | None,
        settle: Settle | None = None,
    ) -> None:
        """Apply to the shadow the user's save that made ``after`` from ``before``.

        ``change`` is the save's function, run on the shadow as it was run on the draft; None
        means the same edit (:func:`patched`). ``settle`` is what the save itself does to new
        answers (a reask, a check); a change the strict page would refuse (``ValueError``, or
        ``settle`` returning None) leaves the shadow as it is, as that page would.
        """
        if self.draft is None:
            return
        old = self.draft
        try:
            new = cast(DraftAnswers, patched(before, after, old)) if change is None else change(old)
        except ValueError:
            return
        settled = new if settle is None else settle(old, new)
        if settled is not None:
            self.draft = settled

    def restore(self) -> DraftAnswers | None:
        """Return the strict page's draft to write back (None: the draft is already it); drop it."""
        draft, self.draft = self.draft, None
        return draft

    def forget(self) -> None:
        """Drop the shadow: the draft was replaced as a whole (a new folder, another draft)."""
        self.draft = None


__all__ = ["Change", "Settle", "StrictShadow", "patched"]
