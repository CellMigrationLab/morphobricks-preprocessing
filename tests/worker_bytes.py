"""Byte identity of runs with any number of workers: a fixed clock and every file's bytes.

``written_at`` is the only field of any output excluded from byte identity (owner decision of
16 November 2026, contract §9). The tests fix the run's clock (:func:`fixed_clock`) so the
manifest's raw bytes are compared as they are, never parsed with the field deleted.
"""

from __future__ import annotations

import shutil
from datetime import datetime

import pytest

from described_tree import run_described
from morphobricks_preprocessing.run import RunMode
from morphobricks_preprocessing.run import outputs as outputs_module
from morphobricks_preprocessing.run import workers as workers_module

WRITTEN_AT = "2026-11-16T12:00:00+00:00"
"""The time every run of these tests writes: ``written_at`` under a fixed clock."""


class FixedClock(datetime):
    """``datetime`` whose ``now`` is a fixed time (the run's only clock read for an output)."""

    at = datetime.fromisoformat(WRITTEN_AT)

    @classmethod
    def now(cls, tz=None):
        return cls.at.astimezone(tz)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    """Every run writes ``written_at`` = :data:`WRITTEN_AT`; set ``fixed_clock.at`` to move it."""
    monkeypatch.setattr(outputs_module, "datetime", FixedClock)
    monkeypatch.setattr(FixedClock, "at", datetime.fromisoformat(WRITTEN_AT))
    return FixedClock


def every_file(out):
    """``{relative path: raw bytes}`` of every file in the output folder, at any depth."""
    return {
        path.relative_to(out).as_posix(): path.read_bytes()
        for path in sorted(out.rglob("*"))
        if path.is_file()
    }


def fresh_run(root, out, workers, mapping, *, keep=("morphobricks.toml",), **kwargs):
    """Run into ``out`` emptied first (``keep`` stays); return the result and every file's bytes.

    One folder for every run, so the reports, which name it, are compared as well.
    """
    if out.exists():
        for path in out.iterdir():
            if path.name not in keep:
                shutil.rmtree(path) if path.is_dir() else path.unlink()
    out.mkdir(exist_ok=True)
    mode = RunMode("restart", verify_content=True, workers=workers)
    result = run_described(root, mapping, output_folder=out, mode=mode, **kwargs)
    assert result.written, [(issue.code, issue.message) for issue in result.issues]
    return result, every_file(out)


def expected_workers(workers, images):
    """``RunResult.workers`` of a run reading ``images`` images (the pool's size)."""
    if workers == "auto":
        return min(images, workers_module.available_cpus())
    return min(workers, images)
