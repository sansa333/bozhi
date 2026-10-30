from decimal import Decimal

import pytest

from shipping_agent.config import Settings
from shipping_agent.models import Evidence, ExtractedLine, Extraction, Source


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, accounting_entity="Buyer", llm_api_key="")


@pytest.fixture
def sample():
    text = ("Invoice INV-1 date 2026-10-24\nIssuer Seller\nRecipient Buyer\nBuyer pays Seller\n"
            "Handling Fee USD 40.00 PP\nTOTAL USD 40.00\nPlease update amount to USD 40.00.")
    source = Source(source_id="s1", filename="invoice.eml", sha256="abc", media_type=".eml", text=text)
    evidence = [Evidence(path=p, quote=q) for p, q in [
        ("document_info.document_type", "Invoice"),
        ("document_info.reference_no", "INV-1"),
        ("document_info.document_date", "2026-10-24"),
        ("transaction_party.issuer", "Issuer Seller"),
        ("transaction_party.recipient", "Recipient Buyer"),
        ("transaction_party.payer", "Buyer pays Seller"),
        ("transaction_party.payee", "Buyer pays Seller"),
        ("totals.USD", "TOTAL USD 40.00")]]
    row = ExtractedLine(source_line="row 1", confidence=0.98,
                        charge_details={"original_charge_item": "Handling Fee", "amount": "40.00",
                                        "currency": "USD", "payment_term": "PP"},
                        evidence=[Evidence(path="charge_details."+k, quote="Handling Fee USD 40.00 PP")
                                  for k in ("original_charge_item", "amount", "currency", "payment_term")])
    ex = Extraction(document_info={"document_type": "Invoice", "reference_no": "INV-1",
                                   "document_date": "2026-10-24"},
                    transaction_party={"issuer": "Seller", "recipient": "Buyer", "payer": "Buyer",
                                       "payee": "Seller"},
                    evidence=evidence, totals={"USD": Decimal("40.00")}, lines=[row])
    return source, ex
