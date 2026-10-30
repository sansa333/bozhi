"""Credential-free synthetic demonstration. Explicit replay, never advertised as real model output."""
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path

from shipping_agent.config import Settings
from shipping_agent.engine import Engine, ReplayExtractor
from shipping_agent.models import Evidence, ExtractedLine, Extraction
from shipping_agent.parsers import digest, write_json
from shipping_agent.rules import normalize_charge


def example(reference: str, amend: bool):
    text = (f"Invoice {reference}\nDate 2026-10-24\nIssuer Seller\nRecipient Buyer\n"
            "Buyer pays Seller\nHandling Fee USD 40.00 PP\nTOTAL USD 40.00\n")
    instruction = "Please update amount to USD 40.00."
    if amend:
        text += instruction
    message = EmailMessage()
    message["Subject"] = "SYNTHETIC DEMO " + reference
    message.set_content(text)
    raw = message.as_bytes()
    ex = Extraction(
        document_info={"document_type": "Invoice", "reference_no": reference, "document_date": "2026-10-24"},
        transaction_party={"issuer": "Seller", "recipient": "Buyer", "payer": "Buyer", "payee": "Seller"},
        totals={"USD": Decimal("40")},
        evidence=[Evidence(path=k, quote=v) for k, v in [
            ("document_info.document_type", "Invoice"), ("document_info.reference_no", reference),
            ("document_info.document_date", "2026-10-24"), ("transaction_party.issuer", "Issuer Seller"),
            ("transaction_party.recipient", "Recipient Buyer"),
            ("transaction_party.payer", "Buyer pays Seller"), ("transaction_party.payee", "Buyer pays Seller"),
            ("totals.USD", "TOTAL USD 40.00")]],
        lines=[ExtractedLine(source_line="row 1", confidence=0.99,
                             charge_details={"original_charge_item": "Handling Fee", "amount": "40",
                                             "currency": "USD", "payment_term": "PP"},
                             evidence=[Evidence(path="charge_details." + p, quote="Handling Fee USD 40.00 PP")
                                       for p in ("original_charge_item", "amount", "currency", "payment_term")],
                             explicit_changes=["charge_details.amount"] if amend else [],
                             change_quote=instruction if amend else None)])
    return raw, ex


def main():
    from shipping_agent.models import Bill
    destination = Path("output/demo")
    settings = Settings(_env_file=None, data_dir=destination / "audit", llm_api_key="",
                        accounting_entity="Buyer", ocr_mode="remote", accounting_aliases=[])
    blobs, snapshots = [], {}
    old = None
    for reference, amend in (("SYNTHETIC-CREATE-1", False), ("SYNTHETIC-UPDATE-1", True)):
        raw, ex = example(reference, amend)
        blobs.append((reference + ".eml", raw))
        snapshots[digest(raw)] = ex.model_dump()
        if amend:
            old = Bill(bill_id="synthetic-ledger-1", document_info=ex.document_info,
                       transaction_party=ex.transaction_party,
                       charge_details=normalize_charge(ex.lines[0].charge_details),
                       financial_attributes={"accounting_entity": "Buyer", "posting_type": "AP"})
            old.charge_details.amount = Decimal(30)
    report = Engine(settings, ReplayExtractor(snapshots)).run_blobs(blobs, [old])
    operations = [b.operation for b in report.records]
    assert operations == ["CREATE", "UPDATE"], operations
    write_json(destination / "records.json", [b.model_dump() for b in report.records])
    print(f"SYNTHETIC REPLAY (no model/OCR calls): {operations}; output/demo/records.json")


if __name__ == "__main__":
    main()
