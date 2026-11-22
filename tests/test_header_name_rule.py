"""One header-name rule for every caller (issue #368).

Equal after NFC, never trimmed, never case-folded.
"""

from __future__ import annotations

import unicodedata

from morphobricks_preprocessing.description_identity import header_name_matches

COMPOSED = "Alexa Á"  # A with acute, one code point
DECOMPOSED = unicodedata.normalize("NFD", COMPOSED)


def test_nfc_equivalent_spellings_match_and_nothing_else_does():
    assert COMPOSED != DECOMPOSED
    assert header_name_matches(DECOMPOSED, [COMPOSED])
    assert header_name_matches(COMPOSED, [DECOMPOSED])
    assert not header_name_matches(f" {COMPOSED}", [COMPOSED])  # never trimmed
    assert not header_name_matches(COMPOSED.lower(), [COMPOSED])  # never case-folded
    assert not header_name_matches(COMPOSED, [])  # only confirmed names count
