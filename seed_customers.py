"""Seed the mock CRM with a few demo customers.

Run this once before the first demo:
    python seed_customers.py
"""
import asyncio

from churn_rescue.config import settings
from churn_rescue.db import Customer, init_db
from churn_rescue.crm import repo


SEED_DATA = [
    Customer(
        id="cust_001",
        name="Sarah Chen",
        phone="+14155551001",
        email="sarah.chen@example.com",
        subscription_tier="pro",
        monthly_amount=79.0,
        months_active=18,
        ltv=None,
        churn_status="active",
        allowed_discount=None,
        created_at="",
        updated_at="",
    ),
    Customer(
        id="cust_002",
        name="Marcus Johnson",
        phone="+14155551002",
        email="marcus.j@example.com",
        subscription_tier="basic",
        monthly_amount=29.0,
        months_active=6,
        ltv=None,
        churn_status="active",
        allowed_discount=None,
        created_at="",
        updated_at="",
    ),
    Customer(
        id="cust_003",
        name="Elena Rodriguez",
        phone="+14155551003",
        email="elena.r@example.com",
        subscription_tier="enterprise",
        monthly_amount=249.0,
        months_active=36,
        ltv=None,
        churn_status="active",
        allowed_discount=None,
        created_at="",
        updated_at="",
    ),
]


async def main() -> None:
    init_db(settings.database_path)
    for customer in SEED_DATA:
        if customer.ltv is None:
            customer.ltv = customer.monthly_amount * customer.months_active
        await repo.upsert(customer)
        print(f"Seeded {customer.id} | LTV=${customer.ltv:,.2f}")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
