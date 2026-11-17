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


def _windows_peak(kernel32: object = None, psapi: object = None) -> int:
    """Return this process's peak working set in bytes (Windows ``GetProcessMemoryInfo``).

    ``K32GetProcessMemoryInfo`` of kernel32 (Windows 7 and later), else psapi's
    ``GetProcessMemoryInfo``, both with their types declared: the pseudo-handle of
    ``GetCurrentProcess`` is pointer-sized, and an undeclared call passes it truncated to 32
    bits on 64-bit Windows and fails. ``kernel32`` and ``psapi`` are for tests.

    Raises
    ------
    OSError
        If the call fails, or gives no peak: never a silent 0.
    """
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

    kernel32 = kernel32 or ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.argtypes = []
    query = getattr(kernel32, "K32GetProcessMemoryInfo", None)
    if query is None:
        query = (psapi or ctypes.WinDLL("psapi", use_last_error=True)).GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
    query.restype = wintypes.BOOL
    counters = _Counters()
    counters.cb = ctypes.sizeof(_Counters)
    if not query(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        code = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0
        raise OSError(f"GetProcessMemoryInfo failed (error {code}): no peak working set")
    if counters.PeakWorkingSetSize <= 0:
        raise OSError("GetProcessMemoryInfo gave no peak working set")
    return int(counters.PeakWorkingSetSize)
