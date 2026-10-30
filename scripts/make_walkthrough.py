"""Generate shareable, fully synthetic EML inputs for the README walkthrough."""
from email import policy
from email.message import EmailMessage
from pathlib import Path

from shipping_agent.models import Bill
from shipping_agent.parsers import write_json

DESTINATION = Path("examples/walkthrough")
ENTITY = "Demo Trading Ltd"

MESSAGES = [
    ("01-new-invoice.eml", "DEMO-INV-001 | New shipping charges", """SYNTHETIC EXAMPLE - NOT A REAL BILL
Invoice DEMO-INV-001
Document date: 2026-10-30
Issuer: Demo Ocean Logistics
Recipient: Demo Trading Ltd
Payer: Demo Trading Ltd
Payee: Demo Ocean Logistics
Demo Trading Ltd pays Demo Ocean Logistics for the following actual charges.

Description | Amount | Currency | Payment term
Handling Fee | 65.00 | USD | PP
Doc Fee | 35.00 | USD | PP
TOTAL USD 100.00
"""),
    ("02-update-instruction.eml", "DEMO-UPDATE-001 | Update handling amount", """SYNTHETIC EXAMPLE - NOT A REAL BILL
Invoice DEMO-UPDATE-001
Document date: 2026-10-30
Issuer: Demo Ocean Logistics
Recipient: Demo Trading Ltd
Payer: Demo Trading Ltd
Payee: Demo Ocean Logistics
Demo Trading Ltd pays Demo Ocean Logistics for this charge.

Please update the amount of Handling Fee on invoice DEMO-UPDATE-001 to USD 40.00.
Keep all other fields unchanged.
Description | Amount | Currency | Payment term
Handling Fee | 40.00 | USD | PP
TOTAL USD 40.00
"""),
    ("03-currency-to-confirm.eml", "DEMO-REVIEW-001 | Currency requires confirmation", """SYNTHETIC EXAMPLE - NOT A REAL BILL
Invoice DEMO-REVIEW-001
Document date: 2026-10-30
Issuer: Demo Ocean Logistics
Recipient: Demo Trading Ltd
Payer: Demo Trading Ltd
Payee: Demo Ocean Logistics
Demo Trading Ltd pays Demo Ocean Logistics for this actual charge.

Description | Amount
Handling Fee | $50.00
The dollar currency has NOT been specified. Please ask finance to confirm it.
Do not assume this is USD or any other specific dollar currency.
"""),
]


def generate(destination: Path = DESTINATION):
    destination.mkdir(parents=True, exist_ok=True)
    for name, subject, text in MESSAGES:
        msg = EmailMessage(policy=policy.SMTP)
        msg["Subject"] = subject
        msg["From"] = "billing@example.invalid"
        msg["To"] = "finance@example.invalid"
        msg["Date"] = "Fri, 30 Oct 2026 09:00:00 +0800"
        msg.set_content(text)
        (destination / name).write_bytes(msg.as_bytes())
    old = Bill(bill_id="demo-existing-handling-001",
               document_info={"document_type": "Invoice", "reference_no": "DEMO-UPDATE-001",
                              "document_date": "2026-10-30"},
               transaction_party={"issuer": "Demo Ocean Logistics", "recipient": ENTITY,
                                  "payer": ENTITY, "payee": "Demo Ocean Logistics"},
               charge_details={"original_charge_item": "Handling Fee", "charge_item": "Handling Charge",
                               "amount": "30.00", "currency": "USD", "payment_term": "PP"},
               financial_attributes={"accounting_entity": ENTITY, "posting_type": "AP"})
    write_json(destination / "existing-bills.json", [old.model_dump()])
    print(f"Generated {len(MESSAGES)} synthetic EML files and one existing-bill JSON in {destination}")


if __name__ == "__main__":
    generate()
