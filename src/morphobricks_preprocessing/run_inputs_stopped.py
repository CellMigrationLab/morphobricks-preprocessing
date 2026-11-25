"""A stopped run's refusal, for :meth:`.run_inputs.RunInputs.open` (issue #322, contract §9.1).

A run that preprocessing stops publishes no ``run_manifest.json``: its refusal is only in its
report, ``discovery_report.json``, which is for people (contract §9.2). This module is the one
place that reads that report for a brick. :func:`stopped_run_refusal` is called by ``open``
only when the output folder holds no manifest, before anything else of the run is read (so
before any pixel), and returns:

- :class:`.run_inputs_error.RunStoppedError` (``RUN_FOLDER_STOPPED``) for a final report of a
  ``run`` step whose status is ``stopped``: its error issues, in the report's order, with their
  code, message, paths and action exactly as the report records them;
- :class:`.run_inputs_error.RunReportUnreadableError` (``RUN_FOLDER_REPORT_UNREADABLE``) naming
  the file for a report that cannot be read, is not of the shape report version 1 writes, is of
  another report version, or says the run stopped without recording an error;
- None otherwise (no report, the report of ``check``, a provisional report, a ``written``
  report): ``open`` then refuses the folder as before, "holds no run_manifest.json".
"""

from __future__ import annotations

import json
from pathlib import Path

from .discovery_report import REPORT_FILENAME, REPORT_VERSION, STOPPED, WRITTEN
from .manifest import MANIFEST_FILENAME
from .run_inputs_error import RunInputsError, RunReportUnreadableError, RunStoppedError
from .validation import Severity, ValidationIssue, plural

_STEPS = ("run", "check")
_ISSUE_TEXT = ("code", "message", "action")


class _Malformed(ValueError):
    """The report is not of the shape its version writes (the message says where)."""


def stopped_run_refusal(output: Path) -> RunInputsError | None:
    """Return the refusal of the stopped run whose report is in ``output``, if there is one.

    Parameters
    ----------
    output
        An output folder that holds no ``run_manifest.json``.

    Returns
    -------
    RunInputsError or None
        :class:`RunStoppedError` for a stopped run, :class:`RunReportUnreadableError` for a
        report that cannot be read, None when the report does not say a run stopped.

    Raises
    ------
    FileNotFoundError
        When a manifest was published while the report was read: :func:`.run.generation.
        read_stable` then opens the folder again.
    """
    path = output / REPORT_FILENAME
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return _unreadable(path, f"it cannot be read ({exc.strerror or exc})")
    if (output / MANIFEST_FILENAME).exists():
        # A run published while the report was read: that report may be another run's.
        raise FileNotFoundError(output / MANIFEST_FILENAME)
    try:
        return _refusal(output, path, data)
    except _Malformed as exc:
        return _unreadable(path, str(exc))


def _refusal(output: Path, path: Path, data: bytes) -> RunInputsError | None:
    """Return the stopped run's refusal from the report's bytes, None when it did not stop."""
    report = _json(data)
    version = report.get("report_version")
    if version != REPORT_VERSION:
        if not isinstance(version, str):
            raise _Malformed("it has no report version")
        return RunReportUnreadableError(
            f"{RunReportUnreadableError.code}: {path} is report version {version!r}; this "
            f"preprocessing reads report version {REPORT_VERSION}, so it cannot say why the run "
            f"in {output} published nothing. Run preprocessing again into that folder with this "
            "version. No image or mask was read.",
            path,
        )
    step = _one_of(report, "step", _STEPS)
    status = _one_of(report, "status", (STOPPED, WRITTEN))
    provisional = report.get("provisional")
    if not isinstance(provisional, bool):
        raise _Malformed("its 'provisional' is not true or false")
    issues = _issues(report.get("issues"))
    if step != "run" or status != STOPPED or provisional:
        return None
    errors = tuple(issue for issue in issues if issue.is_error)
    if not errors:
        raise _Malformed("it says the run stopped but records no error")
    return RunStoppedError(_stopped_text(output, errors), errors)


def _json(data: bytes) -> dict:
    """Return the report's JSON object; duplicate keys and non-JSON numbers are refused."""
    try:
        report = json.loads(
            data.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_no_constant
        )
    except UnicodeDecodeError as exc:
        raise _Malformed("it is not UTF-8 text") from exc
    except json.JSONDecodeError as exc:
        raise _Malformed(f"it is not valid JSON ({exc.msg}, line {exc.lineno})") from exc
    except _Malformed:
        raise  # a duplicate key or a non-JSON number, refused by the hooks
    except ValueError as exc:  # an integer over Python's digit limit (#455)
        raise _Malformed("it holds a number too long to read") from exc
    if not isinstance(report, dict):
        raise _Malformed("it is not a JSON object")
    return report


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict:
    """Build one JSON object, refusing a key given twice."""
    keys = [key for key, _ in pairs]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        raise _Malformed(f"it gives {', '.join(map(repr, repeated))} twice")
    return dict(pairs)


def _no_constant(name: str) -> object:
    """Refuse ``NaN`` and ``Infinity``, which are not JSON."""
    raise _Malformed(f"it holds {name}, which is not JSON")


def _one_of(report: dict, key: str, allowed: tuple[str, ...]) -> str:
    """Return the report's ``key``, one of ``allowed``."""
    value = report.get(key)
    if value not in allowed:
        raise _Malformed(f"its {key!r} is {value!r}, not one of {', '.join(allowed)}")
    return value


def _issues(items: object) -> tuple[ValidationIssue, ...]:
    """Return the report's issues as written, in order; any other shape is refused."""
    if not isinstance(items, list):
        raise _Malformed("its 'issues' is not a list")
    return tuple(_issue(item, index) for index, item in enumerate(items, 1))


def _issue(item: object, index: int) -> ValidationIssue:
    """Return one issue exactly as written: nothing is sorted, trimmed or filled in."""
    where = f"issue {index}"
    if not isinstance(item, dict) or set(item) != {"severity", "paths", *_ISSUE_TEXT}:
        raise _Malformed(
            f"{where} is not an object with exactly code, severity, message, paths and action"
        )
    if any(not isinstance(item[key], str) for key in _ISSUE_TEXT):
        raise _Malformed(f"{where} has a code, message or action that is not text")
    paths = item["paths"]
    if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
        raise _Malformed(f"{where} has paths that are not a list of text")
    try:
        severity = Severity(item["severity"])
    except ValueError as exc:
        raise _Malformed(f"{where} has the unknown severity {item['severity']!r}") from exc
    return ValidationIssue(severity, item["code"], item["message"], tuple(paths), item["action"])


def _stopped_text(output: Path, errors: tuple[ValidationIssue, ...]) -> str:
    """Say that the run stopped, with its errors' codes and its first error."""
    codes = ", ".join(dict.fromkeys(issue.code for issue in errors))
    return (
        f"{RunStoppedError.code}: the preprocessing run in {output} stopped with "
        f"{plural(len(errors), 'error')} ({codes}) and published no results. First: "
        f"{errors[0].message} Fix what the errors say and run preprocessing again. "
        "No image or mask was read."
    )


def _unreadable(path: Path, why: str) -> RunReportUnreadableError:
    """Return the refusal of a report that cannot be read: nothing is guessed from it."""
    return RunReportUnreadableError(
        f"{RunReportUnreadableError.code}: {path} cannot be read as a preprocessing run report: "
        f"{why}. The folder holds no {MANIFEST_FILENAME}, so there is no run to open and no "
        "known reason why. Run preprocessing again into that folder. No image or mask was read.",
        path,
    )
