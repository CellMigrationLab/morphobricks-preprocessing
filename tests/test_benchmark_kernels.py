"""The kernel benchmarks (#106) and the description-reader benchmark (#505) still run.

Each tool runs once in its ``--quick`` smoke mode (small inputs, one timed call): every case
must give a SHA-256 digest that every call agreed on, and the read layouts of one plane must
give the same digest (they read the same pixels). No timing is checked.
"""

import importlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import numpy as np

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))  # as when they run: their own folder first
harness = importlib.import_module("benchmark_harness")
kernels = importlib.import_module("benchmark_kernels")
reads_module = importlib.import_module("benchmark_reads")
description_read = importlib.import_module("benchmark_description_read")

SHA256 = re.compile(r"[0-9a-f]{64}")


def _zarr_installed():
    return importlib.util.find_spec("zarr") is not None


def test_every_kernel_case_runs_in_quick_mode_and_gives_a_digest(tmp_path, capsys):
    report = kernels.main(["--quick", "--repeats", "1", "--folder", str(tmp_path)])
    assert json.loads(capsys.readouterr().out) == report
    assert report["mode"] == "quick" and report["machine"]["processor"]
    groups = {case["group"] for case in report["cases"]}
    assert groups == set(kernels.GROUPS)
    for case in report["cases"]:
        if "skipped" in case:
            assert case["name"].startswith("zarr") and not _zarr_installed(), case
            continue
        assert SHA256.fullmatch(case["digest"]) and case["digest_stable"], case
        assert case["wall_seconds"]["median"] >= 0 and case["peak_traced_bytes"] >= 0
    for kind in ("plane", "windows"):
        digests = {c["digest"] for c in report["cases"] if c["name"].startswith(f"{kind}_")}
        assert len(digests) == 1, kind  # every layout reads the same pixels
    reads = [c for c in report["cases"] if c["group"] == "reads" and "skipped" not in c]
    assert len(reads) == 2 * (len(reads_module.TIFF_LAYOUTS) + _zarr_installed())


def test_the_description_benchmark_runs_in_quick_mode_and_profiles(capsys):
    report = description_read.main(["--quick", "--top", "5"])
    assert json.loads(capsys.readouterr().out) == report
    assert [s["repeats_in_description"] for s in report["sizes"]] == [30, 100]
    for size in report["sizes"]:
        assert SHA256.fullmatch(size["digest"]) and size["digest_stable"]
        assert "5._repeat_membership" in size["phase_seconds"]
    assert len(report["growth"]) == 1
    assert len(report["profile"]["top"]) == 5


def test_the_description_digest_is_that_of_the_accepted_model():
    data = description_read.description_bytes(3)
    phase, issues, model = description_read.read(data)
    assert (phase, issues) == (5, ()) and model == data  # canonical bytes read back the same


def test_the_digest_tells_types_orders_and_values_apart():
    digest = harness.digest
    values = np.arange(6, dtype=np.uint16)
    assert digest(values) == digest(values.copy())
    assert digest(values) != digest(values.astype(np.int16))  # same bytes, other type
    assert digest(values) != digest(values.reshape(2, 3))
    assert digest({"a": 1, "b": 2}) != digest({"b": 2, "a": 1})
    assert digest((1.0,)) != digest((1.0000000000000002,))
    assert digest([1]) != digest([True]) and digest(None) != digest("None")


def test_measure_reports_a_call_that_does_not_repeat_itself():
    calls = iter(range(10))
    found = harness.measure(lambda: next(calls), 2, warmup=0, trace=False)
    assert not found["digest_stable"] and found["peak_traced_bytes"] is None
    assert len(found["wall_seconds"]["values"]) == 2
