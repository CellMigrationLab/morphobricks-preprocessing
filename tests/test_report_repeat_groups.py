"""Issue #456: the run report counts the declared repeat groups, never the repeat folders.

The Spec pairs biological repeats by ``repeat_group``, never by folder label. The report said
"N biological repeats" from the distinct condition and repeat-folder labels, so 3 conditions
x 2 declared repeat groups whose folder names cross the groups gave 6 biological repeats.
Without declared groups the report and the notebook's folder scan now say so and count nothing.
"""

import json
from dataclasses import replace

import numpy as np

from described_tree import CONFIRMED_ON, tree_description
from morphobricks_preprocessing import discover_experiment, prefill_mapping, run_experiment
from morphobricks_preprocessing.description import Repeat
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME
from morphobricks_preprocessing.description_write import canonical_bytes
from morphobricks_preprocessing.discovery_report import REPORT_FILENAME, REPORT_TEXT_FILENAME
from morphobricks_preprocessing.notebook_quick_scan import scan_summary
from morphobricks_preprocessing.run.check_step import write_check_report
from morphobricks_preprocessing.run.preflight import preflight
from tiff_tree import write_data

# Folder labels cross the repeat groups: r2 is Day1 in B but Day2 in A; r1 is Day1 in A but
# Day2 in B; C's folders have names of their own. Four distinct labels, six repeat folders.
PAIRING = {
    "Day1": {"A": "r1", "B": "r2", "C": "r3"},
    "Day2": {"A": "r2", "B": "r1", "C": "r4"},
}


def experiment(tmp_path):
    root = tmp_path / "exp"
    seed = 0
    for members in PAIRING.values():
        for condition, folder in members.items():
            data = np.random.default_rng(seed).integers(1, 200, (6, 8), dtype=np.uint8)
            write_data(root / condition / folder / "a.tif", data, "YX")
            seed += 1
    return root


def publish_paired(root, output):
    """Publish the description with the two declared repeat groups of :data:`PAIRING`."""
    description = tree_description(preflight(root, output), prefill_mapping("YX"))
    by_folder = {
        (condition, member.source_repeat): member
        for repeat in description.repeats.values()
        for condition, member in repeat.members.items()
    }
    repeats = {
        group: Repeat(
            True,
            CONFIRMED_ON,
            {condition: by_folder[(condition, folder)] for condition, folder in members.items()},
        )
        for group, members in PAIRING.items()
    }
    used = {**description.used_ids, "repeats": tuple(sorted(repeats))}
    paired = replace(description, repeats=repeats, used_ids=used)
    output.mkdir(parents=True, exist_ok=True)
    (output / DESCRIPTION_FILENAME).write_bytes(canonical_bytes(paired))


def found_line(text):
    """Return the report's line of what was found (images, conditions, folders, repeats)."""
    return [line for line in text.splitlines() if line.startswith("6 images in ")]


def test_the_report_counts_two_declared_repeat_groups_not_the_folders(tmp_path):
    root, output = experiment(tmp_path), tmp_path / "out"
    publish_paired(root, output)
    result = run_experiment(root, prefill_mapping("YX"), output_folder=output)
    assert result.written, result.issues
    data = json.loads((output / REPORT_FILENAME).read_text())
    # The folders really cross the groups: four labels, six repeat folders, two groups.
    assert {
        (row["condition"], row["biological_repeat"]): row["repeat_group"] for row in data["design"]
    } == {(c, f): g for g, members in PAIRING.items() for c, f in members.items()}
    assert len({row["biological_repeat"] for row in data["design"]}) == 4
    text = (output / REPORT_TEXT_FILENAME).read_text()
    assert found_line(text) == [
        "6 images in 3 conditions, 6 folders; 2 biological repeats (repeat groups)"
    ]
    assert "6 biological repeats" not in text and "4 biological repeats" not in text
    assert data["repeat_groups"] == ["Day1", "Day2"]


def test_without_declared_repeat_groups_the_report_says_so(tmp_path):
    root, output = experiment(tmp_path), tmp_path / "out"
    path, refused = write_check_report(preflight(root, output), output)
    assert refused is None and path is not None
    text = (output / REPORT_TEXT_FILENAME).read_text()
    assert found_line(text) == [
        "6 images in 3 conditions, 6 folders; "
        "biological repeats not counted: no repeat groups declared"
    ]
    data = json.loads((output / REPORT_FILENAME).read_text())
    assert data["design"] is None and data["repeat_groups"] is None


def test_the_folder_scan_does_not_count_folders_as_biological_repeats(tmp_path):
    root = experiment(tmp_path)
    found = discover_experiment(root, tmp_path / "out")
    assert scan_summary(found, frozenset()) == (
        "6 images · 6 folders · 3 conditions · "
        "biological repeats not counted: no repeat groups declared yet"
    )


def test_one_declared_repeat_group_is_one_biological_repeat():
    from morphobricks_preprocessing.discovery_report import repeats_text

    assert repeats_text(["Day1"]) == "1 biological repeat (repeat group)"
    assert repeats_text(["Day1", "Day2"]) == "2 biological repeats (repeat groups)"
    assert repeats_text([]) == "biological repeats not counted: no repeat groups declared"
