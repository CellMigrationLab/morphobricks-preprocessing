"""The manifest's description section digests are checked before they reach a consumer (#490).

``experiment_description.section_digests`` is what ``RunInputs.section_digests()`` gives a brick
for its cache keys and what ``resolve_request()`` records as provenance. Reading the manifest
refuses one that does not hold exactly the four sections, each a lowercase SHA-256
(``manifest_section_digests_malformed``, a ``ManifestFormatError``); ``RunInputs.open`` refuses a
well-formed digest that is not the one of the published description the run read
(``manifest_section_digest_mismatch``, a ``RunInputsError``), naming each section. Nothing is
filled in, repaired or recomputed into the manifest: the file is left as it was.
"""

from __future__ import annotations

import json
import pickle

import pytest

from morphobricks_preprocessing.description_consume import read_published_description
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME
from morphobricks_preprocessing.description_write import SECTIONS, section_digests
from morphobricks_preprocessing.manifest import (
    MANIFEST_FILENAME,
    ManifestFormatError,
    ManifestSectionDigestsError,
    read_manifest,
)
from morphobricks_preprocessing.run_inputs import RunInputs
from morphobricks_preprocessing.run_inputs_error import (
    RunInputsError,
    SectionDigestMismatchError,
)
from morphobricks_preprocessing.run_inputs_request import Request
from morphobricks_preprocessing.testing import make_described_run
from spec_pin import fixtures

VALID = {f.name.split("_")[0]: f for f in fixtures("valid")}
MALFORMED = "manifest_section_digests_malformed"
MISMATCH = "manifest_section_digest_mismatch"
FIELD = "experiment_description.section_digests"


@pytest.fixture
def run(tmp_path):
    return make_described_run(tmp_path / "run", VALID["v08"].data, masks=("Nuclei", "TumorRegion"))


def _edit(run, change):
    """Rewrite the run's manifest with ``change`` applied to its section digests."""
    path = run.output / MANIFEST_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data["experiment_description"]["section_digests"])
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path.read_bytes()


def _published(run):
    return section_digests(read_published_description(run.output / DESCRIPTION_FILENAME)[0])


def test_a_normal_run_opens_and_gives_the_published_descriptions_digests(run):
    inputs = RunInputs.open(run.output)
    expected = _published(run)
    assert set(expected) == set(SECTIONS)
    assert inputs.section_digests() == expected
    provenance = inputs.resolve_request(Request(("DNA",))).provenance
    assert dict(provenance.section_digests) == expected


def _drop(name):
    return lambda digests: digests.pop(name)


def _set(name, value):
    return lambda digests: digests.__setitem__(name, value)


@pytest.mark.parametrize(
    ("change", "named"),
    [
        (_drop("channels"), "channels"),
        (_drop("masks"), "masks"),
        (_set("used_ids", "a" * 64), "used_ids"),
        (_set("channels", "0"), "channels"),
        (_set("channels", "g" * 64), "channels"),
        (_set("repeats", "A" * 64), "repeats"),
        (_set("conditions", "a" * 63), "conditions"),
        (_set("masks", "a" * 65), "masks"),
        (_set("masks", None), "masks"),
        (_set("channels", 7), "channels"),
        (_set("channels", " " + "a" * 63), "channels"),
        (lambda digests: digests.clear(), "conditions"),
    ],
)
def test_a_malformed_digest_record_is_refused_when_the_manifest_is_read(run, change, named):
    before = _edit(run, change)
    path = run.output / MANIFEST_FILENAME
    for read in (lambda: read_manifest(path), lambda: RunInputs.open(run.output)):
        with pytest.raises(ManifestSectionDigestsError) as refused:
            read()
        assert isinstance(refused.value, ManifestFormatError)
        assert refused.value.code == MALFORMED
        message = str(refused.value)
        assert MALFORMED in message and FIELD in message and named in message
    assert path.read_bytes() == before  # never repaired


def test_the_whole_digest_record_must_be_an_object(run):
    path = run.output / MANIFEST_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    data["experiment_description"]["section_digests"] = list(SECTIONS)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ManifestFormatError, match="section_digests"):
        read_manifest(path)


@pytest.mark.parametrize("section", SECTIONS)
def test_a_well_formed_but_wrong_digest_is_refused_at_open_naming_the_section(run, section):
    wrong = "0" * 64
    assert _published(run)[section] != wrong
    before = _edit(run, _set(section, wrong))
    read_manifest(run.output / MANIFEST_FILENAME)  # well formed: the reader accepts it
    with pytest.raises(SectionDigestMismatchError) as refused:
        RunInputs.open(run.output)
    error = refused.value
    assert isinstance(error, RunInputsError) and error.code == MISMATCH
    message = str(error)
    assert message.startswith(f"{MISMATCH}: ")
    assert f"{FIELD}.{section}" in message and "Run preprocessing again" in message
    others = [name for name in SECTIONS if name != section]
    assert not any(f"{FIELD}.{name}" in message for name in others)
    assert (run.output / MANIFEST_FILENAME).read_bytes() == before  # never overwritten
    again = pickle.loads(pickle.dumps(error))
    assert type(again) is SectionDigestMismatchError and str(again) == message


def test_swapped_sections_are_refused_naming_both(run):
    def swap(digests):
        digests["conditions"], digests["channels"] = digests["channels"], digests["conditions"]

    _edit(run, swap)
    with pytest.raises(SectionDigestMismatchError) as refused:
        RunInputs.open(run.output)
    message = str(refused.value)
    assert f"{FIELD}.conditions" in message and f"{FIELD}.channels" in message
    assert f"{FIELD}.repeats" not in message and f"{FIELD}.masks" not in message


def test_a_changed_description_is_still_refused_by_its_sha256(run):
    path = run.output / DESCRIPTION_FILENAME
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(RunInputsError, match="not the one the run read"):
        RunInputs.open(run.output)


def test_a_missing_description_is_refused(run):
    (run.output / DESCRIPTION_FILENAME).unlink()
    with pytest.raises(RunInputsError):
        RunInputs.open(run.output)
