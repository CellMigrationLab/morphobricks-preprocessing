"""What a shipped notebook pins, read from the notebook file, and what changed since its pin.

The notebooks install this package at their ``RELEASE_REF`` commit, not this checkout. The
Preprocessing notebook keeps the call of the code at that commit (``test_notebook_file.py``,
``RELEASE_CALLS``); the Cellpose notebook must run exactly the code at ``RELEASE_REF`` that it
uses (issue #421). Both read their cells and their pins here (:func:`code_sources`,
:func:`install_setting`).

What the Cellpose notebook uses is derived from its cells, never listed by hand
(:func:`notebook_modules`): the package modules its cells import, then every package module
those import, at any depth and inside functions too, at ``RELEASE_REF`` and at ``HEAD``
(:func:`module_closure`). :func:`pin_differences` names every file of that code, every
non-Python file of the package, the build's own inputs and the install-relevant part of
``pyproject.toml`` that differ between the two commits. A use the derivation cannot follow
is refused with :class:`PinScopeError`, never assumed unchanged: in cells and package code,
a loader reached by an ``as`` alias or ``getattr``; in a cell, code loaded by a name or from
text or a file, a command other than ``python -m pip`` (or one reached by an alias, a ``from``
import or IPython), an install other than at ``@{RELEASE_REF}`` (a forced reinstall included,
and the bare distribution name a cell function passes on to ``pip``), an IPython ``!``/``%``
line; in package code, a module loaded by a computed name or a file or text run; in the
build, a second wheel package, an unknown backend or ``[tool.hatch]`` setting, or a
``hatch.toml``. Package code that names a package module in a string (``"pkg.mod"`` or
``"pkg.mod:attr"``) runs it as an import.

The combined notebook (``MorphoBricks_Preprocessing_Cellpose.ipynb``, #441) shares the
Preprocessing notebook's install and app cells, which may use a local checkout they verify
with Git: it is checked with ``checkout=True`` (:func:`notebook_modules`), which follows that
cell's read-only ``git`` questions, its editable install and its package modules named in
strings instead of refusing them. The Cellpose notebook keeps the strict default.

The check is a drift detector, not a security boundary: it catches a legitimate edit that
changes what the notebook runs without a new pin. Deliberate obfuscation is out of scope
and not seen: ``getattr(__builtins__, ...)``, ``ctypes``, or a module or package name
assembled from strings at runtime.

Known gaps: package code that assembles a module name at runtime and passes it to
something other than a loader (``[sys.executable, "-m", f"{PACKAGE}.{name}"]``) is not seen.
"""

from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path

import pytest

from notebook_pin_build import (  # noqa: F401 (re-exported)
    HATCH_KEYS,
    PACKAGE,
    SOURCE_ROOT,
    PinScopeError,
    _blob_texts,
    _install_table_at,
    install_table,
    tree_blobs,
)
from notebook_pin_code import (  # noqa: F401 (re-exported)
    DOTTED_NAME,
    FILE_LOADERS,
    LOADER_MODULES,
    NAME_LOADERS,
    TEXT_LOADERS,
    _call_name,
    _hidden_loader,
    _imported,
    _loader,
    module_closure,
    used_files,
)

DISTRIBUTION = "morphobricks-preprocessing"
BUILD_FILES = (".gitignore",)
"""The files the build reads besides the package and ``pyproject.toml``: hatchling leaves out
what ``.gitignore`` names, so it can change the installed files. A ``hatch.toml`` is refused
(:data:`HATCH_KEYS`)."""
SHELLS = frozenset({"subprocess", "os"})
"""The modules whose :data:`COMMANDS` start a program. A cell uses them only by their own name
(no ``as``, no other binding) and imports none of their commands by ``from ... import``, so
every command call is seen."""
COMMANDS = frozenset(
    {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}
    | {"system", "popen", "startfile", "posix_spawn", "posix_spawnp"}
    | {f"exec{s}" for s in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")}
    | {f"spawn{s}" for s in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")}
)
"""``subprocess`` and ``os`` calls that start a program."""
PIP_WORDS = frozenset({"install", "--disable-pip-version-check", "--force-reinstall", "--no-deps"})
"""The literal words a cell's ``pip`` command may get after ``-m pip``, in the call or through
variables (:func:`_pip_words`). The requirements are f-strings, whose text
:func:`_refuse_unpinned_install` checks where it is written.
``--force-reinstall --no-deps`` replaces an installed preprocessing of the same version from
another commit (pip keeps it otherwise, #371); it installs the same pinned requirement."""
CHECKOUT_PIP_WORDS = frozenset({"-e"})
"""Further ``pip`` words with ``checkout=True`` only (:func:`notebook_modules`): the editable
install of a local checkout by the Preprocessing install cell, which the combined notebook
shares. That cell installs a checkout only when Git says it is the clean ``RELEASE_REF``
commit (``_require_release_checkout``, tested in ``test_notebook_setup.py``)."""
GIT_WORDS = frozenset({"-C", "rev-parse", "--show-toplevel", "HEAD", "status", "--porcelain"})
"""The literal words a ``git`` command may get with ``checkout=True``: the read-only questions
that cell asks a checkout (its top folder, its commit, whether its tree has changes)."""
CHECKOUT_MARKER = f"{SOURCE_ROOT}/{PACKAGE}/__init__.py"
"""The file by which that cell recognises a nearby checkout (``checkout=True`` only); the
checkout is then used only as described under :data:`CHECKOUT_PIP_WORDS`."""
NAMED_ONCE = frozenset({"REPOSITORY", "notebook_name"})
"""The settings whose literal value may name the package: the repository, and LabConstrictor's
``notebook_name`` (``MorphoBricks_Preprocessing...``). Neither may reach ``pip`` as a word."""
INSTALL_SPEC = re.compile(r"morphobricks[-_.]preprocessing|git\+", re.I)
"""Text that can name this package (or any Git source) for ``pip``."""
METADATA_CALLS = frozenset({"version", "distribution"})
"""``importlib.metadata`` calls that take the bare distribution name and install nothing."""
HIDDEN_RUNNERS = frozenset({"get_ipython"})
"""Names that run commands or code the cell's syntax does not show (IPython's ``system``)."""


def history_missing(reason: str) -> None:
    """Skip a test that needs the Git history, or fail it in CI (``CI`` set), whose checkout
    holds the full history so that the pin check runs there (issue #421)."""
    if os.environ.get("CI"):
        pytest.fail(f"{reason}: CI must check out the full history (fetch-depth: 0)")
    pytest.skip(reason)


def code_sources(notebook: Path) -> list[str]:
    """Return the source of each code cell of ``notebook``, in order."""
    cells = json.loads(notebook.read_text(encoding="utf-8"))["cells"]
    return ["".join(cell["source"]) for cell in cells if cell["cell_type"] == "code"]


def install_setting(source: str, name: str) -> str:
    """Return the string ``name`` is set to in an install cell's ``source`` (one line)."""
    match = re.search(rf'^{name} = "([^"]*)"$', source, re.M)
    assert match is not None, f"the install cell sets no {name}"
    return match.group(1)


def _starts_pip(node: ast.Call) -> bool:
    """Return whether a command call runs ``sys.executable -m pip``."""
    if not node.args or not isinstance(node.args[0], ast.List):
        return False
    head = [ast.unparse(item) for item in node.args[0].elts[:3]]
    return head == ["sys.executable", "'-m'", "'pip'"]


def _starts_git(node: ast.Call) -> bool:
    """Return whether a command call runs ``git`` (its list starts with the literal word)."""
    if not node.args or not isinstance(node.args[0], ast.List) or not node.args[0].elts:
        return False
    first = node.args[0].elts[0]
    return isinstance(first, ast.Constant) and first.value == "git"


def _literal_module(node: ast.AST) -> str | None:
    """Return the package module a string or a loader call names literally, or None.

    A string that is a dotted name of the package, or ``import_module``/``__import__`` given
    one: with ``checkout=True`` both are followed as imports, as in package code.
    """
    if isinstance(node, ast.Call) and _loader(node) in NAME_LOADERS and node.args:
        node = node.args[0]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if DOTTED_NAME.fullmatch(node.value):
            return node.value.replace(":", ".")
    return None


def _refuse_hidden_commands(tree: ast.AST, where: str) -> None:
    """Raise ``PinScopeError`` for a cell command the :data:`COMMANDS` check could miss.

    A command module bound to another name (``import subprocess as sp``, ``runner =
    subprocess``, ``getattr(os, ...)``), a command imported by name (``from os import
    system``) or kept to call later (``start = subprocess.run``) and IPython's runners are
    refused. Other names from these modules (``from os import environ``, ``from subprocess
    import CalledProcessError``) start nothing and are allowed.
    """
    owners = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad = [a.name for a in node.names if a.name in SHELLS and a.asname]
        elif isinstance(node, ast.ImportFrom) and node.module in SHELLS:
            bad = [a.name for a in node.names if a.name in COMMANDS | {"*"}]
        elif isinstance(node, ast.Name):
            shell = node.id in SHELLS and id(node) not in owners
            bad = [node.id] if shell or node.id in HIDDEN_RUNNERS else []
        elif isinstance(node, ast.Attribute) and node.attr in COMMANDS:
            shell = isinstance(node.value, ast.Name) and node.value.id in SHELLS
            bad = [ast.unparse(node)] if shell and id(node) not in called else []
        else:
            bad = []
        if bad:
            raise PinScopeError(
                f"{where} reaches a command by a name the check cannot follow: {bad}"
            )


def _refuse_unseen_use(tree: ast.AST, where: str, checkout: bool = False) -> None:
    """Raise ``PinScopeError`` for a notebook use of code no import statement shows.

    With ``checkout``, a literal package module name (:func:`_literal_module`) is followed,
    not refused, and a ``git`` command is allowed (its words: :data:`GIT_WORDS`).
    """
    _refuse_hidden_commands(tree, where)
    for node in ast.walk(tree):
        if hidden := _hidden_loader(node):
            raise PinScopeError(f"{where} reaches a loader the check cannot follow: {hidden}")
        if checkout and (_literal_module(node) or getattr(node, "value", None) == CHECKOUT_MARKER):
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if PACKAGE in node.value:
                raise PinScopeError(f"{where} names {PACKAGE} outside an import: {node.value!r}")
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if _loader(node):
            raise PinScopeError(f"{where} loads code by {name}(): the pin check cannot follow it")
        owner = node.func.value if isinstance(node.func, ast.Attribute) else None
        is_command = isinstance(owner, ast.Name) and owner.id in ("subprocess", "os")
        runs = _starts_pip(node) or (checkout and _starts_git(node))
        if is_command and name in COMMANDS and not runs:
            raise PinScopeError(f"{where} runs a command other than pip: {ast.unparse(node)}")


def _package_imports(tree: ast.AST, where: str, checkout: bool = False) -> set[str]:
    """Return the package modules (and imported names) a cell's import statements name.

    With ``checkout``, also every package module the cell names literally
    (:func:`_literal_module`).
    """
    names = set()
    for node in ast.walk(tree):
        if checkout and (module := _literal_module(node)):
            names.add(module)
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names if a.name.split(".")[0] == PACKAGE)
        elif isinstance(node, ast.ImportFrom) and node.level:
            raise PinScopeError(f"{where} has a relative import")
        elif isinstance(node, ast.ImportFrom) and node.module.split(".")[0] == PACKAGE:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


def _pinned(node: ast.JoinedStr) -> bool:
    """Return whether an f-string ends with ``@{RELEASE_REF}``, the notebook's pinned install."""
    *_, before, last = [None, None, *node.values]
    return (
        isinstance(last, ast.FormattedValue)
        and isinstance(last.value, ast.Name)
        and last.value.id == "RELEASE_REF"
        and isinstance(before, ast.Constant)
        and before.value.endswith("@")
    )


def _f_string_text(node: ast.JoinedStr) -> str:
    """Return an f-string's literal text, with ``{}`` for each replacement field."""
    return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)


def _values_of(name: str, nodes: list[ast.AST]) -> list[ast.expr]:
    """Return every value the cells give the variable ``name``.

    That is what is assigned to it, appended, inserted or extended onto it
    (``wanted.append(...)``) and, for a parameter of a function the cells define, every
    argument a call of that function gives it (every extra positional argument, for its
    ``*args``). Names are not told apart by scope.
    """
    values: list[ast.expr] = []
    for node in nodes:
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign) and node.value:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                values.append(node.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            grows = node.func.attr in ("append", "extend", "insert")
            if grows and isinstance(node.func.value, ast.Name) and node.func.value.id == name:
                values.extend(node.args[1:] if node.func.attr == "insert" else node.args)
        elif isinstance(node, ast.FunctionDef):
            params = [a.arg for a in node.args.args]
            calls = [n for n in nodes if isinstance(n, ast.Call) and _call_name(n) == node.name]
            if node.args.vararg is not None and node.args.vararg.arg == name:
                values.extend(a for c in calls for a in c.args[len(params) :])
            if name in params:
                at = params.index(name)
                values.extend(c.args[at] for c in calls if len(c.args) > at)
                values.extend(k.value for c in calls for k in c.keywords if k.arg == name)
    return values


def _pip_words(command: ast.Call, nodes: list[ast.AST], start: int = 3) -> list[str]:
    """Return every literal word a cell's ``pip`` command gets after ``-m pip``.

    ``start``: the words of the command list skipped (1 for the words after ``git``).

    Words written in the call and words that reach it through variables are both returned:
    a name (or ``*name``) is followed to its :func:`_values_of`, through lists and further
    names, so a word added to a list elsewhere, or the bare distribution name a cell function
    is given and passes on in a copy or a list, is seen like one written in the call. An
    f-string is not opened: :func:`_refuse_unpinned_install` checks its text where it is
    written.
    """
    leaves = _command_values(command, nodes, start)
    return [n.value for n in leaves if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _command_values(command: ast.Call, nodes: list[ast.AST], start: int = 3) -> list[ast.expr]:
    """Return the values a command list gets after ``start`` words, followed as in
    :func:`_pip_words` (an f-string or a call is a value, not opened)."""
    leaves, pending, seen = [], list(command.args[0].elts[start:]), set()
    while pending:
        item = pending.pop()
        if isinstance(item, ast.Starred):
            pending.append(item.value)
        elif isinstance(item, ast.List | ast.Tuple):
            pending.extend(item.elts)
        elif isinstance(item, ast.IfExp):
            pending.extend([item.body, item.orelse])
        elif isinstance(item, ast.Name):
            if item.id not in seen:
                seen.add(item.id)
                pending.extend(_values_of(item.id, nodes))
        else:
            leaves.append(item)
    return leaves


def _checkout_allowed(nodes: list[ast.AST]) -> set[int]:
    """Return the strings ``checkout=True`` allows; refuse a ``git`` word not in :data:`GIT_WORDS`.

    Allowed: a literal package module name, :data:`CHECKOUT_MARKER`, and an f-string that
    reaches no ``pip`` command (a message).
    """
    commands = [n for n in nodes if isinstance(n, ast.Call) and _starts_pip(n)]
    to_pip = {id(v) for c in commands for v in _command_values(c, nodes)}
    allowed = set()
    for node in nodes:
        if isinstance(node, ast.Call) and _starts_git(node):
            unknown = sorted(set(_pip_words(node, nodes, start=1)) - GIT_WORDS)
            if unknown:
                raise PinScopeError(f"a git command the check does not know: {unknown}")
        named = isinstance(node, ast.Constant) and (
            _literal_module(node) or node.value == CHECKOUT_MARKER
        )
        if named or (isinstance(node, ast.JoinedStr) and id(node) not in to_pip):
            allowed.add(id(node))
    return allowed


def _refuse_unpinned_install(trees: list[ast.AST], checkout: bool = False) -> None:
    """Raise ``PinScopeError`` unless every install of the package in the cells is pinned.

    ``RELEASE_REF`` and ``REPOSITORY`` are each set once (and named in no string); a ``pip``
    command is given no literal word but :data:`PIP_WORDS`, written in it or reaching it
    through variables (:func:`_pip_words`); and every string that can name the package for
    ``pip`` is the f-string ending ``@{RELEASE_REF}``, a :data:`NAMED_ONCE` value, or the bare
    distribution name passed to a metadata call or a function the cells define. Such a
    function may read the installed package by that name, never install it: the name
    reaching ``pip`` is a word :data:`PIP_WORDS` does not hold. With ``checkout``,
    :data:`CHECKOUT_PIP_WORDS` are allowed too, a ``git`` command gets no literal word but
    :data:`GIT_WORDS`, and a literal package module name is allowed (it names no install), as
    is a text that reaches no ``pip`` command (a message, such as the requirement it lists).
    """
    nodes = [node for tree in trees for node in ast.walk(tree)]
    for setting in ("RELEASE_REF", "REPOSITORY"):
        stores = [
            n
            for n in nodes
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id == setting
        ]
        named = [n for n in nodes if isinstance(n, ast.Constant) and n.value == setting]
        if len(stores) > 1 or named:
            raise PinScopeError(f"{setting} is set more than once: the installed commit is unclear")
    defined = {n.name for n in nodes if isinstance(n, ast.FunctionDef)}
    allowed = _checkout_allowed(nodes) if checkout else set()
    pip_words = PIP_WORDS | (CHECKOUT_PIP_WORDS if checkout else frozenset())
    for node in nodes:
        if isinstance(node, ast.JoinedStr):
            allowed |= {id(v) for v in node.values} | ({id(node)} if _pinned(node) else set())
        elif isinstance(node, ast.Call) and _call_name(node) in METADATA_CALLS | defined:
            allowed |= {id(a) for a in node.args if getattr(a, "value", None) == DISTRIBUTION}
        elif isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") in NAMED_ONCE:
            allowed.add(id(node.value))
        if isinstance(node, ast.Call) and _starts_pip(node):
            unknown = sorted(set(_pip_words(node, nodes)) - pip_words)
            if unknown:
                raise PinScopeError(f"a pip command the check does not know: {unknown}")
    for node in nodes:
        text = _f_string_text(node) if isinstance(node, ast.JoinedStr) else None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        if text is not None and INSTALL_SPEC.search(text) and id(node) not in allowed:
            raise PinScopeError(f"an install source other than @{{RELEASE_REF}}: {text!r}")


def notebook_modules(sources: list[str], *, checkout: bool = False) -> set[str]:
    """Return the package modules and names the notebook cells ``sources`` import.

    ``checkout``: the cells include the Preprocessing install cell, which may use a local
    checkout it verifies with Git (the combined notebook, #441): its read-only ``git``
    questions (:data:`GIT_WORDS`), its editable install (:data:`CHECKOUT_PIP_WORDS`) and its
    package modules named in strings (:func:`_literal_module`) are followed, not refused.

    Raises ``PinScopeError`` when a cell reaches code another way (see the module docstring)
    or when no cell imports the package at all.
    """
    names, trees = set(), []
    for number, source in enumerate(sources, start=1):
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            magic = [line for line in source.splitlines() if line.lstrip()[:1] in ("!", "%")]
            what = f"an IPython line {magic[0].strip()!r}" if magic else "code that is not Python"
            raise PinScopeError(f"cell {number} has {what}: the check cannot follow it") from exc
        _refuse_unseen_use(tree, f"cell {number}", checkout)
        names |= _package_imports(tree, f"cell {number}", checkout)
        trees.append(tree)
    _refuse_unpinned_install(trees, checkout)
    if not names:
        raise PinScopeError(f"no cell imports {PACKAGE}: nothing to compare with the pin")
    return names


def pin_differences(
    repo: Path, ref: str, head: str, sources: list[str], *, checkout: bool = False
) -> list[str]:
    """Return what the notebook cells ``sources`` use that differs between ``ref`` and ``head``.

    Empty when the notebook, installing ``ref``, runs exactly the code ``head`` holds. The
    files used are taken at both commits, so a module imported at only one of them counts.
    ``checkout``: as for :func:`notebook_modules`.
    """
    roots = notebook_modules(sources, checkout=checkout)
    before, after = tree_blobs(repo, ref), tree_blobs(repo, head)
    paths = used_files(repo, before, roots) | used_files(repo, after, roots)
    paths |= set(BUILD_FILES)
    changed = {p for p in paths if before.get(p) != after.get(p)}
    if _install_table_at(repo, before) != _install_table_at(repo, after):
        changed.add("pyproject.toml")
    return sorted(changed)
