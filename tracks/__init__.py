"""Agent on Tracks v0.1 — event-sourced runtime.

Public surface is intentionally small; the event log (SQLite `events`) is the
sole source of truth (D-02). Everything else is a derived projection.
"""

__version__ = "0.8.0"
