"""SQLite schema and async-ish data access helpers.

We use the stdlib `sqlite3` module plus `asyncio.to_thread` so the FastAPI
event loop never blocks on disk I/O. This keeps dependencies minimal and
makes the mock CRM portable for a hackathon demo.
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import settings

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE,
    email TEXT,
    subscription_tier TEXT NOT NULL,
    monthly_amount REAL NOT NULL,
    months_active INTEGER NOT NULL,
    ltv REAL,
    churn_status TEXT NOT NULL DEFAULT 'active',
    allowed_discount REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS retention_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL,
    call_e_id TEXT,
    status TEXT NOT NULL DEFAULT 'initiated',
    task TEXT,
    started_at TEXT,
    completed_at TEXT,
    transcript_json TEXT,
    result_json TEXT,
    cancellation_reason TEXT,
    sentiment_label TEXT,
    sentiment_score REAL,
    offered_discount REAL,
    accepted_discount TEXT,
    churn_resolved INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE TABLE IF NOT EXISTS processed_webhook_events (
    id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);
CREATE INDEX IF NOT EXISTS idx_retention_calls_customer ON retention_calls(customer_id);
CREATE INDEX IF NOT EXISTS idx_retention_calls_call_e_id ON retention_calls(call_e_id);
"""


def init_db(db_path: str) -> None:
    """Create tables and indexes. Safe to call on every startup."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()
    logger.info("SQLite mock CRM initialized at %s", db_path)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _fetchone(db_path: str, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def _fetchall(db_path: str, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]


def _execute(db_path: str, sql: str, params: tuple[Any, ...]) -> sqlite3.Cursor:
    with _connect(db_path) as conn:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor


def _execute_many(db_path: str, sql: str, params: list[tuple[Any, ...]]) -> sqlite3.Cursor:
    with _connect(db_path) as conn:
        cursor = conn.executemany(sql, params)
        conn.commit()
        return cursor


class AsyncDB:
    """Thin async wrapper around stdlib sqlite3.

    Every operation runs in the default `asyncio` thread pool so FastAPI
    endpoints stay responsive.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path

    async def _run(self, fn, *args):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, functools.partial(fn, self._db_path, *args))

    async def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        return await self._run(_fetchone, sql, params)

    async def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return await self._run(_fetchall, sql, params)

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return await self._run(_execute, sql, params)

    async def execute_many(self, sql: str, params: list[tuple[Any, ...]]) -> sqlite3.Cursor:
        return await self._run(_execute_many, sql, params)


db = AsyncDB(settings.database_path)


@dataclass
class Customer:
    id: str
    name: str
    phone: str
    email: str | None
    subscription_tier: str
    monthly_amount: float
    months_active: int
    ltv: float | None
    churn_status: str
    allowed_discount: float | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: dict[str, Any] | None) -> Customer | None:
        if not row:
            return None
        return cls(
            id=row["id"],
            name=row["name"],
            phone=row["phone"],
            email=row["email"],
            subscription_tier=row["subscription_tier"],
            monthly_amount=row["monthly_amount"],
            months_active=row["months_active"],
            ltv=row.get("ltv"),
            churn_status=row["churn_status"],
            allowed_discount=row.get("allowed_discount"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
