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
(code loaded by a computed name, a command other than ``python -m pip``) is refused with
:class:`PinScopeError`, never assumed unchanged.
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
SOURCE_ROOT = "src"
BUILD_FILES = ("hatch.toml", ".gitignore")
"""The files the build reads besides the package and ``pyproject.toml``: hatchling reads
``hatch.toml`` when it exists and leaves out what ``.gitignore`` names, so either can change
the installed files."""
BACKEND_TABLES = {"hatchling.build": "hatch"}
"""The ``[tool]`` table each known build backend reads. Every other ``[tool]`` table (Ruff,
pytest, the code rules) is read by no build and by no code of the package, so a change there
installs the same thing. With an unknown backend the whole ``pyproject.toml`` is compared."""
LOADERS = frozenset(
    {"import_module", "__import__", "exec", "eval", "compile", "run_module", "run_path"}
)
"""Calls that load code by a name or text the import statements do not show."""
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
HIDDEN_RUNNERS = frozenset({"get_ipython"})
"""Names that run commands or code the cell's syntax does not show (IPython's ``system``)."""


DOTTED_NAME = re.compile(rf"{PACKAGE}(\.[A-Za-z_][A-Za-z0-9_]*)*")
"""A string that names a module of the package (``morphobricks_preprocessing.cli``)."""


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
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if PACKAGE in node.value:
                raise PinScopeError(f"{where} names {PACKAGE} outside an import: {node.value!r}")
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in LOADERS:
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


def notebook_modules(sources: list[str]) -> set[str]:
    """Return the package modules and names the notebook cells ``sources`` import.

    Raises ``PinScopeError`` when a cell reaches code another way (see the module docstring)
    or when no cell imports the package at all.
    """
    names = set()
    for number, source in enumerate(sources, start=1):
        tree = ast.parse(source)
        _refuse_unseen_use(tree, f"cell {number}")
        names |= _package_imports(tree, f"cell {number}")
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
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _absolute(node, module, path)
            names.extend([base, *(f"{base}.{alias.name}" for alias in node.names)])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.extend([node.value] if DOTTED_NAME.fullmatch(node.value) else [])
        elif isinstance(node, ast.Call) and _call_name(node) in ("import_module", "__import__"):
            _refuse_computed_name(node, path)
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
    whole; of ``[tool]`` only the build backend's own table is kept, or all of it when the
    backend is not in :data:`BACKEND_TABLES`.
    """
    data = tomllib.loads(text)
    backend = data.get("build-system", {}).get("build-backend")
    if backend in BACKEND_TABLES and "tool" in data:
        own = BACKEND_TABLES[backend]
        data["tool"] = {own: data["tool"][own]} if own in data["tool"] else {}
    return data


def _install_table_at(repo: Path, blobs: dict[str, str]) -> dict | None:
    """Return :func:`install_table` of a commit's ``pyproject.toml`` (None without one)."""
    blob = blobs.get("pyproject.toml")
    return None if blob is None else install_table(_blob_texts(repo, [blob])[0])


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
