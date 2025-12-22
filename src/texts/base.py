from __future__ import annotations

from collections.abc import Callable

Translator = Callable[[str], str]


def noop_t(s: str) -> str:
    return s
