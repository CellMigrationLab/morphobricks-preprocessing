"""available_memory: what the notebook's default memory cap reads (issue #107, owner 16 Nov 2026).

The notebook defaults to Auto under a cap of half the memory available when it starts; Auto
alone bounds the workers by the CPUs only. On Linux the figure is ``MemAvailable`` lowered to
what the control group still allows; it never comes out negative.
"""

import pytest

from morphobricks_preprocessing.run.workers import available_memory


def _files(tmp_path, meminfo=None, v2=None, v1=None):
    if meminfo is not None:
        (tmp_path / "meminfo").write_text(meminfo, encoding="ascii")
    cgroup = tmp_path / "cgroup"
    (cgroup / "memory").mkdir(parents=True)
    for names, values in (
        (("memory.max", "memory.current"), v2),
        (("memory/memory.limit_in_bytes", "memory/memory.usage_in_bytes"), v1),
    ):
        for name, value in zip(names, values or (), strict=False):
            (cgroup / name).write_text(f"{value}\n", encoding="ascii")
    return tmp_path / "meminfo", cgroup


MEMINFO = "MemTotal:       16000000 kB\nMemFree:  1000 kB\nMemAvailable:   12000000 kB\n"


@pytest.mark.parametrize(
    ("v2", "v1", "expected"),
    [
        (None, None, 12_000_000 * 1024),  # MemAvailable only
        (("max", 5), None, 12_000_000 * 1024),  # cgroup v2 without a limit
        ((8 * 2**30, 3 * 2**30), None, 5 * 2**30),  # v2: limit minus usage, below MemAvailable
        (None, (2**63 - 4096, 7), 12_000_000 * 1024),  # v1 "no limit"
        (None, (6 * 2**30, 2**30), 5 * 2**30),  # v1 limit minus usage
        ((2**30, 2 * 2**30), None, 0),  # over its limit: nothing left, never negative
    ],
)
def test_available_memory_is_meminfo_lowered_to_the_control_groups_room(tmp_path, v2, v1, expected):
    meminfo, cgroup = _files(tmp_path, MEMINFO, v2, v1)
    assert available_memory(meminfo, cgroup) == expected


def test_available_memory_without_meminfo_is_the_platform_figure_or_none(tmp_path):
    meminfo, cgroup = _files(tmp_path)
    found = available_memory(meminfo, cgroup)
    assert found is None or found > 0
