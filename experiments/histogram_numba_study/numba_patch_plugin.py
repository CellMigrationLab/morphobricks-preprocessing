"""pytest -p numba_patch_plugin: install the prototype before any test (MB_PATCH_MODE)."""

import numba_patch


def pytest_configure(config):
    numba_patch.install()
    import morphobricks_normalization.histogram as H

    patched = {f.__module__ for f in (H._float_bins, H._integer_bin_starts)}
    assert "numba_patch" in patched, "patch not installed"


def pytest_report_header(config):
    return f"numba_patch installed, mode={numba_patch.MODE}"
