"""What a run leaves in its output folder: helpers for the tests (design v0.4 §1, §4)."""

import json

from morphobricks_preprocessing.discovery_report import REPORT_FILENAME, REPORT_TEXT_FILENAME

REPORT_FILES = {REPORT_FILENAME, REPORT_TEXT_FILENAME}


def only_the_report(folder):
    """True when a stopped run wrote nothing but its report (with status "stopped")."""
    if not folder.is_dir() or {p.name for p in folder.iterdir()} != REPORT_FILES:
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
    """Return ``{name: bytes}`` of a folder's files, the report files left out."""
    return without_report({path.name: path.read_bytes() for path in folder.iterdir()})
