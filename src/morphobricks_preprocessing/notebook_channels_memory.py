"""*Tag channels*: this session's memory of the draft's tag review (refusals, confirmations).

Part of :class:`.notebook_channels_repeat.RepeatRows`. The draft file's ``tag_review``
(:mod:`.repeat_review`) is the record of the units a repeat folder's row refused and of the
confirmations the user gave them; the rows keep it in memory to read and redraw it. Memory
follows the draft, never the other way round:

- a write that set the whole review is taken at once (:meth:`ReviewMemory.adopt`);
- a notebook opened on the draft takes the draft's (:meth:`ReviewMemory.restore`);
- a review is written only from memory that holds the draft's review as last seen
  (:meth:`ReviewMemory.persist`).

External re-check as (24 November 2026): a write that set the review could succeed and the
kernel stop before memory took it. Memory was then behind the draft, and reopening *Tag
channels* in the same session reconciled and wrote that stale memory over the saved review,
erasing a refusal or a confirmation. Memory now remembers the review it last took from or wrote
into the draft (:attr:`ReviewMemory.seen`); when the draft holds another one, memory takes the
draft's again (:meth:`ReviewMemory.sync`) before anything reads the refusals or writes a review.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, NamedTuple

from .draft_answers import ChannelAnswer
from .repeat_review import Override, RefusedUnit, TagReview

if TYPE_CHECKING:
    from .channel_labels import UnitLayout
    from .draft_answers import UnitKey
    from .notebook_channels_grid import TagGrid
    from .notebook_channels_override import OverrideEditor


class Refusal(NamedTuple):
    """A unit a row did not fill: the contradiction, the unit, the row's reference and tags."""

    text: str
    layout: UnitLayout
    lead: UnitLayout | None
    """The row's reference unit; None for a unit whose images disagree (:func:`repeat_apart`)."""
    tags: tuple[str, ...]
    """The row's tags at the click (what the header was compared with)."""
    channels: Mapping[str, ChannelAnswer]
    """The labels and aliases when the unit was first refused: what its header is read against.

    Aliases saved afterwards, by confirming or tagging other units, are not evidence for this
    unit, and neither is a changed image header: only an action on this unit releases the
    refusal (:meth:`.RepeatRows._released`).
    """


class ReviewMemory:
    """The refusals and confirmations of the rows, kept as the draft's ``tag_review`` holds them."""

    grid: TagGrid
    refused: dict[UnitKey, Refusal]
    override: OverrideEditor
    pending: dict
    notice: str
    draft: object
    unsaved: bool
    staged: dict[UnitKey, Refusal] | None
    seen: TagReview | None
    """The draft's review as memory last took it or wrote it; None before the first restore."""

    def persist(self) -> bool:
        """Keep the refusals and confirmations in the draft file, when they changed.

        A new notebook on the draft restores them (:meth:`restore`): a refused unit counts only
        once that unit is confirmed, whatever the session. A refused save is not silent (the
        names card says "Not saved." and the grid shows a line, :attr:`unsaved`) and is retried
        at the next persist, because the file still differs from memory; the record stays in
        memory meanwhile, which is what this session reads. The record is only ever written into
        the draft it came from: with another draft open nothing is written. Memory behind the
        draft takes the draft's review first (:meth:`sync`): it is never written over it.
        Returns False when the review could not be saved.
        """
        self.sync()
        ok = self._write(self._review())
        self.unsaved = not ok
        return ok

    def _write(self, review: TagReview) -> bool:
        """Write the whole tag review into the draft when it differs; False when refused.

        Only over the review memory saw (:attr:`seen`): a draft that holds another one is not
        written, and memory takes it (:meth:`sync`).
        """
        if self.other_draft():
            return False  # memory is another draft's: never written into this one
        if self.sync():
            return False  # built from memory behind the draft: never written over it
        if self.saved(review):
            return True
        ok = self.grid.store.update(replace(self.grid.store.answers(), tag_review=review))
        if ok:
            self.seen = self._saved_review()
        return ok

    def saved(self, review: TagReview | None = None) -> bool:
        """Return True when the draft holds ``review`` (memory's by default): else no publish."""
        return self._saved_review() == (review or self._review())

    def _saved_review(self) -> TagReview:
        """Return the review the draft holds now (an empty one when it has none)."""
        return self.grid.store.answers().tag_review or TagReview()

    def adopt(self) -> None:
        """Take the confirmations a successful tag write just saved, as the draft now holds them.

        Called by :meth:`.TagGrid.commit` right after a write that set the whole tag review,
        before any other write, redraw or *Check and save* refresh: the check reads the very
        write's confirmations, never the ones before it. That write held the whole review
        (:attr:`unsaved`); after a row click it also held the click's refusals (:attr:`staged`),
        taken here.
        """
        if self.staged is not None:  # a row click's refusals, saved by this very write
            self.refused, self.staged = self.staged, None
        review = self._saved_review()
        self.override.confirmed = {k: v for k, v in review.confirmed.items() if k in self.refused}
        self.seen, self.unsaved = review, False

    def sync(self) -> bool:
        """Take the draft's review again when it is not the one memory last saw; True if taken.

        That happens only when a write that set the review succeeded and the kernel stopped
        before memory took it (:meth:`adopt`): memory is then behind the draft and is never a
        source of what is listed, held out or written. Nothing while memory is another draft's
        (:meth:`restore` takes that one), before the first restore, or during a row click's
        write (:attr:`staged`, taken by :meth:`adopt`).
        """
        if self.seen is None or self.staged is not None or self.other_draft():
            return False
        review = self._saved_review()
        if review == self.seen:
            return False
        self._take(review, self.grid.layouts)
        return True

    def other_draft(self) -> bool:
        """Return True when the draft open now is not the one the record in memory belongs to."""
        return self.draft_id() != self.draft

    def draft_id(self) -> object:
        """Return which draft the store holds (None for a store that cannot tell)."""
        which = getattr(self.grid.store, "draft_id", None)
        return which() if which is not None else None

    def _forget(self) -> None:
        """Drop every refusal, confirmation and editor state: they belong to another draft."""
        self.refused, self.pending, self.notice, self.unsaved = {}, {}, "", False
        self.seen = None
        self.override.confirmed = {}
        self.override.editing, self.override.drafts = None, {}

    def _review(
        self,
        confirmed: Mapping[UnitKey, Override] | None = None,
        refusals: Mapping[UnitKey, Refusal] | None = None,
    ) -> TagReview:
        """Return what is refused and confirmed now, as the draft file keeps it.

        ``confirmed`` and ``refusals`` replace the memory (the record a click is about to save
        together with its tags, :meth:`.OverrideEditor._confirm` and :meth:`.RepeatRows.apply`).
        """
        refused = {
            key: RefusedUnit(
                old.tags,
                None if old.lead is None else old.lead.key,
                {k: tuple(a.header_names) for k, a in old.channels.items() if a.header_names},
            )
            for key, old in (self.refused if refusals is None else refusals).items()
        }
        return TagReview(refused, dict(self.override.confirmed if confirmed is None else confirmed))

    def restore(self, layouts: Sequence[UnitLayout]) -> None:
        """Take the refusals and confirmations of the draft file (a notebook opened on it).

        Run before the headers are trusted and the refusals reconciled
        (:meth:`.RepeatRows.reconcile` brings each up to date with the layouts, as after any
        re-check). The memory of the same draft is kept while the draft holds the review memory
        last saw (memory is the draft's, or ahead of it after a refused write, retried later);
        a draft that holds another review is taken (:meth:`sync`). Another draft (the notebook
        pointed at another folder or output) forgets the memory first and is restored from its
        own file, empty when it has no review. A unit that is gone is not restored; a reference
        that is gone is replaced by the current one at the next reconcile.
        """
        if self.other_draft():
            self._forget()
            self.draft = self.draft_id()
        review = self._saved_review()
        if self.refused and review == self.seen:
            return
        self._take(review, layouts)

    def _take(self, review: TagReview, layouts: Sequence[UnitLayout]) -> None:
        """Replace the memory by ``review``, for the units of ``layouts``.

        A refusal memory holds exactly as the review records it is kept as it is (its reason and
        the aliases of its time are not in the file).
        """
        current, refused, mine = {u.key: u for u in layouts}, {}, self._review().refused
        for key, saved in review.refused.items():
            if key in current and mine.get(key) == saved:
                refused[key] = self.refused[key]
            elif (layout := current.get(key)) is not None:
                lead = layout if saved.reference is None else current.get(saved.reference, layout)
                aliases = {k: ChannelAnswer(None, v) for k, v in saved.aliases.items()}
                refused[key] = Refusal(
                    "", layout, None if saved.reference is None else lead, saved.tags, aliases
                )
        self.refused, self.seen = refused, review
        self.override.confirmed = {k: v for k, v in review.confirmed.items() if k in refused}


__all__ = ["Refusal", "ReviewMemory"]
