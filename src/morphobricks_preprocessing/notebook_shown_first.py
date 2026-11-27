"""A cell's widget displayed before its long checks, then not again by the cell's ``display``.

Cells 4 and 5 of the combined notebook keep their text (``x = step(...)`` then ``display(x)``),
so that they are the same for the release they pin and for this code (#575, v0.9.1). The step
function displays the widget first itself, so that the wait of the checks it then makes is on
screen, and sets :attr:`ShownFirst.skip_next_display`; the cell's own ``display`` then shows
nothing more, once.
"""

from __future__ import annotations

from IPython.display import display


class ShownFirst:
    """Mixin for an ipywidgets box shown first by its step function (before ``widgets.VBox``).

    Attributes
    ----------
    skip_next_display
        True once the step function displayed the widget: the next display of this same widget
        (the cell's own) is consumed and shows nothing; every later display shows it.
    """

    skip_next_display = False

    def _ipython_display_(self, **kwargs: object) -> None:
        """Display the widget, unless its step function just did (then once nothing).

        ipywidgets 7 (Colab) displays a widget by ``Widget._ipython_display_``, ipywidgets 8 by
        ``_repr_mimebundle_`` (7 has none): whichever the installed version has.
        """
        if self.skip_next_display:
            self.skip_next_display = False
            return
        shown_by_widget = getattr(super(), "_ipython_display_", None)
        if shown_by_widget is not None:
            shown_by_widget(**kwargs)
            return
        display(self._repr_mimebundle_(), raw=True)


__all__ = ["ShownFirst"]
