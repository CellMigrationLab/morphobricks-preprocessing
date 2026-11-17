"""The benchmark protocol of ``tools/benchmark_run_workers.py`` (review items F1 to F3).

Its run order is counterbalanced and seeded (recorded), an acceptance run has at least 10
rounds (``--quick`` is the smoke mode), every run's peak RSS is kept as a distribution, the
speed-up is paired by round, and a run whose digests differ from the serial ones fails.
No run is made here: the tool's own functions are checked on made-up runs.
"""

import importlib.util
import sys
from collections import Counter
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "benchmark_run_workers.py"
sys.path.insert(0, str(TOOL.parent))  # as when it runs: its own folder first
_spec = importlib.util.spec_from_file_location("benchmark_run_workers", TOOL)
bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)
memory = sys.modules["benchmark_memory"]

WORKERS = ["1", "2", "4", "auto"]


def test_balanced_order_puts_every_configuration_in_every_position_per_block():
    orders = bench.round_orders(WORKERS, 8, "balanced", 0)
    for block in (orders[:4], orders[4:]):
        for position in range(4):
            assert sorted(order[position] for order in block) == sorted(WORKERS)
    assert all(sorted(order) == sorted(WORKERS) for order in orders)
    assert bench.round_orders(WORKERS, 8, "balanced", 0) == orders  # seeded: reproducible
    assert bench.round_orders(WORKERS, 8, "balanced", 1) != orders


def test_shuffled_and_fixed_orders():
    shuffled = bench.round_orders(WORKERS, 20, "shuffled", 3)
    assert shuffled == bench.round_orders(WORKERS, 20, "shuffled", 3)
    assert len({tuple(order) for order in shuffled}) > 1
    assert Counter(order[0] for order in shuffled).keys() == set(WORKERS)
    assert bench.round_orders(WORKERS, 3, "fixed", 0) == [WORKERS] * 3


def _parse(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["benchmark_run_workers.py", *argv])
    return bench.parse_arguments()


def test_acceptance_needs_ten_rounds_and_quick_is_the_smoke_mode(monkeypatch, capsys):
    assert _parse(monkeypatch).repeats == 12  # 1 2 4 auto: three whole blocks of four
    assert _parse(monkeypatch, "--workers", "2", "auto").repeats == 12  # three: four blocks
    assert _parse(monkeypatch, "--workers", "auto").repeats == 10  # two: five blocks
    assert _parse(monkeypatch, "--repeats", "10").repeats == 10
    assert _parse(monkeypatch, "--quick").repeats == 3
    assert _parse(monkeypatch, "--quick", "--repeats", "1").repeats == 1
    with pytest.raises(SystemExit):
        _parse(monkeypatch, "--repeats", "9")
    assert "--repeats 10 or more" in capsys.readouterr().err
    args = _parse(monkeypatch, "--workers", "4", "auto", "4")
    assert args.workers == ["1", "4", "auto"] and args.order == "balanced"


def test_pinning_is_taskset_where_it_exists(monkeypatch):
    monkeypatch.setattr(bench.shutil, "which", lambda name: None)
    assert bench.pin_prefix("0-3") == []
    args = _parse(monkeypatch, "--quick", "--cpus", "0-3")
    assert bench.protocol(args)["pinning"] == "unavailable"
    monkeypatch.setattr(bench.shutil, "which", lambda name: "/usr/bin/taskset")
    assert bench.pin_prefix("0-3") == ["/usr/bin/taskset", "-c", "0-3"]
    assert bench.pin_prefix(None) == []
    found = bench.protocol(args)
    assert found["pinning"] == "taskset -c 0-3" and found["mode"] == "quick"
    assert found["seed"] == 0 and "load_at_start" in found


def _run(wall, peak_mib, digest="a", workers=1):
    return {
        "workers": workers,
        "wall_seconds": wall,
        "peak_rss_bytes": peak_mib * bench.MIB,
        "load_before": [1.0, 1.0, 1.0],
        "digests": {"plane_histograms.csv": digest},
    }


def test_summary_pairs_by_round_and_keeps_every_peak_rss():
    rounds = [
        {"round": 1, "order": ["2", "1"], "runs": {"1": _run(10, 100), "2": _run(5, 150, "a", 2)}},
        {"round": 2, "order": ["1", "2"], "runs": {"1": _run(8, 110), "2": _run(8, 300, "a", 2)}},
    ]
    table, same = bench.summary(rounds, ["1", "2"])
    assert same
    two = table["2"]
    assert two["paired_speedup"]["values"] == [2.0, 1.0]  # each round's serial / its own
    assert two["paired_speedup"]["median"] == 1.5
    assert two["peak_rss_mib"] == {"min": 150, "median": 225, "max": 300, "values": [150, 300]}
    assert two["digests_equal_to_serial"] and table["1"]["paired_speedup"]["median"] == 1.0


def test_a_run_with_other_digests_is_not_identical():
    rounds = [{"round": 1, "order": ["1", "2"], "runs": {"1": _run(1, 1), "2": _run(1, 1, "b")}}]
    table, same = bench.summary(rounds, ["1", "2"])
    assert not same and not table["2"]["digests_equal_to_serial"]


def test_a_runs_peak_rss_is_its_own_not_the_drivers():
    """``ru_maxrss`` on Linux carries the parent's peak into a child: every run reported the
    driver's. The child's own peak (``VmHWM``, ``PeakWorkingSetSize``) is below a buffer the
    parent holds; where only ``ru_maxrss`` is offered (macOS) the report says so."""
    import json
    import subprocess

    size = 256 * 2**20
    buffer = bytearray(size)
    buffer[::4096] = b"\1" * len(range(0, size, 4096))  # every page touched: resident
    done = subprocess.run(
        [sys.executable, str(TOOL), "--peak"], capture_output=True, text=True, check=True
    )
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert buffer[0] == 1
    if found["peak_rss_source"] in ("VmHWM", "PeakWorkingSetSize"):
        assert 0 < found["peak_rss_bytes"] < size, found
    else:
        assert "may include the parent" in found["peak_rss_source"]


def test_peak_rss_reads_vmhwm_and_the_platform_unit_of_ru_maxrss(tmp_path, monkeypatch):
    status = tmp_path / "status"
    status.write_text("Name:\tpython\nVmHWM:\t  123456 kB\nVmRSS:\t 1 kB\n", encoding="ascii")
    assert memory.peak_rss(status) == (123456 * 1024, "VmHWM")
    if memory.os.name == "nt":
        return
    resource = pytest.importorskip("resource")

    class _Usage:
        ru_maxrss = 1000

    monkeypatch.setattr(resource, "getrusage", lambda who: _Usage())
    for platform, expected in (("darwin", 1000), ("linux", 1000 * 1024)):
        monkeypatch.setattr(memory.sys, "platform", platform)
        peak, source = memory.peak_rss(tmp_path / "missing")
        assert peak == expected and source.startswith("ru_maxrss")
