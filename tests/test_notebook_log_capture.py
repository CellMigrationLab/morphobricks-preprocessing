"""The notebook's log capture keeps DEBUG off the root handlers (Colab prints them)."""

import io
import logging

import pytest

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_ui  # noqa: E402


def test_attached_log_capture_keeps_debug_off_the_root_handlers_but_not_warnings():
    root_stream = io.StringIO()
    root_handler = logging.StreamHandler(root_stream)
    root_handler.setLevel(logging.NOTSET)
    root = logging.getLogger()
    root.addHandler(root_handler)
    package = logging.getLogger("morphobricks_preprocessing")
    before = (package.level, package.propagate, list(package.handlers))
    try:
        capture = notebook_ui._LogCapture()
        with capture.attached():
            child = logging.getLogger("morphobricks_preprocessing.projection_marker")
            child.debug("quiet detail")
            child.warning("loud warning")
        assert "quiet detail" not in root_stream.getvalue()
        assert "loud warning" in root_stream.getvalue()
        assert any("quiet detail" in line for line in capture.lines)
        assert (package.level, package.propagate, list(package.handlers)) == before
    finally:
        root.removeHandler(root_handler)
