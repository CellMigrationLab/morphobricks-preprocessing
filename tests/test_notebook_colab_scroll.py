"""Cell 3 on Google Colab: the app's output scrolls with the page (owner, v0.5.2).

Colab puts a long output in a frame with its own scroll bar. Cell 3 calls
``google.colab.output.no_vertical_scroll()`` before it shows the app; nothing is called outside
Colab.
"""

import sys

from test_notebook_setup import INSTALL, RUN, cell_source, colab, setup_environment  # noqa: F401


def test_cell_3_on_colab_asks_for_an_output_that_scrolls_with_the_page(
    setup_environment,  # noqa: F811
    monkeypatch,
):
    colab(monkeypatch)
    calls = []
    monkeypatch.setattr(
        sys.modules["google.colab"].output, "no_vertical_scroll", lambda: calls.append(1)
    )
    state = setup_environment
    state.start(INSTALL)
    assert calls == []  # only the cell that shows the app
    state.start(RUN, fresh=False)
    assert calls == [1]
    assert "colab_output.no_vertical_scroll()" in cell_source(RUN)


def test_cell_3_outside_colab_never_imports_the_colab_output(setup_environment):  # noqa: F811
    setup_environment.start(INSTALL)
    setup_environment.start(RUN, fresh=False)
    assert "google.colab" not in sys.modules
    source = cell_source(RUN)
    guard = source.index('if "google.colab" in sys.modules:')
    assert source.index("from google.colab import output") > guard
