"""Proposed IDs for a draft experiment description (experiment-v1 sections 3.2, 4.6a and 7).

A proposal is the folder label (or the raw header name of a channel) **as it is**, offered only
when the reader would accept it as an ID; otherwise there is no proposal and the user names the
ID. Nothing is trimmed, normalized, re-cased, numbered or derived (section 3.3: no ID is ever
repaired), and a proposal is never adopted without the user's confirmation. The rules applied are
the reader's own (``description_identity``, ``description_rules``): this module holds the
decision "propose or not" and no second copy of the rules.

The Spec says a condition or technical-repeat folder that is not allowed (``control``, ``files``)
gets "a different ID that is allowed". It does not say how to make one, and deriving one from
the label would be a repair, so here such a folder has no proposal either.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Literal

from .description_identity import Identities, nfc
from .description_report import Issues
from .description_rules import FORBIDDEN, RGB_TOKENS, Code

IdKind = Literal["conditions", "channels", "repeats", "technical_repeats"]
REUSED = "R_USED_ID_REUSED"
"""Reported for a value that is already in ``used_ids``: it names a retired or declared ID."""


def id_problems(kind: IdKind, value: str, used: Collection[str] = ()) -> tuple[str, ...]:
    """Return the codes under which ``value`` is refused as an ID of ``kind``, empty if allowed.

    The Label rules, NFC and the ``single`` rule for every kind; the forbidden names (section
    4.7, compared exactly) for every ID, since each is a table key; the Token rule and the
    reserved list for channels. ``used`` holds the IDs of that kind the lineage already lists
    (``used_ids``): a value equal ignoring case to one is ``E_ID_CASE_COLLISION``, and equal
    exactly is ``R_USED_ID_REUSED`` (section 4.9).

    The ``single`` rule that ties a condition or technical-repeat ID to its folder is not
    checked here: it compares the ID with a folder, which this function does not know.
    """
    out = Issues()
    ids = Identities(out)
    if kind == "channels":
        ids.token(value, "id")
        ids.nfc(value, "id")
        ids.single(value, "id")
        ids.reserved(value, "id")
    else:
        ids.label_id(value, "id", kind)
    codes = [issue.code.value for issue in out.items]
    if value in FORBIDDEN:
        codes.append(Code.E_FORBIDDEN_FIELD.value)
    if value in used:
        codes.append(REUSED)
    elif any(nfc(other).casefold() == nfc(value).casefold() for other in used):
        codes.append(Code.E_ID_CASE_COLLISION.value)
    return tuple(dict.fromkeys(codes))


def propose_id(kind: IdKind, label: str, used: Collection[str] = ()) -> str | None:
    """Return the folder ``label`` as the proposed ID of ``kind``, or None when it is not allowed.

    Applies to conditions, technical repeats and, as the owner decided (Q6), biological repeats:
    for ``control``, ``files`` or ``z`` there is no proposal. Never a repaired spelling.
    """
    if kind == "channels":
        raise ValueError("a channel ID is proposed from a header name: use propose_channel_ids")
    return None if id_problems(kind, label, used) else label


@dataclass(frozen=True)
class ChannelProposal:
    """What is proposed for the channel at one source index C*i* of a layout.

    ``header_name`` is the name as the header holds it (None when it names none), and
    ``proposed_id`` the same text when it is a valid channel ID as it stands, else None.
    """

    index: int
    header_name: str | None
    proposed_id: str | None


def propose_channel_ids(
    header_names: Sequence[str | None], used: Collection[str] = ()
) -> tuple[ChannelProposal, ...]:
    """Propose a channel ID for each C index of a layout from its raw header names (Q4).

    A header name is proposed as the channel ID only if it is a valid channel ID exactly as
    written (Token rule, not reserved, not retired in ``used``, no outer whitespace); otherwise
    the ID is left blank for the user. Names that are equal ignoring case within the layout
    get no proposal, since one channel cannot stand at two positions of a unit. ``R``, ``G``
    and ``B`` are never proposed: they are the fixed IDs of a declared RGB unit and take no
    header names (section 4.4b), so a header called ``R`` is no evidence of an RGB unit.
    A proposal is not an alias: the user decides whether the name is also recorded as
    ``header_names`` evidence.
    """
    candidates = [
        name if name and not id_problems("channels", name, used) else None for name in header_names
    ]
    folded = [nfc(name).casefold() for name in candidates if name]
    return tuple(
        ChannelProposal(
            index,
            header,
            candidate
            if candidate
            and folded.count(nfc(candidate).casefold()) == 1
            and candidate.casefold() not in {token.casefold() for token in RGB_TOKENS}
            else None,
        )
        for index, (header, candidate) in enumerate(zip(header_names, candidates, strict=True))
    )
