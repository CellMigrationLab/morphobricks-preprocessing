"""bench.py BENCH WORKDIR REPEATS VARIANTS... -- WORKERS... : fresh process per run, rotated order."""

import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
args = sys.argv[1:]
bench, work, repeats = Path(args[0]), Path(args[1]), int(args[2])
split = args.index("--")
variants, workers = args[3:split], args[split + 1 :]
limit = os.environ.get("MB_LIMIT_MIB", "64")


def one(v, w):
    out = work / "out"  # one folder for every run: the reports name it (tests/worker_bytes.py)
    p = subprocess.run(
        [sys.executable, str(HERE / "runonce.py"), str(bench), str(out), w, v, limit],
        capture_output=True,
        text=True,
    )
    if p.returncode:
        raise SystemExit(p.stderr[-3000:])
    return json.loads(p.stdout.strip().splitlines()[-1])


one("current", "1")  # warm-up: disk cache
for v in variants:
    if v != "current":
        one(v, "1")  # warm-up: Numba cache on disk
runs = {}
for r in range(repeats):
    order = variants[r % len(variants) :] + variants[: r % len(variants)]
    for w in workers:
        for v in order:
            res = one(v, w)
            runs.setdefault((v, w), []).append(res)
            print(
                json.dumps({k: res[k] for k in ("variant", "workers", "wall", "cpu", "used")}),
                flush=True,
            )
ref = runs[("current", workers[0])][0]["files"]
rows = []
for (v, w), found in runs.items():
    walls = [x["wall"] for x in found]
    cpus = [x["cpu"] for x in found]
    rows.append(
        {
            "variant": v,
            "workers": w,
            "used": found[0]["used"],
            "median_wall": statistics.median(walls),
            "min_wall": min(walls),
            "max_wall": max(walls),
            "median_cpu": statistics.median(cpus),
            "identical": all(x["files"] == ref for x in found),
            "nfiles": len(ref),
            "files": found[0]["files"],
        }
    )
print("SUMMARY " + json.dumps(rows))
