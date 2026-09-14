# reportlab drags in PIL/_imaging which won't load here, so we just
# write raw pdf by hand -- one letter page, helvetica
from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

from .db import Customer

CONTRACTS_DIR = Path(__file__).resolve().parent.parent / "data" / "contracts"


def _esc(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _stream(lines: list[str]) -> bytes:
    body = "\n".join(lines).encode("latin-1", "replace")
    return b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n" + body + b"\nendstream"


def render_addendum(
    customer: Customer,
    discount_pct: float,
    out_dir: Path = CONTRACTS_DIR,
) -> dict[str, str]:
    # returns {filename, path, url, title, ref}
    out_dir.mkdir(parents=True, exist_ok=True)
    ref = uuid.uuid4().hex[:8].upper()
    fname = f"addendum_{customer.customer_id}_{ref.lower()}.pdf"
    path = out_dir / fname

    new_mrr = customer.mrr * (1 - discount_pct / 100.0)
    today = date.today().isoformat()

    # content stream: BT/ET text blocks, absolute positions (origin bottom-left)
    ops = [
        "0.06 0.44 0.33 rg 0 752 612 68 re f",          # header band
        "1 1 1 rg BT /F2 22 Tf 40 782 Td (CHURN-RESCUE RETENTION ADDENDUM) Tj ET",
        "1 1 1 rg BT /F1 11 Tf 40 762 Td (Subscription Amendment & Retention Credit Notice) Tj ET",
        "0.55 0.55 0.58 rg BT /F1 9 Tf 460 782 Td (REF %s) Tj ET" % ref,
        "0.55 0.55 0.58 rg BT /F1 9 Tf 460 770 Td (%s) Tj ET" % today,
        "0.12 0.12 0.14 rg BT /F2 14 Tf 40 706 Td (Account) Tj ET",
        "0.12 0.12 0.14 rg BT /F1 12 Tf 40 686 Td (%s) Tj ET" % _esc(customer.company_name),
        "0.35 0.35 0.38 rg BT /F1 10 Tf 40 670 Td (Contact: %s  |  Plan: %s  |  Contract end: %s) Tj ET"
        % (_esc(customer.contact_name), _esc(customer.plan), customer.contract_end_date),
        "0.85 0.85 0.87 RG 0.8 w 40 652 m 572 652 l S",  # rule
        "0.12 0.12 0.14 rg BT /F2 14 Tf 40 624 Td (Negotiated Terms) Tj ET",
        "0.12 0.12 0.14 rg BT /F1 12 Tf 40 600 Td (Retention credit: %.0f%% off current plan) Tj ET"
        % discount_pct,
        "0.12 0.12 0.14 rg BT /F1 12 Tf 40 580 Td (MRR before: $%s) Tj ET"
        % f"{customer.mrr:,.2f}",
        "0.06 0.44 0.33 rg BT /F2 13 Tf 40 556 Td (MRR after: $%s) Tj ET"
        % f"{new_mrr:,.2f}",
        "0.35 0.35 0.38 rg BT /F1 10 Tf 40 532 Td (Credit applies monthly through %s.) Tj ET"
        % customer.contract_end_date,
        "0.85 0.85 0.87 RG 0.8 w 40 512 m 572 512 l S",
        "0.35 0.35 0.38 rg BT /F1 9 Tf 40 486 Td (This addendum amends the master subscription agreement for the account above.) Tj ET",
        "0.35 0.35 0.38 rg BT /F1 9 Tf 40 472 Td (Acceptance is effected by completing the linked Stripe checkout session.) Tj ET",
        "0.12 0.12 0.14 rg BT /F1 11 Tf 40 420 Td (Agreed: ______________________   VP of Sales, Retention Desk) Tj ET",
        "0.12 0.12 0.14 rg BT /F1 11 Tf 40 396 Td (Customer: ____________________   %s, %s) Tj ET"
        % (_esc(customer.contact_name), _esc(customer.company_name)),
        "0.75 0.75 0.78 rg BT /F1 8 Tf 40 60 Td (Generated autonomously by Churn-Rescue Agent during live retention call %s) Tj ET"
        % ref,
    ]
    content = _stream(ops)

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        content,
    ]

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n".encode()
    pdf += b"0000000000 65535 f \n"
    for off in offsets:
        pdf += f"{off:010d} 00000 n \n".encode()
    pdf += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()

    path.write_bytes(bytes(pdf))
    return {
        "filename": fname,
        "path": str(path),
        "url": f"/contracts/{fname}",
        "title": f"Subscription Addendum - {customer.company_name}",
        "ref": ref,
    }
