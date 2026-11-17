"""A process's own peak resident memory, for ``benchmark_run_workers.py`` (review F3).

``ru_maxrss`` on Linux carries the parent's high-water mark across ``fork`` and ``exec``, so a
child run started by a driver that wrote a large dataset reported the driver's peak. The peak
is read where it is the process's own, and its source is reported.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def peak_rss(status: Path = Path("/proc/self/status")) -> tuple[int, str]:
    """Return this process's own peak resident memory in bytes, and where it was read.

    Linux: ``VmHWM`` of ``status`` (kB), this process only (it starts again at ``exec``).
    Windows: ``PeakWorkingSetSize`` (``GetProcessMemoryInfo``). Elsewhere: ``ru_maxrss``,
    bytes on macOS and KiB on other systems, which may include the peak of the process that
    started this one: its source says so.
    """
    try:
        for line in status.read_text(encoding="ascii").splitlines():
            name, _, value = line.partition(":")
            if name == "VmHWM" and value.split() and value.split()[0].isdigit():
                return int(value.split()[0]) * 1024, "VmHWM"
    except (OSError, UnicodeDecodeError):
        pass
    if os.name == "nt":
        return _windows_peak(), "PeakWorkingSetSize"
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    unit = 1 if sys.platform == "darwin" else 1024
    return peak * unit, "ru_maxrss (may include the parent process's peak)"


def _windows_peak() -> int:
    """Return this process's peak working set in bytes (Windows ``GetProcessMemoryInfo``)."""
    import ctypes
    from ctypes import wintypes

    class _Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = _Counters()
    counters.cb = ctypes.sizeof(_Counters)
    process = ctypes.windll.kernel32.GetCurrentProcess()
    ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb)
    return int(counters.PeakWorkingSetSize)
