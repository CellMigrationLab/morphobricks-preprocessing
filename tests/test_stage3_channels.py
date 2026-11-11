"""Stage 3 S3.3: per-unit channel lists and ``plane_histograms.csv`` schema version 5.

Owner decision of 11 November 2026: different channel counts across repeats are enabled now.
Each imaging unit lists its channels by canonical ID (experiment-v1 section 4.4); the same ID is
the same stain; each image's channel count is checked against its own unit. The table is
written by normalization v0.5.0 (ADR 0003 HV1-HV4): one row per plane, the header lists every
declared channel ID sorted, the ``channels`` column the IDs present in that plane, and an
absent channel is blank, never zero.

- the owner's extra-stain case: three shared stains and a fourth (``EXTRA``) acquired only in
  ``Biological_repeat_2``, end to end (table, manifest coverage, normalization fits per repeat,
  the global fit refused unless the user opts in, the resolver's scope classification);
- ``v04``: the same stains in the opposite source order give the same per-ID numbers as the
  unswapped order (never mixed by C index);
- the conformance plan section 4 row "Histogram table v5" on ``v13``, ``v04``, ``v05``, ``v06``
  and ``v17`` trees, and on a channel ID containing ``_``;
- one discrepant image inside a unit is still refused (``R_UNIT_NOT_HOMOGENEOUS``);
- units of different image types (``v29``) stay refused (``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED``).
"""

import csv
import json
import zlib
from pathlib import Path

import numpy as np
import pytest
import tifffile
from morphobricks_normalization.csv_io import channel_columns, read_plane_histogram_table
from morphobricks_normalization.fitting import ChannelScopeIncompleteError, fit
from morphobricks_normalization.settings import NormalizationSettings

from described_tree import run_described
from description_tree import MAPPING
from morphobricks_preprocessing import Request, RunInputs
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.report import group_summaries
from morphobricks_preprocessing.testing_described import unit_folder
from spec_pin import fixtures

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}
LEVELS = {"DNA": 200, "ACTIN": 40, "PAXILLIN": 120, "PHASE": 10, "TUB": 160, "EXTRA": 80}
"""Each stain's pixels lie in ``[level, level + 20)``: a mixed channel shows at once."""
REPEATS = ("Biological_repeat_1", "Biological_repeat_2", "Biological_repeat_3")


def _level(channel):
    return LEVELS.get(channel, LEVELS["ACTIN"])  # a renamed ACTIN keeps its level


def write_stained(root, description, images=2):
    """Write ``images`` CYX images per unit; a channel's pixels depend on its ID, not its index.

    The pixels of channel ``ID`` of image ``i`` of a folder are the same whatever position the
    unit lists the ID at, so two descriptions that order the same stains differently describe
    the same measurements.
    """
    found = []
    for repeat_id, repeat in sorted(description.repeats.items()):
        for condition_id, member in sorted(repeat.members.items()):
            for unit in member.technical_repeats.values():
                folder = unit_folder(description, repeat_id, condition_id, unit)
                for index in range(images):
                    rel = (folder / f"img{index}.tif").as_posix()
                    write_image(Path(root) / rel, rel, unit.channels)
                    found.append(rel)
    return sorted(found)


def write_image(path, rel, channels):
    planes = []
    for channel in channels:
        rng = np.random.default_rng(zlib.crc32(f"{rel}|{channel}".encode()))
        planes.append(_level(channel) + rng.integers(0, 20, (8, 8)))
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.stack(planes).astype(np.uint8)
    tifffile.imwrite(path, data, photometric="minisblack", metadata={"axes": "CYX"})


def described_run(tmp_path, data, images=2):
    """Publish ``data``, write its stained tree and run; return (result, output, images)."""
    description = description_from_bytes(data)
    root, out = tmp_path / "exp", tmp_path / "out"
    out.mkdir(parents=True)
    (out / "morphobricks.toml").write_bytes(data)
    paths = write_stained(root, description, images)
    return run_described(root, MAPPING, output_folder=out), out, paths


def table_rows(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def extra_stain(extra_everywhere=False):
    """The owner's case: DNA, ACTIN, TUB everywhere; EXTRA only in Biological_repeat_2."""
    lines = [
        'schema = "morphobricks-experiment"',
        "schema_version = 1",
        "",
        '[conditions.Control]\nsource_folder = "Control"\n',
        '[conditions.Treated]\nsource_folder = "Treated"\n',
        *(f"[channels.{channel}]\n" for channel in ("DNA", "ACTIN", "TUB", "EXTRA")),
    ]
    for repeat in REPEATS:
        stained = extra_everywhere or repeat == "Biological_repeat_2"
        channels = '["DNA", "ACTIN", "TUB"' + (', "EXTRA"]' if stained else "]")
        lines.append(f'[repeats.{repeat}]\nconfirmed = true\nconfirmed_on = "2026-11-11"\n')
        for condition in ("Control", "Treated"):
            lines += [
                f'[repeats.{repeat}.members.{condition}]\nsource_repeat = "{repeat}"\n'
                "image_count = 2\n",
                f"[repeats.{repeat}.members.{condition}.technical_repeats.single]\n"
                f'source_folder = "single"\nchannels = {channels}\n',
            ]
    lines += [
        "[used_ids]",
        'conditions = ["Control", "Treated"]',
        'channels = ["ACTIN", "DNA", "EXTRA", "TUB"]',
        "repeats = [" + ", ".join(f'"{r}"' for r in REPEATS) + "]",
        'technical_repeats = ["single"]',
        "",
    ]
    return "\n".join(lines).encode("utf-8")


# --- the owner's extra-stain case --------------------------------------------------------------


@pytest.fixture
def extra_run(tmp_path):
    result, out, paths = described_run(tmp_path, extra_stain())
    assert result.written, [(i.code, i.message) for i in result.issues]
    return result, out, paths


def test_the_extra_stain_is_blank_never_zero_where_it_was_not_acquired(extra_run):
    result, _, paths = extra_run
    rows = table_rows(result.csv_path)
    assert len(rows) == len(paths)  # one row per plane (N41): one plane per 2-D image
    header = list(rows[0])
    ids = [c.removeprefix("Histogram_ValidPixelCount_") for c in header if "ValidPixelCount" in c]
    assert ids == ["ACTIN", "DNA", "EXTRA", "TUB"]  # every declared ID, sorted
    for row in rows:
        assert row["schema_version"] == "5"
        extra = {c: row[c] for c in channel_columns("EXTRA", int(row["bin_count"]))}
        if row["biological_repeat"] == "Biological_repeat_2":
            assert row["channels"] == "DNA;ACTIN;TUB;EXTRA" and row["channel_count"] == "4"
            assert all(v != "" for v in extra.values())
            assert 80 <= int(row["Intensity_MinIntensity_EXTRA"]) < 100
        else:
            assert row["channels"] == "DNA;ACTIN;TUB" and row["channel_count"] == "3"
            assert set(extra.values()) == {""}  # blank: not measured, never a zero histogram
    table = read_plane_histogram_table(result.csv_path)
    assert table.channel_ids == ("ACTIN", "DNA", "EXTRA", "TUB")


def test_the_manifest_records_each_image_s_channels_and_the_coverage(extra_run):
    result, _, _ = extra_run
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == "5" and manifest["schema_version"] == "5"
    by_image = {entry["relative_path"]: entry["channel_ids"] for entry in manifest["files"]}
    assert by_image["Control/Biological_repeat_2/img0.tif"] == ["DNA", "ACTIN", "TUB", "EXTRA"]
    assert by_image["Treated/Biological_repeat_3/img1.tif"] == ["DNA", "ACTIN", "TUB"]
    coverage = manifest["experiment_description"]["channel_coverage"]
    assert {repeat: entry["EXTRA"]["status"] for repeat, entry in coverage.items()} == {
        "Biological_repeat_1": "unavailable",
        "Biological_repeat_2": "full",
        "Biological_repeat_3": "unavailable",
    }
    assert {coverage[r]["DNA"]["status"] for r in REPEATS} == {"full"}
    units = manifest["experiment_description"]["units"]
    assert {tuple(u["channels"]) for u in units} == {
        ("DNA", "ACTIN", "TUB"),
        ("DNA", "ACTIN", "TUB", "EXTRA"),
    }
    # Every repeat has the extra stain in all or none of its acquisitions: nothing partial.
    assert "partial_channel_coverage" not in {i.code for i in result.issues}


def test_normalization_fits_per_repeat_and_refuses_a_global_fit_unless_opted_in(extra_run):
    result, _, _ = extra_run
    records = read_plane_histogram_table(result.csv_path).records
    settings = NormalizationSettings(method="percentile", scope="biological_repeat")
    for repeat in REPEATS:
        rows = [r for r in records if r.biological_repeat == repeat]
        fitted = fit(rows, settings, group=repeat)
        stained = repeat == "Biological_repeat_2"
        expected = ("ACTIN", "DNA", "EXTRA", "TUB") if stained else ("ACTIN", "DNA", "TUB")
        assert fitted.channels == expected
    with pytest.raises(ChannelScopeIncompleteError, match="EXTRA") as refused:
        fit(records, settings, group="all")
    assert set(refused.value.channels) == {"EXTRA"}
    assert len(refused.value.channels["EXTRA"]) == 8  # the planes of repeats 1 and 3
    opted = NormalizationSettings(
        method="percentile", scope="biological_repeat", partial_channels="planes_with_channel"
    )
    assert fit(records, opted, group="all").channels == ("ACTIN", "DNA", "EXTRA", "TUB")
    # The three shared stains are fitted over every repeat without any choice.
    assert fit(records, settings, group="all", channels=("ACTIN", "DNA", "TUB")).channels == (
        "ACTIN",
        "DNA",
        "TUB",
    )


def test_compare_groups_refuses_the_extra_stain_over_all_repeats_unless_chosen(extra_run):
    result, _, _ = extra_run
    records = read_plane_histogram_table(result.csv_path).records
    by_repeat = group_summaries(records, "biological_repeat")
    assert {s.channel for s in by_repeat if s.labels[1] == "Biological_repeat_2"} == set(LEVELS) - {
        "PAXILLIN",
        "PHASE",
    }
    assert "EXTRA" not in {s.channel for s in by_repeat if s.labels[1] != "Biological_repeat_2"}
    with pytest.raises(ChannelScopeIncompleteError, match="EXTRA .present in 2 of 6 planes"):
        group_summaries(records, "condition")
    chosen = group_summaries(records, "condition", partial_channels="planes_with_channel")
    extra = [s for s in chosen if s.channel == "EXTRA"]
    assert [(s.channel_histogram_count, s.planes_without_channel) for s in extra] == [(2, 4)] * 2


def test_the_resolver_classifies_the_extra_stain_per_repeat(extra_run):
    _, out, _ = extra_run
    inputs = RunInputs.open(out)
    shared = inputs.resolve_request(Request(("DNA", "ACTIN", "TUB")))
    assert not shared.refused
    resolved = inputs.resolve_request(Request(("DNA", "EXTRA")))
    assert [(r.code, r.subject) for r in resolved.refusals] == [("A_SCOPE_INCOMPLETE", "EXTRA")]
    assert "present in 4 of 12 acquisitions" in resolved.refusals[0].message
    status = {c.repeat_group: c.status for c in resolved.coverage if c.subject == "EXTRA"}
    assert status == {
        "Biological_repeat_1": "unavailable",
        "Biological_repeat_2": "full",
        "Biological_repeat_3": "unavailable",
    }
    stained = [f for f in inputs.fovs() if f.repeat_group == "Biological_repeat_2"]
    assert {inputs.channel_index(f, "EXTRA") for f in stained} == {3}


def test_a_rerun_reuses_every_row_and_writes_the_same_bytes(extra_run, tmp_path):
    result, out, _ = extra_run
    table = result.csv_path.read_bytes()
    again = run_described(tmp_path / "exp", MAPPING, output_folder=out)
    assert again.written and again.read == () and again.csv_path.read_bytes() == table
    full = run_described(tmp_path / "exp", MAPPING, output_folder=out, mode="full")
    assert full.written and full.csv_path.read_bytes() == table


def test_one_discrepant_image_in_a_unit_is_refused(tmp_path):
    root, out = tmp_path / "exp", tmp_path / "out"
    data = extra_stain()
    out.mkdir()
    (out / "morphobricks.toml").write_bytes(data)
    write_stained(root, description_from_bytes(data))
    rel = "Control/Biological_repeat_2/img1.tif"  # one FOV without the extra stain
    write_image(root / rel, rel, ("DNA", "ACTIN", "TUB"))
    result = run_described(root, MAPPING, output_folder=out)
    assert not result.written and not (out / "plane_histograms.csv").exists()
    refused = [i for i in result.issues if i.code == "R_UNIT_NOT_HOMOGENEOUS"]
    assert refused and rel in refused[0].paths
    assert "R_CHANNEL_COUNT_MISMATCH" not in {i.code for i in result.issues}  # §8 order


def test_an_image_whose_count_differs_from_its_unit_is_refused(tmp_path):
    root, out = tmp_path / "exp", tmp_path / "out"
    data = extra_stain()
    out.mkdir()
    (out / "morphobricks.toml").write_bytes(data)
    write_stained(root, description_from_bytes(data))
    for index in range(2):  # the whole unit lacks the stain its description lists
        rel = f"Treated/Biological_repeat_2/img{index}.tif"
        write_image(root / rel, rel, ("DNA", "ACTIN", "TUB"))
    result = run_described(root, MAPPING, output_folder=out)
    assert not result.written
    assert "R_CHANNEL_COUNT_MISMATCH" in {i.code for i in result.issues}


# --- v04: the same stains, swapped source order -------------------------------------------------


def _unswapped(data):
    """``v04`` with DrugA's list in CTRL's order: the same stains, the other source order."""
    swapped = b'source_folder = "single"\nchannels = ["ACTIN", "DNA"]'
    assert data.count(swapped) == 1
    return data.replace(swapped, b'source_folder = "single"\nchannels = ["DNA", "ACTIN"]')


def _by_id(records):
    return {
        (r.plane_id, channel): r.channel_histogram(channel)
        for r in records
        for channel in r.channels
    }


def test_v04_swapped_order_gives_the_per_id_numbers_of_the_unswapped_order(tmp_path):
    swapped, _, _ = described_run(tmp_path / "a", VALID["v04"].data)
    plain, _, _ = described_run(tmp_path / "b", _unswapped(VALID["v04"].data))
    assert swapped.written and plain.written
    one = read_plane_histogram_table(swapped.csv_path).records
    two = read_plane_histogram_table(plain.csv_path).records
    assert {r.channels for r in one} == {("DNA", "ACTIN"), ("ACTIN", "DNA")}
    assert {r.channels for r in two} == {("DNA", "ACTIN")}
    a, b = _by_id(one), _by_id(two)
    assert a.keys() == b.keys()
    for key, histogram in a.items():
        other = b[key]
        assert (histogram.pixel_min, histogram.pixel_max) == (other.pixel_min, other.pixel_max)
        np.testing.assert_array_equal(histogram.counts, other.counts)
    assert group_summaries(one, "condition") == group_summaries(two, "condition")
    # ACTIN and DNA are never mixed: each stain keeps its own range in both conditions.
    for summary in group_summaries(one, "condition"):
        level = _level(summary.channel)
        assert level <= summary.minimum and summary.maximum < level + 20


# --- conformance plan section 4, "Histogram table v5" -----------------------------------------


def _renamed(data, old=b"ACTIN", new=b"PAX_488"):
    return data.replace(old, new)


@pytest.mark.parametrize(
    "name, data",
    [
        ("v13", VALID["v13"].data),
        ("v04", VALID["v04"].data),
        ("v05", VALID["v05"].data),
        ("v06", VALID["v06"].data),
        ("v17", VALID["v17"].data),
        ("v04_underscore", _renamed(VALID["v04"].data)),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_histogram_table_v5_on_the_fixture_trees(tmp_path, name, data):
    description = description_from_bytes(data)
    result, _, paths = described_run(tmp_path, data, images=1 if name == "v13" else 2)
    assert result.written, [(i.code, i.message) for i in result.issues]
    rows = table_rows(result.csv_path)
    assert len(rows) == len(paths)  # one row per plane
    header = list(rows[0])
    declared = sorted(description.channels)
    ids = [c.removeprefix("Histogram_ValidPixelCount_") for c in header if "ValidPixelCount" in c]
    assert ids == declared and "failed_channels" not in header
    assert header.index("channels") == header.index("schema_version") + 1
    units = {}
    for repeat_id, repeat in description.repeats.items():
        for condition_id, member in repeat.members.items():
            for unit in member.technical_repeats.values():
                units[unit_folder(description, repeat_id, condition_id, unit).as_posix()] = unit
    for row in rows:
        listed = units[row["source_relative_path"].rsplit("/", 1)[0]].channels
        assert row["schema_version"] == "5" and row["channels"] == ";".join(listed)
        for channel in declared:
            cells = [row[column] for column in channel_columns(channel, int(row["bin_count"]))]
            if channel in listed:
                assert all(cell != "" for cell in cells)
                low = int(row[f"Intensity_MinIntensity_{channel}"])
                assert _level(channel) <= low < _level(channel) + 20  # never another stain
            else:
                assert cells and set(cells) == {""}  # absent: blank, never zero
    records = read_plane_histogram_table(result.csv_path).records
    # Pooling by ID per technical repeat (every plane of a unit has the unit's channels).
    for summary in group_summaries(records, "technical_repeat"):
        level = _level(summary.channel)
        assert level <= summary.minimum and summary.maximum < level + 20


def test_v17_partial_repeat_is_recorded_and_its_statistics_need_a_choice(tmp_path):
    result, out, _ = described_run(tmp_path, VALID["v17"].data)
    assert result.written
    (warned,) = [i for i in result.issues if i.code == "partial_channel_coverage"]
    assert warned.paths == ("R002: PHASE in 1 of 2 acquisitions",)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    coverage = manifest["experiment_description"]["channel_coverage"]
    assert coverage["R002"]["PHASE"] == {
        "status": "partial",
        "units_with": 1,
        "units": 2,
        "lacking": ["CTRL/acquisition_2"],
    }
    assert coverage["R001"]["PHASE"]["status"] == "unavailable"
    records = read_plane_histogram_table(result.csv_path).records
    with pytest.raises(ChannelScopeIncompleteError, match="PHASE"):
        group_summaries(records, "biological_repeat")
    resolved = RunInputs.open(out).resolve_request(Request(("PHASE",)))
    assert {c.repeat_group: c.status for c in resolved.coverage} == {
        "R001": "unavailable",
        "R002": "partial",
    }


def test_v29_units_of_different_image_types_stay_refused():
    # Kept refused: the owner enabled different channel lists, not RGB beside channel units.
    from morphobricks_preprocessing.description_layouts import declared_layouts, is_heterogeneous

    assert is_heterogeneous(declared_layouts(description_from_bytes(VALID["v29"].data)))
    for name in ("v04", "v05", "v06", "v17"):
        assert not is_heterogeneous(declared_layouts(description_from_bytes(VALID[name].data)))
