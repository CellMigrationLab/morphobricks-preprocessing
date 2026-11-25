"""A nesting implied by a path of any length is not direct (#381)."""

import pytest

from test_mask_hierarchy_cycles import relations


def direct(pairs):
    return {(p.child, p.parent) for p in pairs if p.direct}


CHAIN = [("A", "B"), ("B", "C"), ("C", "D")]


@pytest.mark.parametrize(
    ("shortcuts", "kept"),
    [
        ([("A", "D")], CHAIN),  # only the long shortcut: implied by a 3-edge path
        ([("A", "C"), ("B", "D"), ("A", "D")], CHAIN),  # short and long shortcuts together
        ([("A", "C")], CHAIN),  # a 2-edge shortcut, as before
        ([], CHAIN),
    ],
)
def test_a_four_level_chain_keeps_only_its_links(shortcuts, kept):
    pairs = relations(*CHAIN, *shortcuts)
    assert direct(pairs) == set(kept)
    # Relations and evidence are unchanged: only ``direct`` follows the reduction.
    nested = {(p.child, p.parent) for p in pairs if p.relation == "inside"}
    assert nested == set(CHAIN) | set(shortcuts)


def test_a_five_level_chain_with_only_the_longest_shortcut():
    chain = [("A", "B"), ("B", "C"), ("C", "D"), ("D", "E")]
    assert direct(relations(*chain, ("A", "E"))) == set(chain)


def test_a_multi_parent_dag_keeps_each_parent_that_is_not_implied():
    # A in B and in X; B in C in D; X in D; A in D implied (through B and through X).
    links = [("A", "B"), ("A", "X"), ("B", "C"), ("C", "D"), ("X", "D"), ("A", "D")]
    assert direct(relations(*links)) == {("A", "B"), ("A", "X"), ("B", "C"), ("C", "D"), ("X", "D")}


def test_a_cycle_inside_a_long_chain_reduces_only_between_sets():
    # P and Q nested in each other, in R in S; Small in P; Small in S implied by 4 edges.
    links = [("P", "Q"), ("Q", "P"), ("P", "R"), ("Q", "R"), ("R", "S"), ("Small", "P")]
    got = direct(relations(*links, ("Small", "S"), ("P", "S")))
    assert got == set(links)
    assert ("Small", "S") not in got and ("P", "S") not in got
