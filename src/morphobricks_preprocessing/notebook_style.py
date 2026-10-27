"""Look and feel of the notebook: cards, status messages, tiles and folder choosers.

These building blocks follow the conventions of the CellMigrationLab notebooks
(NucleiSky, LabChronicle): every step is a page of white cards on a grey
background, each control has a short help line under it, every message uses the
same coloured status card, and headline numbers are shown as tiles. Keeping
them here, instead of copying them into each notebook, keeps every step
consistent. Needs the ``notebook`` extra (``ipywidgets``).
"""

from __future__ import annotations

import html
import os
from collections.abc import Iterable, Sequence

import ipywidgets as widgets

# Light colour scheme forced so the pages stay readable in dark Jupyter themes.
STYLE_CSS = """
<style>
.mb-root { color-scheme: light; background: #F1F5F9; padding: 16px 18px; border-radius: 10px;
  color: #0F172A; font-size: 14px; box-sizing: border-box; overflow: hidden !important;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
.mb-page { overflow: hidden !important; }
.mb-root .widget-box { overflow-x: hidden !important; }
.mb-root * { box-sizing: border-box; }
.mb-root .widget-label, .mb-root label { color: #0F172A; }
.mb-card { background: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 8px; padding: 14px 16px;
  margin: 0 0 12px 0; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06); overflow: hidden !important;
  min-width: 0; }
.mb-title { font-size: 20px; font-weight: 700; margin: 0 0 2px 0; color: #0F172A; }
.mb-subtitle { color: #475569; margin: 0 0 12px 0; line-height: 1.45; }
.mb-header { font-size: 12px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase;
  color: #334155; border-bottom: 1px solid #E2E8F0; padding-bottom: 6px; margin-bottom: 8px; }
.mb-help { color: #64748B; font-size: 12.5px; line-height: 1.45; margin: 2px 0 8px 0;
  overflow-wrap: anywhere; }
.mb-intro { background: #EFF6FF; border-left: 4px solid #2563EB; border-radius: 6px;
  padding: 10px 12px; color: #1E3A8A; line-height: 1.5; margin-bottom: 12px; }
.mb-status { border-left: 4px solid; border-radius: 6px; padding: 9px 12px; line-height: 1.5;
  margin: 8px 0; overflow-wrap: anywhere; }
.mb-status ul { margin: 4px 0 0 0; padding-left: 20px; }
.mb-status details { margin-top: 4px; }
.mb-next { background: #EFF6FF; border: 2px solid #93C5FD; border-radius: 8px;
  padding: 14px 16px; margin: 12px 0; overflow-wrap: anywhere; }
.mb-next-title { color: #1E40AF; font-size: 16px; font-weight: 700; margin-bottom: 6px; }
.mb-next-action { color: #0F172A; font-size: 19px; font-weight: 600; line-height: 1.5; }
.mb-next-detail { color: #475569; font-size: 14px; line-height: 1.5; margin-top: 6px; }
.mb-issue-count { display: inline-block; border-radius: 6px; padding: 5px 10px;
  font-size: 16px; font-weight: 700; margin: 0 8px 8px 0; }
.mb-ok { color: #166534; background: #ECFDF5; border-color: #16A34A; }
.mb-info { color: #1E3A8A; background: #EFF6FF; border-color: #2563EB; }
.mb-warn { color: #92400E; background: #FFFBEB; border-color: #D97706; }
.mb-error { color: #991B1B; background: #FEF2F2; border-color: #DC2626; }
.mb-tiles { display: flex; flex-wrap: wrap; gap: 10px; margin: 6px 0 4px 0; }
.mb-tile { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px; padding: 8px 12px;
  min-width: 120px; flex: 1 1 120px; }
.mb-tile .mb-tile-value { font-size: 20px; font-weight: 700; color: #0F172A; }
.mb-tile .mb-tile-label { font-size: 12px; color: #64748B; }
.mb-table-wrap { overflow-x: auto; max-height: 460px; overflow-y: auto; }
.mb-table { border-collapse: collapse; font-size: 13px; width: 99%; }
.mb-table th { background: #F8FAFC; text-align: left; padding: 6px 10px; position: sticky; top: 0;
  border-bottom: 2px solid #CBD5E1; white-space: nowrap; }
.mb-table td { padding: 5px 10px; border-bottom: 1px solid #E2E8F0; }
.mb-table td.mb-num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.mb-root .mb-table.mb-labels th, .mb-root .mb-table.mb-labels td { text-align: center; }
.mb-root .mb-table td.mb-cell-warn { background: #FFFBEB; color: #92400E;
  box-shadow: inset 3px 0 0 #D97706; }
.mb-pill { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 12px;
  background: #E2E8F0; color: #475569; }
.mb-pill-single { background: #F1F5F9; color: #64748B; font-style: italic; }
.mb-check { list-style: none; padding-left: 0; margin: 4px 0; }
.mb-check li { margin: 2px 0; }
.mb-confirm { border: 2px solid #F59E0B; background: #FFFBEB; border-radius: 8px;
  padding: 10px 12px; margin: 10px 0 4px 0; }
.mb-confirm.mb-confirmed { border-color: #16A34A; background: #F0FDF4; }
.mb-question { color: #0F172A; line-height: 1.5; margin-bottom: 8px; }
.mb-spinner { display: inline-block; width: 12px; height: 12px; margin-right: 8px;
  border: 2px solid #93C5FD; border-top-color: #2563EB; border-radius: 50%;
  animation: mb-spin 0.8s linear infinite; vertical-align: -1px; }
@keyframes mb-spin { to { transform: rotate(360deg); } }
.mb-root .widget-button { border-radius: 6px; }
.mb-root .widget-button:disabled { opacity: 0.45; cursor: not-allowed; }
</style>
"""

_STATUS_CLASSES = {"ok": "mb-ok", "info": "mb-info", "warn": "mb-warn", "error": "mb-error"}
_MAX_LIST = 20


def page(title: str, subtitle: str, children: Sequence[widgets.Widget]) -> widgets.VBox:
    """Wrap a step in the grey page with its title and one-sentence purpose."""
    root = widgets.VBox(
        [
            widgets.HTML(f"<div class='mb-title'>{html.escape(title)}</div>"),
            widgets.HTML(f"<div class='mb-subtitle'>{subtitle}</div>"),
            *children,
        ]
    )
    root.add_class("mb-root")
    outer = widgets.VBox([widgets.HTML(STYLE_CSS), root], layout=widgets.Layout(width="99%"))
    outer.add_class("mb-page")
    return outer


def card(header: str, children: Sequence[widgets.Widget], *, visible: bool = True) -> widgets.VBox:
    """Build a white card with an upper-case header; hidden while ``visible`` is False."""
    box = widgets.VBox(
        [widgets.HTML(f"<div class='mb-header'>{html.escape(header)}</div>"), *children]
    )
    box.add_class("mb-card")
    set_visible(box, visible)
    return box


def intro(text: str) -> widgets.HTML:
    """Blue box at the top of a step saying what it does (``text`` is HTML)."""
    return widgets.HTML(f"<div class='mb-intro'>{text}</div>")


def help_text(text: str) -> widgets.HTML:
    """Grey help line under a control (``text`` is HTML)."""
    return widgets.HTML(f"<div class='mb-help'>{text}</div>")


def set_visible(widget: widgets.Widget, visible: bool) -> None:
    """Show or hide a widget without removing it from the page."""
    widget.layout.display = "" if visible else "none"


def is_visible(widget: widgets.Widget) -> bool:
    """Return True unless the widget was hidden with :func:`set_visible`."""
    return widget.layout.display != "none"


def status(kind: str, title: str, lines: Iterable[str] = (), *, raw: bool = False) -> str:
    """HTML of a status card.

    Parameters
    ----------
    kind
        ``"ok"`` (green), ``"info"`` (blue), ``"warn"`` (amber) or ``"error"`` (red).
    title
        Bold first line.
    lines
        Further lines, shown as a bulleted list.
    raw
        Treat ``lines`` as HTML instead of plain text.
    """
    items = [line if raw else html.escape(line) for line in lines]
    body = f"<ul>{''.join(f'<li>{item}</li>' for item in items)}</ul>" if items else ""
    return f"<div class='mb-status {_STATUS_CLASSES[kind]}'><b>{html.escape(title)}</b>{body}</div>"


def working(text: str) -> str:
    """HTML of a blue status card with a spinner, shown while something runs."""
    return (
        f"<div class='mb-status mb-info'><span class='mb-spinner'></span>{html.escape(text)}</div>"
    )


def next_steps(action: str, detail: str = "") -> str:
    """Build a separate 'What next?' box with a prominent action and optional explanation."""
    explanation = f"<div class='mb-next-detail'>{html.escape(detail)}</div>" if detail else ""
    return (
        "<div class='mb-next'><div class='mb-next-title'>What next?</div>"
        f"<div class='mb-next-action'>{html.escape(action)}</div>{explanation}</div>"
    )


def path_list(paths: Sequence[str], limit: int = _MAX_LIST) -> str:
    """HTML list of paths; long lists are folded behind a "show all" toggle."""
    escaped = [f"<b>{html.escape(p)}</b>" for p in paths]
    if len(escaped) <= limit:
        return "<ul>" + "".join(f"<li>{p}</li>" for p in escaped) + "</ul>"
    shown = "".join(f"<li>{p}</li>" for p in escaped[:limit])
    rest = "".join(f"<li>{p}</li>" for p in escaped[limit:])
    return (
        f"<ul>{shown}</ul><details><summary>Show {len(escaped) - limit:,} more</summary>"
        f"<ul>{rest}</ul></details>"
    )


def tiles(items: Iterable[tuple[str, str, str]]) -> str:
    """HTML of a row of headline numbers: ``(emoji, value, label)`` per tile."""
    cells = "".join(
        f"<div class='mb-tile'><div class='mb-tile-value'>{emoji} {html.escape(value)}</div>"
        f"<div class='mb-tile-label'>{html.escape(label)}</div></div>"
        for emoji, value, label in items
    )
    return f"<div class='mb-tiles'>{cells}</div>"


def table(rows: Sequence[Sequence[str]], numeric_from: int | None = None) -> str:
    """HTML table in a scrollable box; first row is the header.

    Columns from index ``numeric_from`` on are right-aligned.
    """
    if not rows:
        return ""
    header = "".join(f"<th>{html.escape(cell)}</th>" for cell in rows[0])
    body = []
    for row in rows[1:]:
        cells = []
        for i, cell in enumerate(row):
            numeric = numeric_from is not None and i >= numeric_from
            cls = " class='mb-num'" if numeric else ""
            cells.append(f"<td{cls}>{html.escape(cell)}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")
    return (
        f"<div class='mb-table-wrap'><table class='mb-table'><thead><tr>{header}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></div>"
    )


def checklist(items: Iterable[tuple[bool, str]]) -> str:
    """HTML list of ✅ done / ⭕ to do items."""
    lines = "".join(
        f"<li>{'✅' if done else '⭕'} {html.escape(text)}</li>" for done, text in items
    )
    return f"<ul class='mb-check'>{lines}</ul>"


def button(description: str, *, style: str = "", icon: str = "", tooltip: str = "", main=False):
    """Build a button; ``main`` buttons are full width and taller, for a step's main action."""
    layout = widgets.Layout(width="99%", height="42px") if main else widgets.Layout(width="auto")
    return widgets.Button(
        description=description, button_style=style, icon=icon, tooltip=tooltip, layout=layout
    )


def clean_path(text: str) -> str:
    """Tidy a pasted path: strip spaces and quotes, expand ``~`` and environment variables."""
    path = text.strip().strip("\"'“”‘’").strip()
    return os.path.expandvars(os.path.expanduser(path)) if path else ""


def ask_directory(initial: str = "") -> str | None:
    """Open the system's folder dialog; return the chosen folder, ``""`` if cancelled.

    Returns None when no dialog can be shown (no display, or Tk is missing), so
    the caller can ask the user to type the path instead.
    """
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        return None
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        return None
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        start = initial if initial and os.path.isdir(initial) else os.path.expanduser("~")
        chosen = filedialog.askdirectory(parent=root, initialdir=start, mustexist=True)
    finally:
        root.destroy()
    return os.path.normpath(chosen) if chosen else ""


class ConfirmToggle:
    """A question the user answers with a large button, instead of a small checkbox.

    Before confirmation it shows the question, what to check, and a green
    "Yes, ..." button. After the click it shows a green "confirmed" message and
    the button turns into an amber "Undo" button. ``widget`` is what goes on the
    page; ``value`` is True once confirmed.

    Parameters
    ----------
    question
        The question, e.g. "Do the labels in the table match your experiment?".
    explanation
        What to look at before answering (HTML).
    confirm_label
        Text of the confirm button.
    confirmed_text
        Message shown once confirmed.
    undo_label
        Text of the button that takes the confirmation back.
    """

    def __init__(
        self,
        question: str,
        explanation: str,
        confirm_label: str,
        confirmed_text: str,
        undo_label: str,
    ):
        self._labels = (confirm_label, undo_label)
        self._confirmed_text = confirmed_text
        self._value = False
        self._callbacks: list = []
        self.prompt = widgets.HTML(
            f"<div class='mb-question'><b>❓ {html.escape(question)}</b>"
            f"<div class='mb-help' style='margin-bottom:0'>{explanation}</div></div>"
        )
        self.button = button(f" {confirm_label}", style="success", icon="check", main=True)
        self.message = widgets.HTML()
        self.widget = widgets.VBox([self.prompt, self.button, self.message])
        self.widget.add_class("mb-confirm")
        self.button.on_click(lambda _: self.set(not self._value))

    @property
    def value(self) -> bool:
        """True once the user confirmed."""
        return self._value

    @property
    def confirmed_text(self) -> str:
        """Message shown once confirmed."""
        return self._confirmed_text

    @confirmed_text.setter
    def confirmed_text(self, text: str) -> None:
        # The message may name the answer, e.g. the image type chosen.
        self._confirmed_text = text
        if self._value:
            self.message.value = status("ok", text)

    @property
    def disabled(self) -> bool:
        """True while the question cannot be answered yet."""
        return self.button.disabled

    @disabled.setter
    def disabled(self, disabled: bool) -> None:
        self.button.disabled = disabled

    def observe(self, callback) -> None:
        """Call ``callback(value)`` whenever the answer changes."""
        self._callbacks.append(callback)

    def set(self, value: bool, *, notify: bool = True) -> None:
        """Set the answer and update the button and message."""
        changed = value != self._value
        self._value = value
        confirm_label, undo_label = self._labels
        if value:
            self.button.description = f" {undo_label}"
            self.button.icon = "undo"
            self.button.button_style = "warning"
            self.message.value = status("ok", self._confirmed_text)
            self.widget.add_class("mb-confirmed")
        else:
            self.button.description = f" {confirm_label}"
            self.button.icon = "check"
            self.button.button_style = "success"
            self.message.value = ""
            self.widget.remove_class("mb-confirmed")
        if changed and notify:
            for callback in self._callbacks:
                callback(value)


def ask_open_file(initial: str = "", filetypes=(("All files", "*.*"),)) -> str | None:
    """Open the system's file dialog; return the chosen file, ``""`` if cancelled.

    Returns None when no dialog can be shown, like :func:`ask_directory`.
    """
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        return None
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        return None
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        start = initial if initial and os.path.isdir(initial) else os.path.expanduser("~")
        chosen = filedialog.askopenfilename(parent=root, initialdir=start, filetypes=filetypes)
    finally:
        root.destroy()
    return os.path.normpath(chosen) if chosen else ""
