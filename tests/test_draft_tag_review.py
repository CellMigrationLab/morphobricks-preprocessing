"""``tag_review`` of the draft file: the refused and confirmed units of *Tag by repeat*.

Optional (a draft without it is read as before, and an empty review writes nothing); when present
it is read strictly, so a malformed section is refused with the file, never dropped.
"""

import json
from dataclasses import replace

import pytest

from draft_file_support import complete
from morphobricks_preprocessing.draft_format import (
    DraftFileError,
    answers_from_bytes,
    answers_to_bytes,
)
from morphobricks_preprocessing.repeat_review import Override, RefusedUnit, TagReview

GONE, OTHER = ("drug", "rep2", "t1"), ("drug", "rep2", "t2")
REVIEW = TagReview(
    {
        GONE: RefusedUnit(("DNA", "ACTIN"), ("ctrl", "rep2", "t1"), {"DNA": ("P", "Pb")}),
        OTHER: RefusedUnit(("DNA", "ACTIN"), None),
    },
    {GONE: Override(("DNA", "ACTIN"), ("P", None))},
)


def _document(review=REVIEW):
    return json.loads(answers_to_bytes(replace(complete(), tag_review=review)))


def _bytes(document):
    return json.dumps(document).encode()


def test_the_review_round_trips_and_the_bytes_are_deterministic():
    answers = replace(complete(), tag_review=REVIEW)
    data = answers_to_bytes(answers)
    assert answers_from_bytes(data) == answers
    assert answers_to_bytes(answers_from_bytes(data)) == data
    section = json.loads(data)["tag_review"]
    assert section["refused"][0] == {
        "unit": list(GONE),
        "tags": ["DNA", "ACTIN"],
        "reference": ["ctrl", "rep2", "t1"],
        "aliases": {"DNA": ["P", "Pb"]},
    }
    assert section["confirmed"] == [
        {"unit": list(GONE), "tags": ["DNA", "ACTIN"], "names": ["P", None]}
    ]


@pytest.mark.parametrize("review", [None, TagReview()])
def test_no_or_an_empty_review_writes_no_section_and_a_file_without_one_is_read_as_before(review):
    answers = replace(complete(), tag_review=review)
    data = answers_to_bytes(answers)
    assert b"tag_review" not in data
    assert answers_from_bytes(data) == replace(complete(), tag_review=None)
    assert data == answers_to_bytes(complete())  # the bytes of every draft that has none


def _mutations():
    def put(path, value):
        def apply(doc):
            node = doc["tag_review"]
            for step in path[:-1]:
                node = node[step]
            node[path[-1]] = value

        return apply

    def drop(path):
        def apply(doc):
            node = doc["tag_review"]
            for step in path[:-1]:
                node = node[step]
            del node[path[-1]]

        return apply

    return {
        "null": lambda d: d.update(tag_review=None),
        "not an object": lambda d: d.update(tag_review=[]),
        "unknown key": put(["extra"], 1),
        "no refused": drop(["refused"]),
        "no confirmed": drop(["confirmed"]),
        "refused not a list": put(["refused"], {}),
        "refused unknown key": put(["refused", 0, "extra"], 1),
        "refused no tags": drop(["refused", 0, "tags"]),
        "refused no aliases": drop(["refused", 0, "aliases"]),
        "unit of two folders": put(["refused", 0, "unit"], ["a", "b"]),
        "unit not text": put(["refused", 0, "unit"], ["a", "b", 3]),
        "tags not text": put(["refused", 0, "tags"], ["DNA", None]),
        "reference of one folder": put(["refused", 1, "reference"], ["a"]),
        "aliases not lists": put(["refused", 0, "aliases"], {"DNA": "P"}),
        "aliases not an object": put(["refused", 0, "aliases"], []),
        "names not text": put(["confirmed", 0, "names"], ["P", 3]),
        "names not a list": put(["confirmed", 0, "names"], "P"),
        "confirmed unknown key": put(["confirmed", 0, "extra"], 1),
        "unit twice (refused)": lambda d: d["tag_review"]["refused"].append(
            d["tag_review"]["refused"][0]
        ),
        "unit twice (confirmed)": lambda d: d["tag_review"]["confirmed"].append(
            d["tag_review"]["confirmed"][0]
        ),
    }


@pytest.mark.parametrize("name", sorted(_mutations()))
def test_a_malformed_section_is_refused_clearly_never_silently_dropped(name):
    document = _document()
    _mutations()[name](document)
    with pytest.raises(DraftFileError) as refusal:
        answers_from_bytes(_bytes(document))
    assert "tag_review" in str(refusal.value)


def test_the_format_version_is_not_bumped_so_an_older_draft_still_opens():
    document = _document(None)
    assert document["version"] == 4 and "tag_review" not in document
    assert answers_from_bytes(_bytes(document)).tag_review is None
