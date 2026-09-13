"""Seed the account roster. Run once:  python seed_customers.py"""
from churn_rescue.db import Customer, init_db, upsert_customer, DEFAULT_DB_PATH

SEED_ACCOUNTS = [
    Customer(
        customer_id="acct_northwind",
        company_name="Northwind Traders",
        contact_name="Sarah Chen",
        phone="+14155550101",
        plan="Enterprise",
        mrr=4200.0,
        churn_risk_score=0.92,
        ltv=148000.0,
        contract_end_date="2026-10-01",
        competitor_threat="Salesforce",
    ),
    Customer(
        customer_id="acct_helvetia",
        company_name="Helvetia Logistics",
        contact_name="Marcus Weber",
        phone="+14155550102",
        plan="Enterprise",
        mrr=3100.0,
        churn_risk_score=0.87,
        ltv=96000.0,
        contract_end_date="2026-09-30",
        competitor_threat="HubSpot",
    ),
    Customer(
        customer_id="acct_bluefin",
        company_name="Bluefin Analytics",
        contact_name="Priya Nair",
        phone="+14155550103",
        plan="Scale",
        mrr=1850.0,
        churn_risk_score=0.81,
        ltv=54000.0,
        contract_end_date="2026-11-15",
        competitor_threat="Linear",
    ),
    Customer(
        customer_id="acct_cobalt",
        company_name="Cobalt Media Group",
        contact_name="Diego Alvarez",
        phone="+14155550104",
        plan="Scale",
        mrr=1600.0,
        churn_risk_score=0.78,
        ltv=41000.0,
        contract_end_date="2026-10-20",
        competitor_threat="Acme",
    ),
    Customer(
        customer_id="acct_ferrous",
        company_name="Ferrous Manufacturing",
        contact_name="Hannah Okafor",
        phone="+14155550105",
        plan="Enterprise",
        mrr=2750.0,
        churn_risk_score=0.74,
        ltv=72000.0,
        contract_end_date="2026-12-01",
        competitor_threat="Salesforce",
    ),
    Customer(
        customer_id="acct_lumen",
        company_name="Lumen Health Systems",
        contact_name="Dr. Alan Reyes",
        phone="+14155550106",
        plan="Scale",
        mrr=1400.0,
        churn_risk_score=0.69,
        ltv=33500.0,
        contract_end_date="2027-01-10",
        competitor_threat="HubSpot",
    ),
    Customer(
        customer_id="acct_kestrel",
        company_name="Kestrel Fintech",
        contact_name="Mia Tanaka",
        phone="+14155550107",
        plan="Growth",
        mrr=950.0,
        churn_risk_score=0.63,
        ltv=19000.0,
        contract_end_date="2026-10-05",
        competitor_threat="Linear",
    ),
    Customer(
        customer_id="acct_redwood",
        company_name="Redwood Retail Co.",
        contact_name="Tom Beckett",
        phone="+14155550108",
        plan="Growth",
        mrr=720.0,
        churn_risk_score=0.55,
        ltv=12500.0,
        contract_end_date="2026-11-30",
        competitor_threat="Acme",
    ),
    Customer(
        customer_id="acct_solace",
        company_name="Solace Legal Partners",
        contact_name="Grace Lindqvist",
        phone="+14155550109",
        plan="Growth",
        mrr=610.0,
        churn_risk_score=0.47,
        ltv=9800.0,
        contract_end_date="2027-02-14",
        competitor_threat=None,
    ),
    Customer(
        customer_id="acct_orion",
        company_name="Orion Edu Labs",
        contact_name="Ben Whitfield",
        phone="+14155550110",
        plan="Starter",
        mrr=240.0,
        churn_risk_score=0.38,
        ltv=4300.0,
        contract_end_date="2027-03-01",
        competitor_threat="HubSpot",
    ),
]


def main() -> None:
    init_db(DEFAULT_DB_PATH)
    for account in SEED_ACCOUNTS:
        upsert_customer(account, DEFAULT_DB_PATH)
        threat = account.competitor_threat or "none"
        print(
            f"Seeded {account.company_name:<28} risk={account.churn_risk_score:.2f} "
            f"LTV=${account.ltv:>9,.0f} threat={threat}"
        )
    print(f"\n{len(SEED_ACCOUNTS)} accounts -> {DEFAULT_DB_PATH}")


if __name__ == "__main__":
    main()
