"""What a run leaves in its output folder: helpers for the tests (design v0.4 §1, §4)."""

import json
import subprocess
import sys

from morphobricks_preprocessing.discovery_report import REPORT_FILENAME, REPORT_TEXT_FILENAME
from morphobricks_preprocessing.run.staging import STAGING_FOLDER

REPORT_FILES = {REPORT_FILENAME, REPORT_TEXT_FILENAME}


def only_the_report(folder, *, staged=False):
    """True when a stopped run wrote nothing but its report (with status "stopped").

    With ``staged``, the staging folder of the rows it read is there too (design v0.4 §1).
    """
    expected = REPORT_FILES | ({STAGING_FOLDER} if staged else set())
    if not folder.is_dir() or {p.name for p in folder.iterdir()} != expected:
        return False
    return json.loads((folder / REPORT_FILENAME).read_text(encoding="utf-8"))["status"] == "stopped"


def check_outputs(folder):
    """True when ``check`` wrote its report and the design template, and nothing else."""
    names = {p.name for p in folder.iterdir()} if folder.is_dir() else set()
    return names == {*REPORT_FILES, "experiment_design.csv"}


def without_report(files):
    """Leave the report files out of ``{name: content}``: every run rewrites them."""
    return {name: content for name, content in files.items() if name not in REPORT_FILES}


def outputs(folder):
    """Return ``{name: bytes}`` of a folder's files, the report files left out.

    The staging folder is left out too: a run that stops or fails keeps the rows it staged,
    so the next run resumes them (design v0.4 §1); the staging tests check it.
    """
    files = {path.name: path for path in folder.iterdir() if path.name != STAGING_FOLDER}
    return without_report({name: path.read_bytes() for name, path in files.items()})


def finished_pid():
    """Return the process ID of a process that has finished (a stale lock's holder)."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid
