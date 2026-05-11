"""Checkpointer adapter."""

from __future__ import annotations

from typing import Any


def build_checkpointer(kind: str = "memory", database_url: str | None = None) -> Any | None:
    """Return a LangGraph checkpointer.

    The lab defaults to MemorySaver so tests run without infrastructure. SQLite/Postgres are kept
    behind optional dependencies for persistence/recovery demonstrations.
    """
    if kind == "none":
        return None
    if kind == "memory":
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()
    if kind == "sqlite":
        try:
            import sqlite3

            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError as exc:
            msg = "SQLite checkpointer requires: pip install langgraph-checkpoint-sqlite"
            raise RuntimeError(msg) from exc

        # Keep one connection open for the saver; WAL makes repeated lab runs resilient.
        conn = sqlite3.connect(database_url or "checkpoints.db", check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return SqliteSaver(conn=conn)
    if kind == "postgres":
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
        except ImportError as exc:
            msg = "Postgres checkpointer requires: pip install langgraph-checkpoint-postgres"
            raise RuntimeError(msg) from exc
        return PostgresSaver.from_conn_string(database_url or "")
    raise ValueError(f"Unknown checkpointer kind: {kind}")
