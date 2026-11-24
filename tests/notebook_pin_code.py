"""The code half of the pin check (:mod:`notebook_pin`): what package code a name or file runs.

Moved from ``notebook_pin.py`` unchanged (600-line rule, #463 review) and re-exported there:
the loaders a cell or package module can call (:func:`_loader`, :func:`_hidden_loader`), the
package module names in strings (:data:`DOTTED_NAME`), and the package modules a commit's
files import at any depth (:func:`module_closure`, :func:`used_files`).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from notebook_pin_build import PACKAGE, SOURCE_ROOT, PinScopeError, _blob_texts

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
DOTTED_NAME = re.compile(rf"{PACKAGE}(\.[A-Za-z_]\w*)*(:[A-Za-z_]\w*(\.[A-Za-z_]\w*)*)?")
"""A string that names a module of the package (``morphobricks_preprocessing.cli``), or an
object in one by the ``module:attribute`` form of entry points and ``pkgutil.resolve_name``
(``morphobricks_preprocessing.cli:main``). The string is followed whatever code it is given
to, as an import of the module (and of the attribute, should it be a submodule)."""


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


def used_files(repo: Path, blobs: dict[str, str], roots: set[str]) -> set[str]:
    """Return the package files a commit's ``blobs`` run for ``roots``, and its data files."""
    package = f"{SOURCE_ROOT}/{PACKAGE}/"
    python = sorted(p for p in blobs if p.startswith(f"{SOURCE_ROOT}/") and p.endswith(".py"))
    files = dict(zip(python, _blob_texts(repo, [blobs[p] for p in python]), strict=True))
    data = {p for p in blobs if p.startswith(package) and not p.endswith(".py")}
    return module_closure(files, roots) | data
