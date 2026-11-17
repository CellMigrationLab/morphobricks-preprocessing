"""*Tag channels* fast mode: unticking gives back the strict page's draft, byte for byte.

Owner rules of 16 November 2026: fast mode is on by default and trusts headers with no click;
**unticking restores the strict page exactly** (the draft byte-equal to the strict page's after
the same user actions); ticking again trusts only the aliases still saved; a hand tag is never
taken back. The strict page's draft is kept beside the draft (:mod:`.strict_draft`): every user
save applies the same change to both, the no-click tags go to the draft only.

The property test drives two pages over the same units, with in-memory stores: the *fast* page
(ticked on opening) and the *strict* page (unticked from the start), with random sequences of
the actions both pages have: tick, untick, click a cell, save the label list, add or remove a
label, answer an image type, *Use for all folders*, re-check (new header names, a new unit)
and reopen (a new session on the saved draft, with stale aliases). The actions call the
handlers the widgets call, with the tag grid not drawn (``light_refresh``) so that a few
hundred sequences take seconds. A click on a unit in fast mode makes its whole tags the user's
(a hand tag is never taken back): on the strict page, the same user tags that unit by hand to
the tags the click saved, clicking each cell it changes. The fast page's own action (the row of
tags) is mirrored the same way. Whenever the fast page is unticked, its draft must be
byte-equal to the strict page's.
"""

import random
from dataclasses import replace

import pytest

from morphobricks_preprocessing.channel_answers import synced, with_tags
from morphobricks_preprocessing.channel_labels import UnitLayout, tags_of
from morphobricks_preprocessing.channel_matrix import REFUSED, matrix_rows
from morphobricks_preprocessing.draft_answers import ChannelAnswer, DraftAnswers, UnitAnswer
from morphobricks_preprocessing.draft_format import SPEC_COMMIT, answers_to_bytes
from morphobricks_preprocessing.strict_draft import StrictShadow, patched

pytest.importorskip("ipywidgets")

from morphobricks_preprocessing.notebook_channels_grid import TagGrid  # noqa: E402
from morphobricks_preprocessing.notebook_channels_labels import LabelsCard  # noqa: E402
from morphobricks_preprocessing.notebook_channels_type import (  # noqa: E402
    ImageTypeColumn,
    ImageTypeRows,
)
from morphobricks_preprocessing.notebook_image_type_line import ImageTypeLine  # noqa: E402

MISSING = object()
SEEDS = range(250)
STEPS = 14
LABELS = ("DNA", "ACTIN", "PHASE")
EXTRA = "X"
EXACT = ("DNA", "ACTIN", "PHASE")
ALIAS = ("DAPI", "Actin", "Ch3")


def _unit(key, names, channels=3, rgb=False):
    names = (None,) * channels if names is None else tuple(names)
    return UnitLayout(key, "a.tif", channels, names, rgb, images=1)


def _layouts(variant=0, added=False):
    """The units: exact names, saved aliases, none, another order, two channels, colour."""
    renamed = variant % 3
    units = [
        _unit(("a", "r1", "t1"), EXACT),
        _unit(("a", "r1", "t2"), ALIAS),
        _unit(("a", "r2", "t1"), (None, ALIAS, EXACT)[renamed]),
        _unit(("b", "r1", "t1"), ("ACTIN", "DNA", "PHASE")),
        _unit(("b", "r1", "t2"), (ALIAS, None, ALIAS)[renamed]),
        _unit(("b", "r2", "t1"), ("DNA", "ACTIN"), channels=2),
        _unit(("b", "r2", "t2"), None, rgb=True),
    ]
    if added:
        units.append(_unit(("c", "r1", "t1"), ALIAS))
    return tuple(units)


def _start(stale):
    """Return the saved answers a sequence starts from: labels only, or stale aliases."""
    names = {"DNA": ("DAPI", "Hoechst"), "ACTIN": ("Actin",), "PHASE": ("Ch3",)}
    if not stale:
        names = {}
    channels = {label: ChannelAnswer(header_names=names.get(label)) for label in LABELS}
    units = {}
    if names:  # a unit saved by hand whose header no longer names its channels
        units[("a", "r2", "t1")] = UnitAnswer(("DNA", "ACTIN", "PHASE"), "channels")
    return DraftAnswers("absent", SPEC_COMMIT, units=units, channels=channels)


class Store:
    """The answers in memory, with the strict page's draft as the notebook keeps it."""

    def __init__(self, answers):
        self.saved, self.shadow = answers, StrictShadow()

    def answers(self):
        return self.saved

    def update(self, new):
        before, self.saved = self.saved, new
        self.shadow.follow(before, new, None)
        return True

    def change(self, change):
        before = self.saved
        self.saved = change(before)
        self.shadow.follow(before, self.saved, change)
        return True

    def unattended(self, new):
        self.shadow.hold(self.saved)
        self.saved = new
        return True

    def carry(self, keys):
        """Drop the answers of the units gone, from both drafts (a new check of the folders)."""
        for name in ("saved", "draft"):
            owner = self if name == "saved" else self.shadow
            answers = getattr(owner, name)
            if answers is not None:
                units = {k: v for k, v in answers.units.items() if k in keys}
                setattr(owner, name, replace(answers, units=units))

    def restore_strict(self):
        strict = self.shadow.restore()
        if strict is not None:
            self.saved = strict
        return True

    def refusal(self):
        return ""

    def used_channels(self):
        return ()


class Page:
    """The *Your channels* cards over one store, wired as the notebook wires them."""

    def __init__(self, answers, layouts, ticked):
        self.store = Store(answers)
        self.layouts = layouts
        self.labels = LabelsCard(self.store, self._labels_changed)
        self.types = ImageTypeColumn(self.store, lambda: self.grid.refresh())
        self.grid = TagGrid(self.store, self.types)
        self._line = None
        if not ticked:
            self.grid.same_channels.value = False
        self.recheck(layouts)

    def recheck(self, layouts):
        """Open the cards for these units, as a new check of the folders does."""
        self.layouts = layouts
        self.labels.open(3, list(ALIAS), layouts)
        self._labels_changed()

    def _labels_changed(self):
        if self.labels.confirmed:
            self.grid.open(self.layouts, self.labels.labels, None)
        else:
            self.grid.close()

    @property
    def line(self):
        """The image-type line (*Use for all folders*), built when first used."""
        if self._line is None:
            self._line = ImageTypeLine(self.types, ImageTypeRows(self.types))
        return self._line

    @property
    def draft(self):
        return answers_to_bytes(self.store.saved)

    def tags(self, key):
        layout = next(u for u in self.layouts if u.key == key)
        return tags_of(layout, self.store.saved.units.get(key))

    def set_labels(self, labels):
        card = self.labels
        card.confirm.button.click()  # change the labels
        while len(card.rows) < len(labels):
            card.add_button.click()
        for row, text in zip(card.rows, [*labels, *[""] * len(card.rows)], strict=False):
            row.id_box.value = text
        card.confirm.button.click()

    def layout(self, key):
        return next(u for u in self.layouts if u.key == key)

    def shown(self, key, index):
        """Return what a unit's dropdown at ``index`` shows, or MISSING when it has none.

        Ticked, a unit of a layout shows its saved tags (none when refused), a unit tagged on
        its own what :meth:`TagGrid.effective` shows; unticked, the unit is split from its
        block (as the user does to tag it on its own) and shows the same.
        """
        grid, layout = self.grid, self.layout(key)
        if grid.colour(layout):
            return MISSING
        tag = grid.effective(layout)[0][index]
        if grid.same_channels.value:
            groups, _ = grid.matrix.groups()
            group = next((g for g in groups if layout in g), None)
            if group is not None:
                template = grid.matrix.template(group)
                rows = matrix_rows(group, self.store.saved, grid.labels, template)
                if next(r for r in rows if r.layout is layout).state == REFUSED:
                    return MISSING
                tag = self.tags(key)[index]
        else:
            grid.split(key)
        return tag if tag in grid.labels else None

    def cell(self, key, index, value):
        """Choose ``value`` in one cell's dropdown (no event when it already shows it)."""
        shown = self.shown(key, index)
        if shown is MISSING or shown == value:
            return False
        self.grid._tagged([self.layout(key)], index, value)  # the dropdown's handler
        return True

    def tag_by_hand(self, key, tags, clicked=()):
        """Tag a unit to ``tags`` by hand: each cell it changes clicked, as the strict page saves.

        The cells the user clicks are those whose tag changes (a header proposal shown on the
        strict page is confirmed by the click too), each saved as one click saves it: the tags
        set (``with_tags``) and the aliases synced with the cells clicked (``synced``).
        """
        layout = next(u for u in self.layouts if u.key == key)
        now = self.tags(key)
        changed = {(key, i) for i, (a, b) in enumerate(zip(now, tags, strict=True)) if a != b}
        clicked = changed | {cell for cell in clicked if cell[0] == key}
        answers = with_tags(self.store.saved, layout, tags)
        assert self.store.update(synced(answers, self.layouts, clicked))
        self.grid.refresh()


def _tagged_units(page):
    return {u.key: page.tags(u.key) for u in page.layouts if u.usable and not u.rgb}


def _mirror(fast, strict, before, clicked=()):
    """Tag by hand on the strict page every unit whose tags a fast-page click saved."""
    keys = {key for key, _ in clicked}
    for key, tags in _tagged_units(fast).items():
        if tags != before.get(key) or key in keys:
            strict.tag_by_hand(key, tags, clicked)


class Sequence:
    """One random sequence of actions on the fast page and the strict page."""

    def __init__(self, seed):
        self.rng = random.Random(seed)
        answers = _start(self.rng.random() < 0.5)
        self.variant, self.added = 0, False
        layouts = _layouts()
        self.fast = Page(answers, layouts, ticked=True)
        self.strict = Page(answers, layouts, ticked=False)
        self.log = [f"seed {seed}: start {answers_to_bytes(answers)!r}"]

    @property
    def ticked(self):
        return self.fast.grid.same_channels.value

    def check(self, where):
        if self.ticked:
            return
        assert self.fast.draft == self.strict.draft, "\n".join([*self.log, where])

    def step(self):
        actions = [
            self.click,
            self.click,
            self.click,
            self.labels,
            self.image_type,
            self.use_for_all,
            self.recheck,
            self.reopen,
            self.toggle,
            self.toggle,
            self.row_of_tags,
        ]
        action = self.rng.choice(actions)
        action()
        self.check(self.log[-1])

    def toggle(self):
        self.log.append("untick" if self.ticked else "tick")
        self.fast.grid.same_channels.value = not self.ticked

    def _units(self):
        return [u for u in self.fast.layouts if u.usable and not u.rgb]

    def click(self):
        key = self.rng.choice(self._units()).key
        layout = next(u for u in self.fast.layouts if u.key == key)
        index = self.rng.randrange(layout.channels)
        value = self.rng.choice([None, *self.fast.labels.labels])
        self.log.append(f"click {key} C{index} = {value}")
        before, saved = _tagged_units(self.fast), self.fast.store.saved
        ticked = self.ticked
        self.fast.cell(key, index, value)
        if self.fast.store.saved is saved:
            return  # nothing saved (a label already in the unit is refused)
        if ticked:
            _mirror(self.fast, self.strict, before, [(key, index)])
        else:  # both pages are the strict page: the same click
            self.strict.cell(key, index, value)

    def row_of_tags(self):
        """Tag the fast page's row of tags (fast only): mirrored as hand tags."""
        matrix = self.fast.grid.matrix
        groups = matrix.groups()[0] if self.ticked else []
        if not groups:
            return
        group = self.rng.choice(groups)
        index = self.rng.randrange(group[0].channels)
        value = self.rng.choice([None, *self.fast.labels.labels])
        self.log.append(f"row of tags {group[0].key} C{index} = {value}")
        before = _tagged_units(self.fast)
        shown = matrix.template(group)[index]
        if shown != value:
            matrix._template_changed(group, index, value)  # the dropdown's handler
            _mirror(self.fast, self.strict, before)

    def labels(self):
        now = self.fast.labels.labels
        choice = self.rng.choice(["same", "add", "remove", "restore"])
        if choice == "add":
            labels = sorted({*now, EXTRA})
        elif choice == "remove" and now:
            labels = sorted(set(now) - {self.rng.choice(now)}) or list(LABELS)
        elif choice == "restore":
            labels = sorted({*now, *LABELS})
        else:
            labels = list(now)
        self.log.append(f"labels {labels}")
        for page in (self.fast, self.strict):
            page.set_labels(labels)

    def image_type(self):
        layout = self.rng.choice([u for u in self.fast.layouts if u.usable])
        value = self.rng.choice(["channels", "rgb"])
        self.log.append(f"type {layout.key} = {value}")
        for page in (self.fast, self.strict):
            page.types._chosen(layout, value)

    def use_for_all(self):
        """*Use for all folders*, even when every folder already has that type."""
        value = self.rng.choice(["channels", "channels", "rgb"])
        self.log.append(f"use for all = {value}")
        for page in (self.fast, self.strict):
            page.line._write(value)

    def recheck(self):
        """A new check of the folders: header names renamed, or a unit added."""
        if self.rng.random() < 0.5:
            self.variant += 1
        else:
            self.added = not self.added
        self.log.append(f"re-check variant {self.variant} added {self.added}")
        layouts = _layouts(self.variant, self.added)
        for page in (self.fast, self.strict):
            page.store.carry({u.key for u in layouts})
            page.recheck(layouts)

    def reopen(self):
        """Untick, then a new session on each saved draft, with renamed headers (stale aliases)."""
        if self.ticked:
            self.toggle()
            self.check(self.log[-1])
        self.variant += 1
        self.log.append(f"reopen variant {self.variant}")
        layouts = _layouts(self.variant, self.added)
        fast, strict = self.fast.store.saved, self.strict.store.saved
        self.fast = Page(fast, layouts, ticked=True)
        self.strict = Page(strict, layouts, ticked=False)


def light_refresh(grid):
    """Redraw nothing, but read each layout's row of tags as drawing the matrix reads it."""
    if grid.same_channels.value:
        for group in grid.matrix.groups()[0]:
            grid.matrix.template(group)


@pytest.fixture
def light(monkeypatch):
    """The cards without drawing their widgets: the actions call the widgets' handlers."""
    monkeypatch.setattr(TagGrid, "refresh", light_refresh)


@pytest.mark.usefixtures("light")
def test_unticking_gives_back_the_strict_pages_draft_byte_for_byte():
    """Random sequences of actions: unticked, the draft is the strict page's, byte for byte.

    250 fixed seeds of 14 actions each (about 20 s), each ending unticked.
    """
    unticks = 0
    for seed in SEEDS:
        sequence = Sequence(seed)
        for _ in range(STEPS):
            sequence.step()
            unticks += sequence.log[-1] == "untick"
        if sequence.ticked:
            sequence.toggle()
            unticks += 1
        sequence.check("final untick")
    assert unticks >= 2 * len(SEEDS)  # most sequences untick more than once


def test_a_click_on_a_trusted_unit_makes_its_whole_tags_the_users():
    """The click's change, run on the strict page's draft, saves the unit's whole tags."""
    layouts = _layouts()
    page = Page(_start(stale=False), layouts, ticked=True)
    exact = ("a", "r1", "t1")
    assert page.tags(exact) == EXACT  # trusted with no click
    page.set_labels([*LABELS, EXTRA])
    page.cell(exact, 2, EXTRA)
    page.grid.same_channels.value = False
    assert page.tags(exact) == ("DNA", "ACTIN", EXTRA)
    assert page.store.saved.channels["DNA"].header_names is not None
    assert "DNA" in page.store.saved.channels["DNA"].header_names


def test_use_for_all_with_every_type_already_answered_resyncs_the_strict_pages_draft():
    """Known limitation of the delta model (review of #325), closed: a save that changes no unit.

    *Use for all folders* clicked when every folder already has that type changes no unit but
    resyncs the aliases: on the strict page it drops those no tagged cell holds, so unticking
    must not give them back.
    """
    layouts = _layouts()
    answers = _start(stale=True)
    typed = {u.key: UnitAnswer((None,) * u.channels, "channels") for u in layouts if u.usable}
    answers = replace(answers, units={**typed, **answers.units})  # answered before opening
    fast, strict = Page(answers, layouts, ticked=True), Page(answers, layouts, ticked=False)
    alias = ("a", "r1", "t2")
    assert fast.tags(alias) == EXACT  # trusted from the saved aliases DAPI, Actin, Ch3
    assert "DAPI" in fast.store.saved.channels["DNA"].header_names
    for page in (fast, strict):
        page.line._write("channels")  # every folder already has it: no unit changes
    assert strict.store.saved.channels["DNA"].header_names is None  # resynced: dropped
    fast.grid.same_channels.value = False
    assert fast.draft == strict.draft


def test_patched_applies_the_same_edit_field_by_field():
    """Another card's answer reaches the strict page's draft as the same edit, nothing more."""
    before = _start(stale=True)
    named = replace(before, pairing={("a", "r1"): "r1"})
    strict = replace(before, units={}, channels={"DNA": ChannelAnswer(header_names=("DAPI",))})
    out = patched(before, named, strict)
    assert out == replace(strict, pairing={("a", "r1"): "r1"})  # tags and aliases kept
    renamed = replace(
        before, channels={**before.channels, "DNA": ChannelAnswer("Nuclei", ("DAPI", "Hoechst"))}
    )
    out = patched(before, renamed, strict)
    assert out.channels["DNA"] == ChannelAnswer("Nuclei", ("DAPI",))  # only the name changed
    shadow = StrictShadow()
    shadow.follow(before, named, None)
    assert shadow.draft is None  # no strict page apart: nothing kept
    shadow.hold(strict)
    shadow.follow(before, named, lambda answers: answers)
    assert shadow.restore() == strict and shadow.draft is None
