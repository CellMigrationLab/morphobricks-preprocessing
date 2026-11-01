Deliberately bad modules for `tests/test_code_rules.py`: each one breaks one code rule, to
prove the rule fires. Ruff skips this folder (`extend-exclude`), and the code rules leave it
out of the repository check.
