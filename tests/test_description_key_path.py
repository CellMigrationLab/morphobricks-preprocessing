"""Table paths quote a key as TOML does, escapes included (#406)."""

import tomllib

import pytest

from morphobricks_preprocessing.description_report import key_path
from morphobricks_preprocessing.description_write import toml_key


@pytest.mark.parametrize(
    ("key", "path"),
    [
        ('a"b', 'base."a\\"b"'),
        ("a\\b", 'base."a\\\\b"'),
        ("a\nb", 'base."a\\nb"'),
        ("a b", 'base."a b"'),
        ("plain_key-1", "base.plain_key-1"),
    ],
)
def test_a_key_is_quoted_and_escaped_as_toml_quotes_it(key, path):
    assert key_path("base", key) == path
    assert key_path("base", key) == f"base.{toml_key(key)}"  # the writer's spelling
    assert tomllib.loads(f"{key_path('base', key)} = 1")["base"] == {key: 1}  # one key, back
