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
line; in package code, a module loaded by a computed name or a file or
text run; in the build, a second wheel package, an unknown backend or ``[tool.hatch]``
setting, or a ``hatch.toml``. Package code that names a package module in a string runs it
as an import.

Known gaps: package code that assembles a module name at runtime and passes it to
something other than a loader (``[sys.executable, "-m", f"{PACKAGE}.{name}"]``) is not seen.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

PACKAGE = "morphobricks_preprocessing"
DISTRIBUTION = "morphobricks-preprocessing"
SOURCE_ROOT = "src"
BUILD_FILES = (".gitignore",)
"""The files the build reads besides the package and ``pyproject.toml``: hatchling leaves out
what ``.gitignore`` names, so it can change the installed files. A ``hatch.toml`` is refused
(:data:`HATCH_KEYS`)."""
HATCH_KEYS = frozenset(
    {"metadata.allow-direct-references", "build.targets.wheel.packages", "build.exclude"}
)
"""The ``[tool.hatch]`` settings the check knows to add no file outside the package. They are
compared, so a change of one is reported as ``pyproject.toml``: ``build.exclude`` applies to
every target, the wheel included, but only leaves files out. Any other setting
(``force-include``, ``sources``, build hooks, a version source) or a ``hatch.toml`` can put
other files in the wheel and is refused."""
HATCH_IGNORED = frozenset({"build.targets.sdist", "envs"})
"""The ``[tool.hatch]`` tables that do not change the wheel pip builds from Git, left out of
the comparison: pip builds the wheel from the source tree, not from an sdist, and ``envs``
are Hatch's development environments."""
BACKEND_REQUIRES = {"hatchling.build": "hatchling"}
"""The one requirement of each known backend. A further one (a hatch plugin) may read any
``[tool]`` table, so then the whole ``pyproject.toml`` is compared."""
BACKEND_TABLES = {"hatchling.build": "hatch"}
"""The ``[tool]`` table each known build backend reads. Every other ``[tool]`` table (Ruff,
pytest, the code rules) is read by no build and by no code of the package, so a change there
installs the same thing. An unknown backend is refused: what it puts in the wheel is unknown."""
WHEEL_PACKAGES = [f"{SOURCE_ROOT}/{PACKAGE}"]
"""The wheel's ``packages``: imports are resolved in this one package, so a second top-level
package in the wheel is refused."""
NAME_LOADERS = frozenset({"import_module", "__import__"})
"""Calls that import a module by a name given as a value, not in an import statement."""
FILE_LOADERS = frozenset({"spec_from_file_location", "SourceFileLoader", "run_path", "run_module"})
"""Calls that run a file or a module without an import statement (``importlib``, ``runpy``)."""
LOADER_MODULES = frozenset({"importlib", "runpy"})
"""The modules the :data:`NAME_LOADERS` and :data:`FILE_LOADERS` come from. Loaders are matched
by their own name, so one bound to another name (``import ... as``) or looked up on these
modules by ``getattr`` is refused (:func:`_hidden_loader`)."""
TEXT_LOADERS = frozenset({"exec", "eval", "compile"})
"""Builtins that run code from text. Matched only as bare names (or on ``builtins``): a method
of the same name, such as a model's ``eval()``, runs no text."""
SHELLS = frozenset({"subprocess", "os"})
"""The modules whose :data:`COMMANDS` start a program. A cell uses them only by their own name
(no ``as``, no ``from ... import``, no other binding), so every command call is seen."""
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
INSTALL_SPEC = re.compile(r"morphobricks[-_.]preprocessing|git\+", re.I)
"""Text that can name this package (or any Git source) for ``pip``."""
METADATA_CALLS = frozenset({"version", "distribution"})
"""``importlib.metadata`` calls that take the bare distribution name and install nothing."""
HIDDEN_RUNNERS = frozenset({"get_ipython"})
"""Names that run commands or code the cell's syntax does not show (IPython's ``system``)."""


DOTTED_NAME = re.compile(rf"{PACKAGE}(\.[A-Za-z_]\w*)*(:[A-Za-z_]\w*(\.[A-Za-z_]\w*)*)?")
"""A string that names a module of the package (``morphobricks_preprocessing.cli``), or an
object in one by the ``module:attribute`` form of entry points and ``pkgutil.resolve_name``
(``morphobricks_preprocessing.cli:main``). The string is followed whatever code it is given
to, as an import of the module (and of the attribute, should it be a submodule)."""


class PinScopeError(Exception):
    """Raised when what a notebook or the package uses cannot be derived from the source."""


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


def _call_name(node: ast.Call) -> str | None:
    """Return the name a call is made by (``f`` of ``f()`` or ``m.f()``), or None."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _loader(node: ast.Call) -> str | None:
    """Return the name of a call that loads code no import statement shows, or None."""
    name = _call_name(node)
    if name in NAME_LOADERS | FILE_LOADERS:
        return name
    owner = node.func.value if isinstance(node.func, ast.Attribute) else None
    builtin = owner is None or (
        isinstance(owner, ast.Name) and owner.id in ("builtins", "__builtins__")
    )
    return name if name in TEXT_LOADERS and builtin else None


def _hidden_loader(node: ast.AST) -> str | None:
    """Return the code of a loader reached by a name :func:`_loader` cannot match, or None.

    That is an ``as`` alias of a :data:`NAME_LOADERS` or :data:`FILE_LOADERS` name (``from
    importlib import import_module as load``) or ``getattr`` on a :data:`LOADER_MODULES`
    module (``getattr(importlib, "import_module")``).
    """
    if isinstance(node, ast.Import | ast.ImportFrom):
        loaders = NAME_LOADERS | FILE_LOADERS
        aliased = any(a.asname and a.name.split(".")[-1] in loaders for a in node.names)
        return ast.unparse(node) if aliased else None
    if isinstance(node, ast.Call) and _call_name(node) == "getattr" and node.args:
        owner = node.args[0]
        while isinstance(owner, ast.Attribute):
            owner = owner.value
        if isinstance(owner, ast.Name) and owner.id in LOADER_MODULES:
            return ast.unparse(node)
    return None


def _starts_pip(node: ast.Call) -> bool:
    """Return whether a command call runs ``sys.executable -m pip``."""
    if not node.args or not isinstance(node.args[0], ast.List):
        return False
    head = [ast.unparse(item) for item in node.args[0].elts[:3]]
    return head == ["sys.executable", "'-m'", "'pip'"]


def _refuse_hidden_commands(tree: ast.AST, where: str) -> None:
    """Raise ``PinScopeError`` for a cell command the :data:`COMMANDS` check could miss.

    A command module bound to another name (``import subprocess as sp``, ``from os import
    system``, ``runner = subprocess``, ``getattr(os, ...)``), a command kept to call later
    (``start = subprocess.run``) and IPython's runners are refused.
    """
    owners = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad = [a.name for a in node.names if a.name in SHELLS and a.asname]
        elif isinstance(node, ast.ImportFrom):
            bad = [node.module] if node.module in SHELLS else []
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


def _refuse_unseen_use(tree: ast.AST, where: str) -> None:
    """Raise ``PinScopeError`` for a notebook use of code no import statement shows."""
    _refuse_hidden_commands(tree, where)
    for node in ast.walk(tree):
        if hidden := _hidden_loader(node):
            raise PinScopeError(f"{where} reaches a loader the check cannot follow: {hidden}")
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
        if is_command and name in COMMANDS and not _starts_pip(node):
            raise PinScopeError(f"{where} runs a command other than pip: {ast.unparse(node)}")


def _package_imports(tree: ast.AST, where: str) -> set[str]:
    """Return the package modules (and imported names) a cell's import statements name."""
    names = set()
    for node in ast.walk(tree):
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
    argument a call of that function gives it. Names are not told apart by scope.
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
            if name in params:
                at = params.index(name)
                calls = [n for n in nodes if isinstance(n, ast.Call) and _call_name(n) == node.name]
                values.extend(c.args[at] for c in calls if len(c.args) > at)
                values.extend(k.value for c in calls for k in c.keywords if k.arg == name)
    return values


def _pip_words(command: ast.Call, nodes: list[ast.AST]) -> list[str]:
    """Return every literal word a cell's ``pip`` command gets after ``-m pip``.

    Words written in the call and words that reach it through variables are both returned:
    a name (or ``*name``) is followed to its :func:`_values_of`, through lists and further
    names, so a word added to a list elsewhere, or the bare distribution name a cell function
    is given and passes on in a copy or a list, is seen like one written in the call. An
    f-string is not opened: :func:`_refuse_unpinned_install` checks its text where it is
    written.
    """
    words, pending, seen = [], list(command.args[0].elts[3:]), set()
    while pending:
        item = pending.pop()
        if isinstance(item, ast.Starred):
            pending.append(item.value)
        elif isinstance(item, ast.List | ast.Tuple):
            pending.extend(item.elts)
        elif isinstance(item, ast.IfExp):
            pending.extend([item.body, item.orelse])
        elif isinstance(item, ast.Name) and item.id not in seen:
            seen.add(item.id)
            pending.extend(_values_of(item.id, nodes))
        elif isinstance(item, ast.Constant) and isinstance(item.value, str):
            words.append(item.value)
    return words


def _refuse_unpinned_install(trees: list[ast.AST]) -> None:
    """Raise ``PinScopeError`` unless every install of the package in the cells is pinned.

    ``RELEASE_REF`` and ``REPOSITORY`` are each set once (and named in no string); a ``pip``
    command is given no literal word but :data:`PIP_WORDS`, written in it or reaching it
    through variables (:func:`_pip_words`); and every string that can name the package for
    ``pip`` is the f-string ending ``@{RELEASE_REF}``, the ``REPOSITORY`` value, or the bare
    distribution name passed to a metadata call or a function the cells define. Such a
    function may read the installed package by that name, never install it: the name
    reaching ``pip`` is a word :data:`PIP_WORDS` does not hold.
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
    allowed = set()
    for node in nodes:
        if isinstance(node, ast.JoinedStr):
            allowed |= {id(v) for v in node.values} | ({id(node)} if _pinned(node) else set())
        elif isinstance(node, ast.Call) and _call_name(node) in METADATA_CALLS | defined:
            allowed |= {id(a) for a in node.args if getattr(a, "value", None) == DISTRIBUTION}
        elif isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "REPOSITORY":
            allowed.add(id(node.value))
        if isinstance(node, ast.Call) and _starts_pip(node):
            unknown = sorted(set(_pip_words(node, nodes)) - PIP_WORDS)
            if unknown:
                raise PinScopeError(f"a pip command the check does not know: {unknown}")
    for node in nodes:
        text = _f_string_text(node) if isinstance(node, ast.JoinedStr) else None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        if text is not None and INSTALL_SPEC.search(text) and id(node) not in allowed:
            raise PinScopeError(f"an install source other than @{{RELEASE_REF}}: {text!r}")


def notebook_modules(sources: list[str]) -> set[str]:
    """Return the package modules and names the notebook cells ``sources`` import.

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
        _refuse_unseen_use(tree, f"cell {number}")
        names |= _package_imports(tree, f"cell {number}")
        trees.append(tree)
    _refuse_unpinned_install(trees)
    if not names:
        raise PinScopeError(f"no cell imports {PACKAGE}: nothing to compare with the pin")
    return names


def _module_path(module: str, files: dict[str, str]) -> str | None:
    """Return the file of ``module`` among ``files`` (a package's ``__init__.py``), or None."""
    stem = "/".join((SOURCE_ROOT, *module.split(".")))
    for path in (f"{stem}/__init__.py", f"{stem}.py"):
        if path in files:
            return path
    return None


def _absolute(node: ast.ImportFrom, module: str, path: str) -> str:
    """Return the absolute module a (possibly relative) ``from`` import of ``module`` names."""
    if not node.level:
        return node.module
    parts = module.split(".")
    if not path.endswith("/__init__.py"):
        parts = parts[:-1]
    parts = parts[: len(parts) - node.level + 1]
    return ".".join([*parts, *([node.module] if node.module else [])])


def _refuse_computed_name(node: ast.Call, path: str) -> None:
    """Raise ``PinScopeError`` unless a loader call in the package names a module literally.

    The literal name itself is taken as an import by :func:`_imported`, like every package
    module named in a string.
    """
    argument = node.args[0] if node.args else None
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        if not argument.value.startswith("."):
            return
    raise PinScopeError(f"{path} loads code by a computed name: {ast.unparse(node)}")


def _imported(text: str, module: str, path: str) -> list[str]:
    """Return every module (or module.name) the file ``path`` of ``module`` imports.

    Every string that is a dotted name of the package counts as an import too: code can run a
    module it names (``import_module("...")``, ``[sys.executable, "-m", "..."]``) without an
    import statement.
    """
    names = []
    for node in ast.walk(ast.parse(text)):
        if hidden := _hidden_loader(node):
            raise PinScopeError(f"{path} reaches a loader the check cannot follow: {hidden}")
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _absolute(node, module, path)
            names.extend([base, *(f"{base}.{alias.name}" for alias in node.names)])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            named = DOTTED_NAME.fullmatch(node.value)
            names.extend([node.value.replace(":", ".")] if named else [])
        elif isinstance(node, ast.Call) and _loader(node) in NAME_LOADERS:
            _refuse_computed_name(node, path)
        elif isinstance(node, ast.Call) and _loader(node):
            raise PinScopeError(f"{path} runs code from a file or text: {ast.unparse(node)}")
    return names


def module_closure(files: dict[str, str], roots: set[str]) -> set[str]:
    """Return the files of the package modules ``roots`` run, through every import.

    ``files`` maps each ``src/`` Python file of one commit to its text. Importing a module runs
    its parent packages' ``__init__.py`` too, so they are included; an imported name that is
    not a module (a function) adds nothing beyond its module.
    """
    seen: set[str] = set()
    paths: set[str] = set()
    pending = list(roots)
    while pending:
        parts = pending.pop().split(".")
        for depth in range(1, len(parts) + 1):
            module = ".".join(parts[:depth])
            path = _module_path(module, files) if parts[0] == PACKAGE else None
            if path is None or module in seen:
                continue
            seen.add(module)
            paths.add(path)
            pending.extend(_imported(files[path], module, path))
    return paths


def _git(repo: Path, *arguments: str, data: bytes | None = None) -> bytes:
    """Return the output of ``git -C repo arguments`` (raises when git fails)."""
    command = ["git", "-C", str(repo), *arguments]
    return subprocess.run(command, input=data, capture_output=True, check=True).stdout


def tree_blobs(repo: Path, rev: str) -> dict[str, str]:
    """Return every file of commit ``rev`` with its blob ID."""
    listing = _git(repo, "ls-tree", "-r", "-z", rev).decode("utf-8").split("\0")
    entries = (line.split("\t", 1) for line in listing if line)
    return {path: meta.split()[2] for meta, path in entries}


def _blob_texts(repo: Path, blobs: list[str]) -> list[str]:
    """Return the text of each blob ID in ``blobs``, read by one ``git cat-file --batch``."""
    out = _git(repo, "cat-file", "--batch", data="".join(f"{b}\n" for b in blobs).encode())
    texts, at = [], 0
    for _ in blobs:
        header_end = out.index(b"\n", at)
        size = int(out[at:header_end].split()[2])
        texts.append(out[header_end + 1 : header_end + 1 + size].decode("utf-8"))
        at = header_end + 2 + size  # the content is followed by one newline
    return texts


def install_table(text: str) -> dict:
    """Return ``pyproject.toml`` without the ``[tool]`` tables no build reads.

    ``[project]`` (dependencies, extras, version, scripts) and ``[build-system]`` are kept
    whole; of ``[tool]`` only the build backend's own table is kept, without
    :data:`HATCH_IGNORED` (all of it with a build plugin). Raises ``PinScopeError`` for a
    backend not in :data:`BACKEND_TABLES` or a wheel whose packages are not
    :data:`WHEEL_PACKAGES`.
    """
    data = tomllib.loads(text)
    backend = data.get("build-system", {}).get("build-backend")
    if backend not in BACKEND_TABLES:
        raise PinScopeError(f"pyproject.toml has an unknown build backend: {backend!r}")
    own = _without(data.get("tool", {}).get(BACKEND_TABLES[backend], {}), HATCH_IGNORED)
    unknown = _leaf_keys(own) - HATCH_KEYS
    if unknown:
        raise PinScopeError(f"[tool.hatch] has settings the check does not know: {unknown}")
    wheel = own.get("build", {}).get("targets", {}).get("wheel", {})
    if wheel.get("packages") != WHEEL_PACKAGES:
        raise PinScopeError(f"the wheel's packages are not {WHEEL_PACKAGES}: {wheel}")
    requires = data.get("build-system", {}).get("requires", [])
    names = {re.match(r"[A-Za-z0-9._-]*", r).group().lower() for r in requires}
    if names == {BACKEND_REQUIRES[backend]}:
        data["tool"] = {BACKEND_TABLES[backend]: own}
    return data


def _without(table: dict, dropped: frozenset[str], prefix: str = "") -> dict:
    """Return a copy of ``table`` without the subtables whose dotted key is in ``dropped``."""
    return {
        key: _without(value, dropped, f"{prefix}{key}.") if isinstance(value, dict) else value
        for key, value in table.items()
        if f"{prefix}{key}" not in dropped
    }


def _leaf_keys(table: dict, prefix: str = "") -> set[str]:
    """Return the dotted key of every value in ``table`` that is not itself a table."""
    keys = set()
    for key, value in table.items():
        name = f"{prefix}{key}"
        keys |= _leaf_keys(value, f"{name}.") if isinstance(value, dict) else {name}
    return keys


def _install_table_at(repo: Path, blobs: dict[str, str]) -> dict:
    """Return :func:`install_table` of a commit's ``pyproject.toml``."""
    if "pyproject.toml" not in blobs:
        raise PinScopeError("a commit has no pyproject.toml: what it installs is unknown")
    if "hatch.toml" in blobs:
        raise PinScopeError("a commit has a hatch.toml: its build settings are not checked")
    return install_table(_blob_texts(repo, [blobs["pyproject.toml"]])[0])


def used_files(repo: Path, blobs: dict[str, str], roots: set[str]) -> set[str]:
    """Return the package files a commit's ``blobs`` run for ``roots``, and its data files."""
    package = f"{SOURCE_ROOT}/{PACKAGE}/"
    python = sorted(p for p in blobs if p.startswith(f"{SOURCE_ROOT}/") and p.endswith(".py"))
    files = dict(zip(python, _blob_texts(repo, [blobs[p] for p in python]), strict=True))
    data = {p for p in blobs if p.startswith(package) and not p.endswith(".py")}
    return module_closure(files, roots) | data


def pin_differences(repo: Path, ref: str, head: str, sources: list[str]) -> list[str]:
    """Return what the notebook cells ``sources`` use that differs between ``ref`` and ``head``.

    Empty when the notebook, installing ``ref``, runs exactly the code ``head`` holds. The
    files used are taken at both commits, so a module imported at only one of them counts.
    """
    roots = notebook_modules(sources)
    before, after = tree_blobs(repo, ref), tree_blobs(repo, head)
    paths = used_files(repo, before, roots) | used_files(repo, after, roots)
    paths |= set(BUILD_FILES)
    changed = {p for p in paths if before.get(p) != after.get(p)}
    if _install_table_at(repo, before) != _install_table_at(repo, after):
        changed.add("pyproject.toml")
    return sorted(changed)
