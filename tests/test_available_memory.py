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


def _group(folder, limit, usage, v1=False):
    folder.mkdir(parents=True, exist_ok=True)
    names = (
        ("memory.limit_in_bytes", "memory.usage_in_bytes")
        if v1
        else ("memory.max", "memory.current")
    )
    for name, value in zip(names, (limit, usage), strict=True):
        (folder / name).write_text(f"{value}\n", encoding="ascii")


@pytest.mark.parametrize("v1", [False, True], ids=["v2", "v1"])
def test_a_limit_on_the_process_group_below_the_mount_counts(tmp_path, v1):
    # A batch job or a systemd slice without a cgroup namespace: the mount is the root group
    # (no limit), the process's own group, named by /proc/self/cgroup, sets the limit. Before
    # the fix only the mount was read, so the whole machine's MemAvailable was returned.
    meminfo, cgroup = _files(tmp_path, MEMINFO)
    mount = cgroup / "memory" if v1 else cgroup
    _group(mount, 2**63 - 4096 if v1 else "max", 7, v1)
    _group(mount / "job_7", 6 * 2**30, 2**30, v1)  # the job: 5 GiB left
    _group(mount / "job_7" / "step_0", "max" if not v1 else 2**63 - 4096, 3, v1)
    membership = tmp_path / "self_cgroup"
    line = "4:memory:/job_7/step_0\n0::/\n" if v1 else "0::/job_7/step_0\n"
    membership.write_text(line, encoding="ascii")
    assert available_memory(meminfo, cgroup, membership) == 5 * 2**30


def test_a_parent_group_with_less_room_than_the_process_group_wins(tmp_path):
    meminfo, cgroup = _files(tmp_path, MEMINFO)
    _group(cgroup / "slice", 4 * 2**30, 3 * 2**30)  # the slice: 1 GiB left, shared
    _group(cgroup / "slice" / "session", 8 * 2**30, 2**30)
    membership = tmp_path / "self_cgroup"
    membership.write_text("0::/slice/session\n", encoding="ascii")
    assert available_memory(meminfo, cgroup, membership) == 2**30


def test_an_unreadable_or_odd_membership_falls_back_to_the_mount(tmp_path):
    meminfo, cgroup = _files(tmp_path, MEMINFO, v2=(8 * 2**30, 3 * 2**30))
    for text in (None, "", "garbage\n", "0::/../../etc\n", "0::/missing/group\n"):
        membership = tmp_path / "self_cgroup"
        membership.unlink(missing_ok=True)
        if text is not None:
            membership.write_text(text, encoding="ascii")
        assert available_memory(meminfo, cgroup, membership) == 5 * 2**30
