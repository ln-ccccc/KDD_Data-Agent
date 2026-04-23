"""Agent runtime kernel (orchestrator utilities).

Design principles:
- SSOT: LangGraph state is the single source of truth.
- Separate policy (decisions) from rendering (prompt construction) and state updates (reducers).
- Keep mechanisms deterministic and testable; keep learning/adaptation optional.
"""

