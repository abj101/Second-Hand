"""Memory architecture parity notes (Phase 7).

Overlay (default STS2 collect):
  Passive Notebook text in the user turn. Phase 3's Journal→Notebook bridge is the
  overlay path's equivalent of queryable memory — no tool round-trips in a single-shot
  think:false 4096-ctx call.

Two-stage:
  Superset via memory_recall / memory_remember / memory_notebook MCP tools on the reasoner.
  Tools read/write the same FactJournal + Notebook stores; they are not a separate backend.

Decision: Option A — overlay does not get tool-calling; bridge + static Notebook fields
are intentional parity for production data collection.
"""
