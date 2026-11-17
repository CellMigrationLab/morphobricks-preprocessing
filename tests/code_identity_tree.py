"""Small installed distributions for the code-identity tests (Spec ADR 0005 BM15)."""

from __future__ import annotations

import base64
import hashlib
import json
from importlib import metadata
from types import SimpleNamespace

from morphobricks_preprocessing.code_identity import code_identity

COMMIT = "a" * 40
FILES = {"fakepkg/__init__.py": b"X = 1\n", "fakepkg/mod.py": b"Y = 2\n"}


def _hash(data):
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def install(site, files=FILES, *, editable=False, unhashed=(), commit=COMMIT):
    """Install ``files`` as the distribution ``fakepkg`` 1.0 under ``site``; return it."""
    info = site / "fakepkg-1.0.dist-info"
    info.mkdir(parents=True)
    for name, data in files.items():
        (site / name).parent.mkdir(parents=True, exist_ok=True)
        (site / name).write_bytes(data)
    (info / "METADATA").write_bytes(b"Metadata-Version: 2.1\nName: fakepkg\nVersion: 1.0\n")
    url = {"url": "file:///src/fakepkg"}
    url |= {"dir_info": {"editable": True}} if editable else {"vcs_info": {"commit_id": commit}}
    (info / "direct_url.json").write_text(json.dumps(url), encoding="utf-8")
    rows = [
        f"{name},{'' if name in unhashed else _hash(data)},{len(data)}"
        for name, data in files.items()
    ]
    for name in ("METADATA", "direct_url.json"):
        rows.append(f"fakepkg-1.0.dist-info/{name},{_hash((info / name).read_bytes())},1")
    rows.append("fakepkg-1.0.dist-info/RECORD,,")
    (info / "RECORD").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return metadata.PathDistribution(info)


def modules_of(site, names=("fakepkg", "fakepkg.mod")):
    """Imported modules of the installation, as ``sys.modules`` would hold them."""
    paths = {"fakepkg": "fakepkg/__init__.py", "fakepkg.mod": "fakepkg/mod.py"}
    return {name: SimpleNamespace(__file__=str(site / paths[name])) for name in names}


def broken_identity(site, kind):
    """Return the identity of an installation broken as ``kind`` names (never verified)."""
    dist = install(
        site, editable=kind == "editable", unhashed=("fakepkg/mod.py",) if kind == "no_hash" else ()
    )
    modules = modules_of(site)
    if kind == "record_missing":
        (site / "fakepkg-1.0.dist-info" / "RECORD").unlink()
    elif kind == "hash_differs":
        (site / "fakepkg" / "mod.py").write_bytes(b"Y = 3\n")
    elif kind == "not_in_record":
        (site / "fakepkg" / "added.py").write_bytes(b"Z = 1\n")
    elif kind == "source_tree":
        modules["fakepkg"] = SimpleNamespace(__file__=str(site.parent / "tree" / "__init__.py"))
    return code_identity(dist, modules)
