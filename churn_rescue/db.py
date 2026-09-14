# sqlite roster + call_log. stdlib only, no orm.
# TODO: sqlite won't survive >1 writer, swap for postgres if this ships
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "churn_rescue.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id        TEXT PRIMARY KEY,
    company_name       TEXT NOT NULL,
    contact_name       TEXT NOT NULL,
    phone              TEXT NOT NULL UNIQUE,
    plan               TEXT NOT NULL,
    mrr                REAL NOT NULL,
    churn_risk_score   REAL NOT NULL CHECK (churn_risk_score BETWEEN 0.0 AND 1.0),
    ltv                REAL NOT NULL,
    contract_end_date  TEXT NOT NULL,
    competitor_threat  TEXT,
    status             TEXT NOT NULL DEFAULT 'at_risk',
    created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS call_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id    TEXT NOT NULL REFERENCES customers(customer_id),
    started_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at       TEXT,
    outcome        TEXT,
    discount_pct   REAL,
    states_visited TEXT,
    transcript     TEXT
);
"""


@dataclass
class Customer:
    customer_id: str
    company_name: str
    contact_name: str
    phone: str
    plan: str
    mrr: float
    churn_risk_score: float
    ltv: float
    contract_end_date: str
    competitor_threat: str | None
    status: str = "at_risk"

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict[str, Any]) -> "Customer":
        d = dict(row)
        return cls(
            customer_id=d["customer_id"],
            company_name=d["company_name"],
            contact_name=d["contact_name"],
            phone=d["phone"],
            plan=d["plan"],
            mrr=float(d["mrr"]),
            churn_risk_score=float(d["churn_risk_score"]),
            ltv=float(d["ltv"]),
            contract_end_date=d["contract_end_date"],
            competitor_threat=d.get("competitor_threat"),
            status=d.get("status", "at_risk"),
        )


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    # idempotent; runs at startup and from the seeder
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)


def upsert_customer(customer: Customer, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    sql = """
        INSERT INTO customers (
            customer_id, company_name, contact_name, phone, plan, mrr,
            churn_risk_score, ltv, contract_end_date, competitor_threat, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(customer_id) DO UPDATE SET
            company_name      = excluded.company_name,
            contact_name      = excluded.contact_name,
            phone             = excluded.phone,
            plan              = excluded.plan,
            mrr               = excluded.mrr,
            churn_risk_score  = excluded.churn_risk_score,
            ltv               = excluded.ltv,
            contract_end_date = excluded.contract_end_date,
            competitor_threat = excluded.competitor_threat,
            status            = excluded.status
    """
    with _connect(db_path) as conn:
        conn.execute(
            sql,
            (
                customer.customer_id,
                customer.company_name,
                customer.contact_name,
                customer.phone,
                customer.plan,
                customer.mrr,
                customer.churn_risk_score,
                customer.ltv,
                customer.contract_end_date,
                customer.competitor_threat,
                customer.status,
            ),
        )


def list_customers(db_path: str | Path = DEFAULT_DB_PATH) -> list[Customer]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM customers ORDER BY churn_risk_score DESC, ltv DESC"
        ).fetchall()
    return [Customer.from_row(r) for r in rows]


def get_customer(customer_id: str, db_path: str | Path = DEFAULT_DB_PATH) -> Customer | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM customers WHERE customer_id = ?", (customer_id,)
        ).fetchone()
    return Customer.from_row(row) if row else None


def set_customer_status(
    customer_id: str, status: str, db_path: str | Path = DEFAULT_DB_PATH
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE customers SET status = ? WHERE customer_id = ?",
            (status, customer_id),
        )


def log_call(
    customer_id: str,
    outcome: str,
    discount_pct: float | None,
    states_visited: list[str],
    transcript: list[dict[str, Any]],
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    import json
    from datetime import datetime, timezone

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO call_log (
                customer_id, ended_at, outcome, discount_pct, states_visited, transcript
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                customer_id,
                datetime.now(timezone.utc).isoformat(),
                outcome,
                discount_pct,
                json.dumps(states_visited),
                json.dumps(transcript),
            ),
        )
