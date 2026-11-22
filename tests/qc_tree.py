"""A small experiment for the image QC tests: three images of two time points and channels."""

import hashlib

import numpy as np

from described_tree import run_described
from morphobricks_preprocessing import prefill_mapping
from tiff_tree import write_data

CTYX = prefill_mapping("TCYX")
IMAGES = ["ctrl/r1/a.tif", "ctrl/r1/b.tif", "drug/r1/a.tif"]


def stack(seed):
    """Two time points of two channels: smooth structure plus noise, never constant."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[:40, :48]
    base = (np.sin(y / (3 + seed)) * np.cos(x / 5) * 60 + 120).astype(np.uint8)
    data = np.empty((2, 2, 40, 48), dtype=np.uint8)
    for t in range(2):
        for c in range(2):
            data[t, c] = base // (c + 1) + rng.integers(0, 30, (40, 48), dtype=np.uint8)
    return data


def make_experiment(tmp_path):
    """Write the experiment under ``tmp_path / "exp"`` and return its folder."""
    root = tmp_path / "exp"
    for seed, rel in enumerate(IMAGES):
        write_data(root / rel, stack(seed), "TCYX")
    return root


def run(root, out, **kwargs):
    result = run_described(root, CTYX, output_folder=out, **kwargs)
    assert result.written, [issue.message for issue in result.issues if issue.is_error]
    return result


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def interrupt_at_second_file(monkeypatch):
    """Make the run raise ``RuntimeError("stopped")`` when it reads its second image.

    Returns the real reader, to put back (``run.resume._read_file``) before the next run.
    """
    from morphobricks_preprocessing import run as run_module

    read, calls = run_module.resume._read_file, []

    def stop_at_second(*args):
        calls.append(args[1])
        if len(calls) == 2:
            raise RuntimeError("stopped")
        return read(*args)

    monkeypatch.setattr(run_module.resume, "_read_file", stop_at_second)
    return read
