"""The axis check in the notebook: one card, and marks under the role dropdowns.

HTML only, from the axis review (:mod:`.axis_check`); the widgets live in
:mod:`.notebook_ui` (the ``notebook`` extra).
"""

from __future__ import annotations

import html
from collections.abc import Iterable

from . import notebook_style as ui
from .axes import AxisMapping, AxisReview, axis_name
from .axis_check import axis_disagreements
from .validation import plural


def axis_check_html(review: AxisReview) -> str:
    """Show the axis check (contract §2) as one card: errors or plain stacks.

    The four codes (``axes_yx_swapped``, ``axes_contradict_header``, ``axes_x_before_y``,
    ``axes_not_named``)
    share one card; each group of files is listed with its header and confirmed axes, the
    errors with the likely cause and the two fixes. The codes stay in small print, for logs.

    Parameters
    ----------
    review
        The axis review of the roles being edited or confirmed.

    Returns
    -------
    str
        The card's HTML; empty when the axis check found nothing.
    """
    errors, notes = review.axis_errors, [i for i in review.axis_check if not i.is_error]
    if not errors and not notes:
        return ""
    lines = []
    for issue in (*errors, *notes):
        lines.append(
            f"{html.escape(issue.message)}{ui.path_list(list(issue.paths), limit=5)}"
            f"<div class='mb-help'>{html.escape(issue.action)} "
            f"<span style='opacity:.6'>({html.escape(issue.code)})</span></div>"
        )
    if errors:
        n = plural(len(review.axis_error_files), "image")
        return ui.status(
            "error", f"Axis check: {n} cannot be read with these roles.", lines, raw=True
        )
    n = plural(len(review.confirmed_files), "image")
    return ui.status(
        "warn", f"Axis check: {n} do not name their axes; your roles are recorded.", lines, raw=True
    )


def header_marks(mapping: AxisMapping, files: Iterable) -> dict[int, str]:
    """Return a warning (HTML) for each role that disagrees with the axes the headers name.

    Shown under the role dropdowns before the roles are confirmed, from the TIFF images
    with the mapping's arrangement (:func:`.axis_check.axis_disagreements`).

    Parameters
    ----------
    mapping
        The roles being edited.
    files
        The image headers.

    Returns
    -------
    dict
        ``{dimension: HTML}``; empty when the roles agree with every header.
    """
    found: dict[int, set[str]] = {}
    letters: dict[int, set[str]] = {}
    for f in files:
        if not f.readable or f.is_ome_zarr or f.axes != mapping.axes:
            continue
        for index, code in axis_disagreements(f, mapping).items():
            found.setdefault(index, set()).add(code)
            letters.setdefault(index, set()).add(f.axes[index])
    marks = {}
    for index, codes in found.items():
        if codes == {"axes_not_named"}:
            marks[index] = (
                "<div class='mb-help' style='color:#92400E'>ⓘ The image metadata does not name "
                "this axis: the role you confirm is recorded as yours. Check it with the "
                "preview.</div>"
            )
            continue
        codes = codes - {"axes_not_named"}
        if codes == {"axes_x_before_y"}:
            marks[index] = (
                "<div class='mb-help' style='color:#92400E'>⚠ These roles put X before Y, and "
                "nothing is read transposed. Choose Y before X, or save the images again with "
                "Y before X.</div>"
            )
            continue
        named = ", ".join(axis_name(letter) for letter in sorted(letters[index]) if letter)
        what = "swaps the Y and X it names" if codes == {"axes_yx_swapped"} else "contradicts it"
        text = (
            f"The image header names this axis {html.escape(named)}; this role {what}. "
            "Choose the header's role, or save named-axis copies with these axes."
        )
        marks[index] = f"<div class='mb-help' style='color:#92400E'>⚠ {text}</div>"
    return marks
