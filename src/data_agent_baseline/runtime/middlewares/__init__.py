"""DEPRECATED: runtime.middlewares

This folder was an intermediate step. The current architecture consolidates
context control into the runtime kernel with explicit separation:
- policy (decisions)
- renderer (prompt construction)
- reducer (state updates)

Kept temporarily for historical reference; not used by current graph runtimes.
"""
