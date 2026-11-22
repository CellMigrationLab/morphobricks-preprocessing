"""The notebook's matrix uses the one header-name rule (issue #368)."""

from __future__ import annotations

import unicodedata

from morphobricks_preprocessing.channel_matrix import name_label
from morphobricks_preprocessing.draft_answers import ChannelAnswer

COMPOSED = "Alexa \u00c1"  # A with acute, one code point
DECOMPOSED = unicodedata.normalize("NFD", COMPOSED)


def test_the_notebook_matrix_recognises_an_nfc_equivalent_saved_alias():
    # The alias was saved composed; the image header spells it decomposed. Publication, the fast
    # layout, the physical check and reconciliation already accepted it; the notebook did not.
    channels = {"AF488": ChannelAnswer(header_names=(COMPOSED,))}
    assert name_label(DECOMPOSED, {"AF488"}, channels) == "AF488"
