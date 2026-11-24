"""A projection entry's dtype rule and source SHA-256 are checked as read (#443)."""

from dataclasses import replace

import pytest

from morphobricks_preprocessing.conversion_record import ConversionRecord, ConversionRecordError
from test_projection_record import SHA, edited, projected_entry


def projection(change):
    return lambda d: d["files"][0]["projection"].update(change)


@pytest.mark.parametrize(
    "rule", ["sum uint16->float64", "max uint16->uint16 (edited)", "", "MAX uint16->uint16"]
)
def test_a_dtype_rule_other_than_the_table_s_is_refused(rule):
    with pytest.raises(ConversionRecordError, match="dtype rule"):
        ConversionRecord.from_dict(edited(projection({"dtype_rule": rule})))
    with pytest.raises(ValueError, match="dtype rule"):
        replace(projected_entry().projection, dtype_rule=rule)


@pytest.mark.parametrize("sha", ["abc", "", "A" * 64, "g" * 64, "a" * 63, "a" * 65])
def test_a_source_sha256_that_is_not_one_is_refused(sha):
    with pytest.raises(ConversionRecordError, match="SHA-256"):
        ConversionRecord.from_dict(edited(projection({"source_content_sha256": sha})))
    done = projected_entry().projection
    with pytest.raises(ValueError, match="SHA-256"):
        done.with_content(sha)
    with pytest.raises(ValueError, match="SHA-256"):
        replace(done, source_content_sha256=sha)


def test_the_table_s_rule_and_a_real_sha256_are_accepted():
    done = projected_entry().projection
    assert done.dtype_rule == "max uint16->uint16" and done.source_content_sha256 == SHA
    assert done.with_content("0123456789abcdef" * 4).source_content_sha256 == "0123456789abcdef" * 4
    assert replace(done, source_content_sha256=None).source_content_sha256 is None  # a plan
    ConversionRecord.from_dict(edited(lambda d: None))
