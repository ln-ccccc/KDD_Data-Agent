from __future__ import annotations

"""DEPRECATED: middleware interface.

Replaced by runtime.kernel (policy/renderer/reducer) to avoid split-brain context
control and God-object managers.
"""

from typing import Any, Protocol


class Middleware(Protocol):
    """A minimal middleware interface for orchestration.

    We only need a hook before each LLM call for context control.
    """

    def before_llm(self, state: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class MiddlewareChain:
    def __init__(self, middlewares: list[Middleware] | None = None) -> None:
        self._middlewares = list(middlewares or [])

    def before_llm(self, state: dict[str, Any]) -> dict[str, Any]:
        for mw in self._middlewares:
            state = mw.before_llm(state)
        return state
