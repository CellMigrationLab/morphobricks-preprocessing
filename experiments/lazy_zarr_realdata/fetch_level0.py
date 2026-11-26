"""Download the group metadata and the level-0 array of a public OME-Zarr store, key by key.

Keys are derived from the array's own metadata (zarr v2 .zarray or v3 zarr.json), so no bucket
listing is needed. A 404 for a chunk key is recorded as an absent chunk (zarr reads it as the
fill value). Writes a manifest (key, bytes, sha256) next to the store. Hard byte cap.
Usage: python -I fetch_level0.py BASE_URL DEST_DIR LEVEL0_PATH [CAP_BYTES]
"""

import hashlib
import itertools
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

base, dest, level0 = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3].strip("/")
cap = int(sys.argv[4]) if len(sys.argv) > 4 else 1_500_000_000
total = 0


def get(key):
    url = f"{base}/{key}"
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return None
            err = e
        except Exception as e:  # noqa: BLE001
            err = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url}: {err}")


def save(key, data):
    global total
    path = os.path.join(dest, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    total += len(data)
    if total > cap:
        raise SystemExit(f"cap of {cap} bytes exceeded")
    return [key, len(data), hashlib.sha256(data).hexdigest()]


manifest, absent = [], []
meta_keys = [".zgroup", ".zattrs", ".zmetadata", "zarr.json"]
parts = level0.split("/")
for i in range(len(parts)):  # intermediate groups
    pre = "/".join(parts[:i])
    for k in (".zgroup", ".zattrs", "zarr.json"):
        meta_keys.append(f"{pre}/{k}" if pre else k)
for k in (".zarray", ".zattrs", "zarr.json"):
    meta_keys.append(f"{level0}/{k}")
for k in dict.fromkeys(meta_keys):
    d = get(k)
    if d is not None:
        manifest.append(save(k, d))

v3 = os.path.exists(os.path.join(dest, level0, "zarr.json"))
if v3:
    m = json.load(open(os.path.join(dest, level0, "zarr.json")))
    shape = m["shape"]
    grid = m["chunk_grid"]["configuration"]["chunk_shape"]
    enc = m["chunk_key_encoding"]
    sep = enc.get("configuration", {}).get("separator", "/" if enc["name"] == "default" else ".")
    prefix = "c" + sep if enc["name"] == "default" else ""
else:
    m = json.load(open(os.path.join(dest, level0, ".zarray")))
    shape, grid = m["shape"], m["chunks"]
    sep, prefix = m.get("dimension_separator", "."), ""
counts = [math.ceil(s / c) for s, c in zip(shape, grid)]
keys = [
    f"{level0}/{prefix}{sep.join(map(str, idx))}" for idx in itertools.product(*map(range, counts))
]
print(f"{len(keys)} chunk keys, grid {grid}, shape {shape}", flush=True)


def one(key):
    d = get(key)
    return key, d


with ThreadPoolExecutor(8) as pool:
    for n, (key, d) in enumerate(pool.map(one, keys), 1):
        if d is None:
            absent.append(key)
        else:
            manifest.append(save(key, d))
        if n % 200 == 0:
            print(n, total, flush=True)
with open(dest.rstrip("/") + ".manifest.json", "w") as f:
    json.dump(
        {"base": base, "level0": level0, "bytes": total, "files": manifest, "absent": absent}, f
    )
print("done", len(manifest), "files", total, "bytes", len(absent), "absent")
