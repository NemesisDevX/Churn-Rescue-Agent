"""Customer and retention-call repository.

This is intentionally simple SQL-over-SQLite. It is *not* trying to be an
ORM; we keep the queries explicit so the "mock CRM" stays transparent during
a fast-moving hackathon.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .config import settings
from .db import Customer, db

logger = logging.getLogger(__name__)


class CustomerRepository:
    """Data access for customers, retention calls, and webhook idempotency."""

    def __init__(self, database: Any | None = None) -> None:
        self.db = database or db

    # ------------------------------------------------------------------
    # Customer helpers
    # ------------------------------------------------------------------

    async def get_by_id(self, customer_id: str) -> Customer | None:
        row = await self.db.fetchone(
            "SELECT * FROM customers WHERE id = ?", (customer_id,)
        )
        return Customer.from_row(row)

    async def get_by_phone(self, phone: str) -> Customer | None:
        row = await self.db.fetchone(
            "SELECT * FROM customers WHERE phone = ?", (phone,)
        )
        return Customer.from_row(row)

    async def list_all(self) -> list[Customer]:
        rows = await self.db.fetchall("SELECT * FROM customers ORDER BY name")
        return [Customer.from_row(r) for r in rows if r]

    async def update_churn_status(self, customer_id: str, churn_status: str) -> None:
        await self.db.execute(
            """UPDATE customers
               SET churn_status = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (churn_status, customer_id),
        )
        logger.info("Customer %s churn_status -> %s", customer_id, churn_status)

    async def update_allowed_discount(self, customer_id: str, discount: float) -> None:
        await self.db.execute(
            """UPDATE customers
               SET allowed_discount = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (discount, customer_id),
        )

    async def update_ltv(self, customer_id: str, ltv: float) -> None:
        await self.db.execute(
            """UPDATE customers
               SET ltv = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (ltv, customer_id),
        )

    async def upsert(self, customer: Customer) -> None:
        await self.db.execute(
            """INSERT INTO customers (
                   id, name, phone, email, subscription_tier,
                   monthly_amount, months_active, ltv, churn_status,
                   allowed_discount
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   name = excluded.name,
                   phone = excluded.phone,
                   email = excluded.email,
                   subscription_tier = excluded.subscription_tier,
                   monthly_amount = excluded.monthly_amount,
                   months_active = excluded.months_active,
                   ltv = excluded.ltv,
                   churn_status = excluded.churn_status,
                   allowed_discount = excluded.allowed_discount,
                   updated_at = CURRENT_TIMESTAMP""",
            (
                customer.id,
                customer.name,
                customer.phone,
                customer.email,
                customer.subscription_tier,
                customer.monthly_amount,
                customer.months_active,
                customer.ltv,
                customer.churn_status,
                customer.allowed_discount,
            ),
        )

    # ------------------------------------------------------------------
    # Retention-call helpers
    # ------------------------------------------------------------------

    async def create_retention_call(self, customer_id: str, status: str = "initiated") -> int:
        cursor = await self.db.execute(
            """INSERT INTO retention_calls (customer_id, status, created_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)""",
            (customer_id, status),
        )
        return cursor.lastrowid

    async def update_retention_call(
        self,
        retention_id: int,
        **kwargs: Any,
    ) -> None:
        allowed = {
            "call_e_id",
            "status",
            "task",
            "started_at",
            "completed_at",
            "transcript_json",
            "result_json",
            "cancellation_reason",
            "sentiment_label",
            "sentiment_score",
            "offered_discount",
            "accepted_discount",
            "churn_resolved",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return

        # JSON-encode structured fields so the repository stays consistent.
        for key in ("transcript_json", "result_json"):
            if key in updates and updates[key] is not None and not isinstance(updates[key], (str, bytes)):
                updates[key] = json.dumps(updates[key])

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [retention_id]
        await self.db.execute(
            f"""UPDATE retention_calls
                SET {set_clause}, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?""",
            tuple(values),
        )

    # ------------------------------------------------------------------
    # Webhook idempotency
    # ------------------------------------------------------------------

    async def is_event_processed(self, event_id: str) -> bool:
        row = await self.db.fetchone(
            "SELECT 1 FROM processed_webhook_events WHERE id = ?", (event_id,)
        )
        return row is not None

    async def mark_event_processed(self, event_id: str) -> None:
        await self.db.execute(
            """INSERT INTO processed_webhook_events (id) VALUES (?)
               ON CONFLICT(id) DO NOTHING""",
            (event_id,),
        )


repo = CustomerRepository()
