"""The per-press content memo changes no output (owner's parity request; review bc F1).

Two identical experiments are segmented and registered in one press each, one with the memo
of #578 (a SHA-256 reused while the file's identity is unchanged) and one without it (every
SHA-256 taken again). Every file of the run folder must be the same, after the fields that
differ between any two runs are normalised: the folder paths (two experiments) and the times
the files were written (and the manifest's own SHA-256, which covers them). No file may be
missing from either side. Both experiments have the very same image bytes.
"""

import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest

import test_notebook_preprocessing_cellpose as cell_tests
from cellpose_stub import install
from test_mask_register_progress import answered, segmented_app

pytest.importorskip("ipywidgets")

from morphobricks_preprocessing import content_memo  # noqa: E402

pytestmark = pytest.mark.usefixtures("legacy_grid")

TIMES = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?")
VOLATILE = {"written_at", "created_at", "checked_at", "saved_at", "mtime_ns", "manifest_sha256"}
"""Times, and the SHA-256 of the manifest that holds them (the manifest is compared itself)."""


def _normalised(data, base):
    if isinstance(data, dict):
        return {k: ("<v>" if k in VOLATILE else _normalised(v, base)) for k, v in data.items()}
    if isinstance(data, list):
        return [_normalised(v, base) for v in data]
    if isinstance(data, str):
        return TIMES.sub("<time>", data.replace(base, "<base>"))
    return data


def _digests(folder, base):
    found = {}
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        rel = path.relative_to(folder).as_posix()
        if rel.startswith(".") or "/." in rel:
            continue  # the lock and staging, never part of a generation
        raw = path.read_bytes()
        if path.suffix == ".json":
            text = json.dumps(_normalised(json.loads(raw), base), sort_keys=True)
        else:
            text = TIMES.sub("<time>", raw.decode("utf-8", "replace").replace(base, "<base>"))
            text = re.sub(r"(mtime_ns|written_at|Run manifest SHA-256)[^\n]*", r"\1 <v>", text)
        found[rel] = hashlib.sha256(text.encode()).hexdigest()
    return found


def _registered(tmp_path, monkeypatch, memo, images_of=None):
    tmp_path.mkdir()
    with monkeypatch.context() as patched:
        install(patched, tmp_path)
        if images_of is not None:  # the very same image bytes as the first experiment

            def copied(root, masks, folder, seed):
                Path(masks).mkdir(parents=True, exist_ok=True)
                (root / folder).mkdir(parents=True)
                shutil.copy2(images_of / folder / "a.tif", root / folder / "a.tif")

            patched.setattr(cell_tests, "add_folder", copied)
        if not memo:  # every SHA-256 taken again: what a press did before #578's memo
            patched.setattr(
                content_memo.ContentMemo, "sha256", lambda self, path, compute: compute(path)
            )
        nb, step = segmented_app(tmp_path)
        registration = answered(nb, step)
        assert registration.register(), registration.status.value
    return nb.result.output_folder


def test_register_with_and_without_the_memo_writes_the_same_run(tmp_path, monkeypatch):
    with_memo = _registered(tmp_path / "a", monkeypatch, memo=True)
    without = _registered(tmp_path / "b", monkeypatch, memo=False, images_of=tmp_path / "a/exp")
    first = _digests(with_memo, str((tmp_path / "a").resolve()))
    second = _digests(without, str((tmp_path / "b").resolve()))
    assert len(first) >= 10, sorted(first)
    assert first == second
