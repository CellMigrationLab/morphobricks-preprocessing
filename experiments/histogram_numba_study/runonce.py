"""One preprocessing run of the benchmark data in this process.

runonce.py BENCH OUT WORKERS VARIANT [MEMORY_LIMIT_MIB]
VARIANT: current | all | pixels | bins | numpy-bins (numba_patch modes).
Prints JSON: wall and CPU seconds of run_experiment, and the SHA-256 of every output file
under a fixed clock (written_at fixed as in tests/worker_bytes.py).
"""

import hashlib
import json
import resource
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "tools"))  # tools/benchmark_run_workers.py
import benchmark_run_workers as b  # noqa: E402
from morphobricks_preprocessing import prefill_mapping, run_experiment  # noqa: E402
from morphobricks_preprocessing.run import RunMode  # noqa: E402
from morphobricks_preprocessing.run import pipeline as pipeline_module  # noqa: E402

bench, out, workers, variant = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
limit = int(sys.argv[5]) if len(sys.argv) > 5 else 64


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.fromisoformat("2026-11-16T12:00:00+00:00").astimezone(tz)


pipeline_module.datetime = FixedClock
jit = 0.0
parts = variant.split("+")
_JOB = None


def _read_in_child(rel):
    """Read one image in a forked worker process (the job was inherited at fork)."""
    from morphobricks_preprocessing.run import resume as resume_module

    return resume_module._read_checked(_JOB, rel)


if "procs" in parts:  # measurement hack: image workers as forked processes, not threads
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    from morphobricks_preprocessing.run import resume as resume_module

    class _ForkPool(ProcessPoolExecutor):
        """Submit ``_read_checked(job, rel)`` as ``_read_in_child(rel)``: the job is not picklable."""

        def __init__(self, n, thread_name_prefix=None):
            super().__init__(n, mp_context=multiprocessing.get_context("fork"))

        def submit(self, fn, job, rel):
            global _JOB
            _JOB = job  # set before the first submit forks the children
            return super().submit(_read_in_child, rel)

    resume_module.ThreadPoolExecutor = _ForkPool
mode = [p for p in parts if p not in ("current", "procs")]
if mode:
    sys.path.insert(0, str(HERE))
    import numba_patch

    t = time.perf_counter()
    numba_patch.install(mode[0])
    jit = time.perf_counter() - t  # kernels compile lazily; see jit_cost.py
shutil.rmtree(out, ignore_errors=True)
out.mkdir(parents=True)
shutil.copy(bench / b.PUBLISHED_NAME, out / b.PUBLISHED_NAME)
opts = {"memory_limit": limit * 2**20}
if workers != "1":
    opts["mode"] = RunMode("restart", workers=workers if workers == "auto" else int(workers))


def _cpu():
    """User + system seconds of this process and its reaped children (process workers)."""
    a, c = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    return a.ru_utime + a.ru_stime + c.ru_utime + c.ru_stime


c0 = _cpu()
t = time.perf_counter()
res = run_experiment(bench / "experiment", prefill_mapping("TCYX"), output_folder=out, **opts)
wall = time.perf_counter() - t
cpu = _cpu() - c0
files = {
    p.relative_to(out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
    for p in sorted(out.rglob("*"))
    if p.is_file()
}
print(
    json.dumps(
        {
            "workers": workers,
            "variant": variant,
            "wall": wall,
            "cpu": cpu,
            "used": getattr(res, "workers", 1),
            "written": res.written,
            "files": files,
            "install_s": jit,
        }
    )
)
