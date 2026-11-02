"""PP-8c item 4: a stopped run says what it wrote, not "Nothing was written".

Every run that holds the lock writes its report, also when it stops (design v0.4 §4), and
the CLI ``run`` writes the design template when it stops on the design. The summary said
"Nothing was written". Now it names what was written and says no table or manifest was.
"""

import numpy as np

from morphobricks_preprocessing import prefill_mapping, run_experiment
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.report import run_summary
from morphobricks_preprocessing.run.lock import output_lock
from tiff_tree import write_data


def experiment(tmp_path):
    root = tmp_path / "exp"
    for seed, rel in enumerate(["ctrl/r1/a.tif", "drug/r1/a.tif"]):
        data = np.random.default_rng(seed).integers(0, 200, (6, 8), dtype=np.uint8)
        write_data(root / rel, data, "YX")
    return root


def test_the_cli_names_the_report_and_the_design_template(tmp_path, capsys):
    root, output = experiment(tmp_path), tmp_path / "out"
    assert main(["run", str(root), "--output", str(output), "--axes", "YX"]) == 1
    out = capsys.readouterr().out
    assert "Nothing was written" not in out
    assert "The run stopped (1 error): no table or manifest was written." in out
    assert "discovery_report.json and discovery_report.txt (the report)" in out
    assert "experiment_design.csv (the experiment design to confirm)" in out
    assert {p.name for p in output.iterdir()} == {
        "discovery_report.json",
        "discovery_report.txt",
        "experiment_design.csv",
    }


def test_a_stopped_api_run_records_that_its_report_was_written(tmp_path):
    root, output = experiment(tmp_path), tmp_path / "out"
    result = run_experiment(root, prefill_mapping("ZYX"), output_folder=output)  # wrong roles
    assert not result.written and result.report_written
    text = run_summary(result)
    assert text.startswith("The run stopped (")
    assert "no table or manifest was written" in text
    assert f"Written to {output}: discovery_report.json and discovery_report.txt" in text
    assert "experiment_design.csv" not in text


def test_a_run_refused_by_the_lock_wrote_nothing(tmp_path):
    root, output = experiment(tmp_path), tmp_path / "out"
    output.mkdir()
    with output_lock(output):
        result = run_experiment(root, prefill_mapping("YX"), output_folder=output)
    assert not result.written and not result.report_written
    assert "Nothing was written." in run_summary(result)
