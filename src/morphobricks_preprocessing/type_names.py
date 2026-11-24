"""The name of a value's type as a message shows it (the one place; issue #470)."""

from __future__ import annotations


def type_name(value: object) -> str:
    """Return ``value``'s type name: short for a builtin, qualified for any other type.

    ``str`` and ``int`` stay short; ``numpy.bool`` and ``numpy.int64`` carry their module,
    since the short name of a numpy bool (``bool``) would read as the builtin it is not.
    """
    kind = type(value)
    if kind.__module__ == "builtins":
        return kind.__qualname__
    return f"{kind.__module__}.{kind.__qualname__}"
