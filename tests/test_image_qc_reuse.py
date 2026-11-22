"""Image QC rows are reused only under the block layout they were measured with (contract §19).

The memory limit changes no histogram row, but the neighbour metrics of a plane read in blocks
leave out the pairs across a block edge, so a row measured under another limit is not the row
a fresh run would write. Reuse and resume compare the limit (when planes are read in blocks)
and the full identity of each row.
"""

import json

import pytest

from described_tree import run_described
from morphobricks_preprocessing import run as run_module
from morphobricks_preprocessing.discovery_report import REPORT_FILENAME
from morphobricks_preprocessing.image_qc_record import IMAGE_QC_FILENAME
from morphobricks_preprocessing.image_qc_table import QcRow, read_image_qc, write_image_qc
from morphobricks_preprocessing.planes import plane_layout
from morphobricks_preprocessing.run.staging import STAGING_FOLDER
from qc_tree import CTYX, IMAGES, interrupt_at_second_file, make_experiment, run, sha256


@pytest.fixture
def experiment(tmp_path):
    return make_experiment(tmp_path)


@pytest.fixture
def small_limit(experiment):
    """A memory limit that reads every plane of the experiment in blocks."""
    return plane_layout(experiment / IMAGES[0], CTYX, memory_limit=1).block_memory_estimate


def fresh_table(tmp_path, experiment, name, **kwargs):
    """Return the table a fresh run writes into a new folder with ``kwargs``."""
    out = tmp_path / name
    run(experiment, out, **kwargs)
    return (out / IMAGE_QC_FILENAME).read_bytes()


def test_one_folder_whole_then_blocked_then_whole_equals_fresh_runs(
    tmp_path,
    experiment,
    small_limit,
):
    whole = fresh_table(tmp_path, experiment, "fresh_whole")
    blocked = fresh_table(tmp_path, experiment, "fresh_blocked", memory_limit=small_limit)
    assert whole != blocked  # the block edges are visible in the neighbour metrics
    out = tmp_path / "out"
    run(experiment, out)
    assert (out / IMAGE_QC_FILENAME).read_bytes() == whole
    result = run(experiment, out, memory_limit=small_limit)
    assert "memory limit changed" in result.rebuild_reason
    assert sorted(result.read) == sorted(IMAGES)
    assert (out / IMAGE_QC_FILENAME).read_bytes() == blocked
    result = run(experiment, out)
    assert "memory limit changed" in result.rebuild_reason
    assert (out / IMAGE_QC_FILENAME).read_bytes() == whole


def test_a_limit_that_leaves_every_plane_whole_still_keeps_the_rows(tmp_path, experiment):
    out = tmp_path / "out"
    run(experiment, out)
    result = run(experiment, out, memory_limit=2**29)
    assert result.rebuild_reason is None and result.read == ()


def test_the_same_blocked_limit_reuses_the_rows(tmp_path, experiment, small_limit):
    out = tmp_path / "out"
    run(experiment, out, memory_limit=small_limit)
    result = run(experiment, out, memory_limit=small_limit)
    assert result.rebuild_reason is None and result.read == ()


@pytest.mark.parametrize("first_blocked", [True, False])
def test_staged_rows_are_not_resumed_under_another_limit(
    tmp_path,
    experiment,
    small_limit,
    monkeypatch,
    first_blocked,
):
    limits = (small_limit, None) if first_blocked else (None, small_limit)
    extra = [{} if limit is None else {"memory_limit": limit} for limit in limits]
    expected = fresh_table(tmp_path, experiment, "fresh", **extra[1])
    out, read = tmp_path / "out", interrupt_at_second_file(monkeypatch)
    with pytest.raises(RuntimeError, match="stopped"):
        run_described(experiment, CTYX, output_folder=out, **extra[0])
    assert any(p.name.endswith(".qc.csv") for p in (out / STAGING_FOLDER).iterdir())
    monkeypatch.setattr(run_module.resume, "_read_file", read)
    result = run(experiment, out, **extra[1])
    assert result.resumed == ()
    report = json.loads((out / REPORT_FILENAME).read_text(encoding="utf-8"))
    assert "block layout" in report["staged_discarded"][IMAGES[0]]
    assert (out / IMAGE_QC_FILENAME).read_bytes() == expected


def test_a_table_whose_rows_name_other_planes_than_the_histogram_table_is_not_reused(
    tmp_path,
    experiment,
):
    out = tmp_path / "out"
    run(experiment, out)
    table, manifest_path = out / IMAGE_QC_FILENAME, out / "run_manifest.json"
    good = table.read_bytes()
    rows = read_image_qc(table)
    # Same plane ID and channel in every row, another position identifier in one.
    first = rows[0]
    identity = ("elsewhere", *first.identity[1:])
    rows[0] = QcRow(identity, first.read_in_blocks, first.qc)
    write_image_qc(rows, table)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["image_qc"]["sha256"] = sha256(table)
    manifest_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    assert read_image_qc(table)[0].key == QcRow(identity, False, first.qc).key
    result = run(experiment, out)
    assert result.rebuild_reason == "the earlier image QC table does not match the histogram table"
    assert table.read_bytes() == good
