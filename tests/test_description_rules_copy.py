"""The reader's own copy of two rules equals the values discovery and normalization use.

Spec section 2.7: the reader takes no vocabulary from outside itself at run time, so
``description_rules`` holds its own ``MISSING_TEXT`` and ``SINGLE``. These tests keep them equal
to the shared values, so a later change to either is caught here.
"""

from __future__ import annotations

import pytest
from morphobricks_normalization.records import SINGLE as NORMALIZATION_SINGLE

from morphobricks_preprocessing import description_rules as rules
from morphobricks_preprocessing.discovery import (
    MISSING_TEXT as DISCOVERY_MISSING_TEXT,
)
from morphobricks_preprocessing.discovery import IdentityError, check_identity_text


def test_the_reader_s_missing_text_equals_discoverys():
    assert rules.MISSING_TEXT == DISCOVERY_MISSING_TEXT


def test_the_reader_s_single_equals_normalizations():
    assert rules.SINGLE == NORMALIZATION_SINGLE


@pytest.mark.parametrize(
    "value",
    ["", "NA", "Nan", "NONE", "null", "N/A", "<NA>", " a", "a ", "\ta", "ok", "single", "x y"],
)
def test_the_label_rule_agrees_with_check_identity_text(value):
    try:
        check_identity_text(value, "identity")
        shared_refuses = False
    except IdentityError:
        shared_refuses = True
    assert rules.label_refused(value) is shared_refuses
