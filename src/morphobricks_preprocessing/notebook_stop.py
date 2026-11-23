"""The one stop of a Cellpose notebook cell: its message on one line, without a traceback.

Cells 2 to 8 of ``MorphoBricks_Cellpose.ipynb`` import :class:`CellStop` from here instead of
each defining its own; only cell 1 (the install, which runs before the package is there) keeps
a copy. This module imports nothing, so the Drive cell can use it at no cost.
"""

from __future__ import annotations


class CellStop(RuntimeError):
    """Stop a notebook cell with its message on one line, without a traceback.

    IPython (Jupyter, Colab, LabConstrictor) shows an exception that has
    ``_render_traceback_`` as the lines it returns: here only the message.
    """

    def _render_traceback_(self) -> list:
        """Return the message as the one line IPython shows."""
        return [str(self)]
