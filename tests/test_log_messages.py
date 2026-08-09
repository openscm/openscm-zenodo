"""
Tests of the logging convention itself

`INFO` states what happened, in the past tense, so a message reads correctly
however deeply it is nested and whoever called it. Intentions ("Retrieving
X...") belong at `DEBUG`, where the audience is somebody working out where a call
got to. This checks the rule mechanically, because it is the kind of thing which
erodes one plausible message at a time.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parents[1] / "src" / "openscm_zenodo"


def iter_info_messages(path):
    """
    Get the leading literal text of every `logger.info` call in a file

    Yields `(line number, text)`. Calls whose message starts with a substituted
    value are skipped, because there is no word there to look at.
    """
    tree = ast.parse(path.read_text())

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "info"
            and isinstance(func.value, ast.Name)
            and func.value.id == "logger"
        ):
            continue

        if not node.args:
            continue

        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            yield node.lineno, first.value

        elif isinstance(first, ast.JoinedStr) and first.values:
            leading = first.values[0]
            if isinstance(leading, ast.Constant) and isinstance(leading.value, str):
                yield node.lineno, leading.value


@pytest.mark.parametrize("path", sorted(SRC.rglob("*.py")), ids=lambda path: path.name)
def test_info_messages_state_what_happened(path):
    """
    No `INFO` message opens with an intention

    A leading present participle is the tell: it announces work rather than
    reporting it, which is a claim the function is not in a position to make. It
    is also the thing which reads badly when one method calls another, because
    the intentions stack up and any of them may then fail.
    """
    intentions = [
        (lineno, text)
        for lineno, text in iter_info_messages(path)
        if text.split(" ")[0].lower().endswith("ing")
    ]

    assert not intentions, (
        f"{path.name} logs an intention at `INFO`: "
        + "; ".join(f"line {lineno}: {text!r}" for lineno, text in intentions)
        + ". State what happened instead, in the past tense, "
        "and put the intention at `DEBUG` if it is worth keeping."
    )
