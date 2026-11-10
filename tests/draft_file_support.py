"""Shared answers and the table of refused draft files (``test_draft_*``)."""

import json

from morphobricks_preprocessing.draft_answers import (
    ChannelAnswer,
    ConditionAnswer,
    DraftAnswers,
    MaskAnswer,
    RepeatAnswer,
    UnitAnswer,
)
from morphobricks_preprocessing.draft_format import (
    ABSENT,
    SPEC_COMMIT,
    answers_to_bytes,
)

DAY = "2026-11-06"
PATHS = [f"{c}/{r}/{n}.tif" for c in ("CTRL", "DrugA") for r in ("rep1", "rep2") for n in "ab"]
UNITS = [(c, r, "single") for c in ("CTRL", "DrugA") for r in ("rep1", "rep2")]
COUNTS = dict.fromkeys(UNITS, 2)
"""The channel count of every unit's image, as the confirmed axis roles read it (2 here)."""


def complete(base=ABSENT):
    """Every answer a description needs, all of them the user's (proposals adopted by them)."""
    return DraftAnswers(
        base,
        SPEC_COMMIT,
        conditions={
            "CTRL": ConditionAnswer(id="CTRL"),
            "DrugA": ConditionAnswer(id="DrugA", reference="CTRL", target="SRC"),
        },
        technical_repeats={"single": "single"},
        repeats={
            "rep1": RepeatAnswer(id="rep1", confirmed_on=DAY),
            "rep2": RepeatAnswer(id="rep2", confirmed_on="2026-11-05", display_name="Second"),
        },
        units={key: UnitAnswer(("DNA", "ACTIN"), "channels") for key in UNITS},
        channels={
            "DNA": ChannelAnswer("DAPI", ("DAPI",)),
            "ACTIN": ChannelAnswer(None, None),
        },
        masks={"Nuclei": MaskAnswer("Nuclei", "instances", ("DNA",))},
    )


def snapshot(folder):
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir())}


def document(answers):
    return json.loads(answers_to_bytes(answers))


def encode(doc):
    return json.dumps(doc).encode()


def broken(change):
    doc = document(complete())
    change(doc)
    return encode(doc)


REFUSED = {
    "not_utf8": (b"\xff\xfe{}", "malformed"),
    "bom": (b"\xef\xbb\xbf" + answers_to_bytes(complete()), "malformed"),
    "not_json": (b"{", "malformed"),
    "empty": (b"", "malformed"),
    "list": (b"[]", "malformed"),
    "nan": (b'{"format": NaN}', "malformed"),
    "duplicate": (
        answers_to_bytes(complete()).replace(b'"version": 3,', b'"version": 3, "version": 3,'),
        "malformed",
    ),
    "wrong_format": (broken(lambda d: d.update(format="other")), "malformed"),
    "version_1": (broken(lambda d: d.update(version=1)), "unknown_version"),
    "version_4": (broken(lambda d: d.update(version=4)), "unknown_version"),
    "version_2": (broken(lambda d: d.update(version=2)), "unknown_version"),
    "version_text": (broken(lambda d: d.update(version="3")), "unknown_version"),
    "version_bool": (broken(lambda d: d.update(version=True)), "unknown_version"),
    "version_float": (broken(lambda d: d.update(version=3.0)), "unknown_version"),
    "version_missing": (broken(lambda d: d.pop("version")), "unknown_version"),
    "extra_top": (broken(lambda d: d.update(extra=1)), "unknown_key"),
    "extra_condition": (
        broken(lambda d: d["conditions"]["CTRL"].update(colour="red")),
        "unknown_key",
    ),
    "extra_unit": (
        broken(lambda d: d["units"]["CTRL"]["rep1"]["single"].update(x=1)),
        "unknown_key",
    ),
    "extra_mask": (broken(lambda d: d["masks"]["Nuclei"].update(x=1)), "unknown_key"),
    "extra_schema": (broken(lambda d: d.update(schema="morphobricks-experiment")), "unknown_key"),
    "missing_section": (broken(lambda d: d.pop("masks")), "malformed"),
    "missing_pairing": (broken(lambda d: d.pop("pairing")), "malformed"),
    "pairing_not_nested": (broken(lambda d: d.update(pairing={"DrugA": "rep1"})), "malformed"),
    "pairing_label_type": (
        broken(lambda d: d.update(pairing={"DrugA": {"rep2": 1}})),
        "malformed",
    ),
    "missing_commit": (broken(lambda d: d.pop("spec_commit")), "malformed"),
    "bad_commit": (broken(lambda d: d.update(spec_commit="main")), "malformed"),
    "unit_without_channels": (
        broken(lambda d: d["units"]["CTRL"]["rep1"]["single"].pop("channels")),
        "malformed",
    ),
    "mask_without_representation": (
        broken(lambda d: d["masks"]["Nuclei"].pop("representation")),
        "malformed",
    ),
    "base_missing": (broken(lambda d: d.pop("base_sha256")), "R_DRAFT_BASE_MISSING"),
    "base_null": (broken(lambda d: d.update(base_sha256=None)), "R_DRAFT_BASE_MISSING"),
    "base_upper": (broken(lambda d: d.update(base_sha256="A" * 64)), "R_DRAFT_BASE_MISSING"),
    "base_short": (broken(lambda d: d.update(base_sha256="ab")), "R_DRAFT_BASE_MISSING"),
    "base_empty": (broken(lambda d: d.update(base_sha256="")), "R_DRAFT_BASE_MISSING"),
    "date_not_real": (
        broken(lambda d: d["repeats"]["rep1"].update(confirmed_on="2026-02-30")),
        "malformed",
    ),
    "date_format": (
        broken(lambda d: d["repeats"]["rep1"].update(confirmed_on="6 Nov 2026")),
        "malformed",
    ),
    "type_id": (broken(lambda d: d["conditions"]["CTRL"].update(id=5)), "malformed"),
    "type_channels": (
        broken(lambda d: d["units"]["CTRL"]["rep1"]["single"].update(channels="DNA")),
        "malformed",
    ),
    "type_channel_item": (
        broken(lambda d: d["units"]["CTRL"]["rep1"]["single"].update(channels=["DNA", 1])),
        "malformed",
    ),
    "type_table": (broken(lambda d: d.update(units=[])), "malformed"),
    "type_technical": (
        broken(lambda d: d["technical_repeats"].update(single=["single"])),
        "malformed",
    ),
}
