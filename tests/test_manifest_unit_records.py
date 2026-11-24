"""The description's unit records are checked before ``RunInputs`` uses them (#491).

``experiment_description.units`` holds one record per declared unit: its identity (``repeat``,
``condition``, ``technical_repeat``), its ``channels`` and ``image_type_header_verified``, and
for a unit declared RGB its declared ``rgb_header_override``. Reading the manifest refuses a
record that is not such an object (``manifest_unit_record_malformed``, a
``ManifestFormatError``), naming the unit and the field; ``RunInputs.open`` refuses a
well-formed record that is not the published description's unit
(``manifest_unit_record_mismatch``, a ``RunInputsError``). Nothing is filled in, skipped or
repaired, and no raw ``KeyError`` escapes.
"""

from __future__ import annotations

import json

import pytest

from morphobricks_preprocessing.manifest import (
    MANIFEST_FILENAME,
    ManifestFormatError,
    read_manifest,
)
from morphobricks_preprocessing.run_inputs import RunInputs
from morphobricks_preprocessing.run_inputs_error import RunInputsError
from morphobricks_preprocessing.testing import make_described_run
from spec_pin import fixtures
from test_rgb_header_override import V40

VALID = {f.name.split("_")[0]: f for f in fixtures("valid")}
MALFORMED = "manifest_unit_record_malformed"
MISMATCH = "manifest_unit_record_mismatch"
UNITS = "experiment_description.units"
OVERRIDE_FIELDS = (
    "rgb_header_override_images",
    "image_type_header_overridden",
    "header_photometric",
)


@pytest.fixture
def plain_run(tmp_path):
    return make_described_run(tmp_path / "run", VALID["v08"].data, masks=("Nuclei", "TumorRegion"))


@pytest.fixture
def rgb_run(tmp_path):
    return make_described_run(tmp_path / "rgb", V40)


def _edit(run, change):
    """Rewrite the run's manifest with ``change`` applied to its unit records."""
    path = run.output / MANIFEST_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data["experiment_description"]["units"])
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path.read_bytes()


def _rgb_index(units):
    return next(i for i, u in enumerate(units) if u.get("rgb_header_override") is True)


def _replace_rgb(record):
    """Replace the first unit declaring the override with ``record`` (#491's case)."""
    return lambda units: units.__setitem__(_rgb_index(units), record)


def _pop_rgb(field):
    return lambda units: units[_rgb_index(units)].pop(field)


def _set_rgb(field, value):
    return lambda units: units[_rgb_index(units)].__setitem__(field, value)


@pytest.mark.parametrize(
    ("change", "named"),
    [
        (_replace_rgb({"rgb_header_override": True}), "repeat"),
        (_replace_rgb({"rgb_header_override": True}), "technical_repeat"),
        (_replace_rgb({}), "condition"),
        (_replace_rgb("CTRL/rep1"), "is not an object"),
        (_replace_rgb(None), "is not an object"),
        (_replace_rgb([]), "is not an object"),
        (_pop_rgb("repeat"), "repeat is missing"),
        (_pop_rgb("condition"), "condition is missing"),
        (_pop_rgb("technical_repeat"), "technical_repeat is missing"),
        (_pop_rgb("channels"), "channels is missing"),
        (_pop_rgb("image_type_header_verified"), "image_type_header_verified is missing"),
        (_set_rgb("repeat", ""), "repeat is not"),
        (_set_rgb("condition", 7), "condition is not"),
        (_set_rgb("technical_repeat", None), "technical_repeat is not"),
        (_set_rgb("channels", "R"), "channels is not"),
        (_set_rgb("channels", ["R", 1, "B"]), "channels is not"),
        (_set_rgb("image_type_header_verified", "false"), "image_type_header_verified is not"),
        (_set_rgb("repeat_id", "rep1"), "repeat_id is not a unit record field"),
    ],
)
def test_a_malformed_unit_record_is_refused_when_the_manifest_is_read(rgb_run, change, named):
    path = rgb_run.output / MANIFEST_FILENAME
    index = _rgb_index(_units(rgb_run))
    before = _edit(rgb_run, change)
    for read in (lambda: read_manifest(path), lambda: RunInputs.open(rgb_run.output)):
        with pytest.raises(ManifestFormatError) as refused:
            read()
        assert type(refused.value).__name__ == "ManifestUnitRecordError"
        assert refused.value.code == MALFORMED
        message = str(refused.value)
        assert MALFORMED in message and f"{UNITS}[{index}]" in message and named in message
    assert path.read_bytes() == before  # never repaired


def _units(run):
    """Return the unit records of the run's manifest as they are now."""
    path = run.output / MANIFEST_FILENAME
    return json.loads(path.read_text(encoding="utf-8"))["experiment_description"]["units"]


@pytest.mark.parametrize("field", ["repeat", "condition", "technical_repeat"])
@pytest.mark.parametrize("which", ["rgb", "plain"])
def test_an_unknown_unit_identity_is_refused_at_open_naming_the_field(
    rgb_run, plain_run, which, field
):
    run = rgb_run if which == "rgb" else plain_run
    index = 0 if which == "plain" else None

    def change(units):
        i = _rgb_index(units) if index is None else index
        units[i][field] = "not_declared"

    before = _edit(run, change)
    read_manifest(run.output / MANIFEST_FILENAME)  # well-formed: read
    with pytest.raises(RunInputsError) as refused:
        RunInputs.open(run.output)
    assert type(refused.value).__name__ == "UnitRecordMismatchError"
    assert refused.value.code == MISMATCH
    message = str(refused.value)
    assert MISMATCH in message and f"{UNITS}[" in message and field in message
    assert "not_declared" in message
    assert (run.output / MANIFEST_FILENAME).read_bytes() == before


def test_an_rgb_unit_without_its_declared_key_is_refused_at_open(rgb_run):
    def change(units):
        unit = units[_rgb_index(units)]
        for field in ("rgb_header_override", *OVERRIDE_FIELDS):
            unit.pop(field, None)

    _edit(rgb_run, change)
    with pytest.raises(RunInputsError, match="rgb_header_override") as refused:
        RunInputs.open(rgb_run.output)
    assert refused.value.code == MISMATCH


def test_a_unit_not_declared_rgb_with_the_declared_key_is_refused_at_open(plain_run):
    _edit(plain_run, lambda units: units[0].__setitem__("rgb_header_override", False))
    with pytest.raises(RunInputsError, match="rgb_header_override") as refused:
        RunInputs.open(plain_run.output)
    assert refused.value.code == MISMATCH


def test_channels_other_than_the_units_are_refused_at_open(plain_run):
    _edit(plain_run, lambda units: units[0].__setitem__("channels", ["NotAChannel"]))
    with pytest.raises(RunInputsError, match=r"units\[0\]\.channels") as refused:
        RunInputs.open(plain_run.output)
    assert refused.value.code == MISMATCH


def test_a_missing_or_repeated_unit_record_is_refused_at_open(plain_run):
    _edit(plain_run, lambda units: units.append(dict(units[0])))
    with pytest.raises(RunInputsError, match="more than once") as refused:
        RunInputs.open(plain_run.output)
    assert refused.value.code == MISMATCH
    _edit(plain_run, lambda units: units.pop())  # back to the original records
    _edit(plain_run, lambda units: units.pop(0))
    with pytest.raises(RunInputsError, match="has no record") as refused:
        RunInputs.open(plain_run.output)
    assert refused.value.code == MISMATCH


def test_valid_rgb_and_plain_unit_records_open(rgb_run, plain_run):
    for run in (rgb_run, plain_run):
        RunInputs.open(run.output)
    assert _units(plain_run) and all("rgb_header_override" not in u for u in _units(plain_run))
    assert any(u.get("rgb_header_override") is True for u in _units(rgb_run))
