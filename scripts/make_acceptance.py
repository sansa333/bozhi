"""Create labeled synthetic business cases. No user materials are read or published."""
import io
from email import policy
from email.message import EmailMessage
from pathlib import Path

import pdfplumber
import xlwt
from docx import Document
from docx.shared import Pt
from openpyxl import Workbook

from shipping_agent.config import Settings
from shipping_agent.models import Bill
from shipping_agent.office import convert_office
from shipping_agent.parsers import write_json

ROOT = Path("examples/acceptance")
ENTITY = "Demo Trading Ltd"
SUPPLIER = "Demo Ocean Logistics Ltd"


def text(ref, rows, issuer=SUPPLIER, recipient=ENTITY, payer=ENTITY, payee=SUPPLIER,
         kind="Invoice", extra="", totals=""):
    return (f"SYNTHETIC TEST DATA - NOT A REAL BILL\n{kind}\nReference No: {ref}\n"
            f"Document Date: 2026-10-30\nIssuer: {issuer}\nRecipient: {recipient}\n"
            f"Payer: {payer}\nPayee: {payee}\n{payer} pays {payee}.\n"
            "Bill of Lading: DEMOBL1001\nBooking No: DEMOBK1001\nContainer No: DEMU1234567\n"
            f"{extra}\nDescription | Quantity | Unit price | Amount | Currency | Payment term\n"
            + "\n".join(" | ".join(row) for row in rows) + "\n" + totals + "\n")


def docx_bytes(content):
    doc = Document()
    doc.styles["Normal"].font.size = Pt(10)
    doc.styles["Normal"].paragraph_format.space_after = Pt(0)
    for line in content.splitlines():
        doc.add_paragraph(line)
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


def email_bytes(content, subject, attachments=()):
    message = EmailMessage(policy=policy.SMTP)
    message["Subject"] = subject
    message["From"] = "billing@example.invalid"
    message["To"] = "finance@example.invalid"
    message["Date"] = "Fri, 30 Oct 2026 09:00:00 +0800"
    message.set_content(content)
    for name, data in attachments:
        message.add_attachment(data, maintype="application", subtype="pdf", filename=name)
    return message.as_bytes()


def expected(ref, original, normalized, amount, currency="USD", term="PP", quantity="1",
             unit=None, operation="CREATE", issuer=SUPPLIER, recipient=ENTITY,
             payer=ENTITY, payee=SUPPLIER, posting="AP", kind="Invoice", original_ref=None,
             entry="NORMAL", original_bill=None):
    return {"operation": operation, "document_info.document_type": kind,
            "document_info.reference_no": ref, "document_info.document_date": "2026-10-30",
            "document_info.bill_of_lading": "DEMOBL1001", "document_info.booking_no": "DEMOBK1001",
            "document_info.container_no": "DEMU1234567", "document_info.original_reference_no": original_ref,
            "transaction_party.issuer": issuer, "transaction_party.recipient": recipient,
            "transaction_party.payer": payer, "transaction_party.payee": payee,
            "charge_details.original_charge_item": original, "charge_details.charge_item": normalized,
            "charge_details.amount": amount, "charge_details.currency": currency,
            "charge_details.payment_term": term, "charge_details.quantity": quantity,
            "charge_details.unit_price": unit if unit is not None else amount,
            "financial_attributes.accounting_entity": ENTITY, "financial_attributes.posting_type": posting,
            "financial_attributes.entry_type": entry, "financial_attributes.original_bill_id": original_bill}


def generate():
    settings = Settings()
    ROOT.mkdir(parents=True, exist_ok=True)
    cases = []

    def add(name, files, labels, old=(), required_issues=()):
        folder = ROOT / name
        folder.mkdir(exist_ok=True)
        for filename, data in files.items():
            (folder / filename).write_bytes(data)
        write_json(folder / "existing.json", list(old))
        cases.append({"name": name, "files": [name+"/"+f for f in files],
                      "existing": name+"/existing.json", "expected": labels,
                      "required_issues": list(required_issues)})

    rows = [("Handling Fee", "1", "65.00", "65.00", "USD", "PP"),
            ("Doc Fee", "1", "35.00", "35.00", "USD", "PP")]
    content = text("SYN-PDF-001", rows, totals="TOTAL USD 100.00")
    pdf = convert_office(docx_bytes(content), ".docx", "pdf", settings)
    labels = [expected("SYN-PDF-001", "Handling Fee", "Handling Charge", "65.00"),
              expected("SYN-PDF-001", "Doc Fee", "Documentation Fee", "35.00")]
    add("pdf", {"invoice.pdf": pdf}, labels)
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        image = document.pages[0].to_image(resolution=180).original.convert("RGB")
    for name, fmt in (("png", "PNG"), ("jpg", "JPEG"), ("tiff", "TIFF"), ("scanned-pdf", "PDF")):
        output = io.BytesIO()
        image.save(output, format=fmt, **({"quality": 96} if fmt == "JPEG" else {}))
        add(name, {"invoice." + ("pdf" if fmt == "PDF" else name): output.getvalue()}, labels)
        cases[-1]["review_nullable_fields"] = ["document_info.bill_of_lading", "document_info.booking_no"]
    add("eml-attachment", {"notice.eml": email_bytes("Please process the attached invoice. "
         "This message contains no fees of its own.", "Synthetic invoice attachment", [("invoice.pdf", pdf)])},
        labels)

    ar = text("SYN-AR-001", [("Trucking Fee", "2", "50.00", "100.00", "EUR", "CC")],
              issuer=ENTITY, recipient="Demo Customer Ltd", payer="Demo Customer Ltd", payee=ENTITY,
              totals="TOTAL EUR 100.00")
    add("eml-ar", {"invoice.eml": email_bytes(ar, "Synthetic receivable")},
        [expected("SYN-AR-001", "Trucking Fee", "Trucking Charge", "100.00", "EUR", "CC", "2", "50.00",
                  issuer=ENTITY, recipient="Demo Customer Ltd", payer="Demo Customer Ltd", payee=ENTITY,
                  posting="AR")])

    old = Bill(bill_id="synthetic-old-001", document_info={"document_type": "Invoice",
        "reference_no": "SYN-UPDATE-001", "document_date": "2026-10-30", "bill_of_lading": "DEMOBL1001",
        "booking_no": "DEMOBK1001", "container_no": "DEMU1234567"},
        transaction_party={"issuer": SUPPLIER, "recipient": ENTITY, "payer": ENTITY, "payee": SUPPLIER},
        charge_details={"original_charge_item": "Handling Fee", "charge_item": "Handling Charge",
                        "amount": "30.00", "currency": "USD", "payment_term": "PP"},
        financial_attributes={"accounting_entity": ENTITY, "posting_type": "AP"})
    update = text("SYN-UPDATE-001", [("Handling Fee", "", "", "40.00", "USD", "PP")],
                  extra="Please update the amount of Handling Fee to USD 40.00. Keep all other fields unchanged.",
                  totals="TOTAL USD 40.00")
    workbook = Workbook()
    for line in update.splitlines():
        workbook.active.append(line.split(" | "))
    output = io.BytesIO()
    workbook.save(output)
    add("xlsx-update", {"amendment.xlsx": output.getvalue()},
        [expected("SYN-UPDATE-001", "Handling Fee", "Handling Charge", "40.00", quantity=None,
                  unit=None, operation="UPDATE")], [old.model_dump()])
    cases[-1]["expected"][0]["charge_details.unit_price"] = None

    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet("Invoice")
    same_name = text("SYN-XLS-001", [("Handling Fee", "1", "65.00", "65.00", "USD", "PP"),
                                    ("Handling Fee", "1", "35.00", "35.00", "USD", "PP")],
                     totals="TOTAL USD 100.00")
    for i, line in enumerate(same_name.splitlines()):
        for j, value in enumerate(line.split(" | ")):
            sheet.write(i, j, value)
    output = io.BytesIO()
    workbook.save(output)
    add("xls-same-name", {"invoice.xls": output.getvalue()},
        [expected("SYN-XLS-001", "Handling Fee", "Handling Charge", "65.00"),
         expected("SYN-XLS-001", "Handling Fee", "Handling Charge", "35.00")])

    tax = text("SYN-TAX-001", [("Customs Fee", "2", "50.00", "100.00", "CNY", "COD")],
               totals="SUBTOTAL CNY 100.00\nTAX CNY 5.00\nTOTAL CNY 105.00")
    add("docx-tax", {"invoice.docx": docx_bytes(tax)},
        [expected("SYN-TAX-001", "Customs Fee", "Customs Clearance Fee", "100.00", "CNY", "COD", "2", "50.00")])

    original = old.model_copy(deep=True)
    original.bill_id = "synthetic-original-credit-001"
    original.document_info.reference_no = "SYN-ORIGINAL-001"
    credit = text("SYN-CREDIT-001", [("Handling Fee", "", "", "15.00", "USD", "PP")],
                  payer=SUPPLIER, payee=ENTITY, kind="Credit Note",
                  extra="Original invoice reference: SYN-ORIGINAL-001\nCredit amount offsets the original invoice.",
                  totals="TOTAL USD 15.00")
    label = expected("SYN-CREDIT-001", "Handling Fee", "Handling Charge", "-15.00", quantity=None,
                     payer=SUPPLIER, payee=ENTITY, kind="Credit Note", original_ref="SYN-ORIGINAL-001",
                     entry="REVERSAL", original_bill=original.bill_id)
    label["charge_details.unit_price"] = None
    add("doc-credit", {"credit.doc": convert_office(docx_bytes(credit), ".docx", "doc", settings)},
        [label], [original.model_dump()])

    ambiguous = text("SYN-CURRENCY-001", [("Handling Fee", "1", "50.00", "$50.00", "", "PP")],
                     extra="Currency is unspecified. The dollar symbol alone is ambiguous.")
    add("currency-review", {"invoice.eml": email_bytes(ambiguous, "Unspecified dollar currency")},
        [expected("SYN-CURRENCY-001", "Handling Fee", "Handling Charge", "50.00", currency=None,
                  operation="PENDING_REVIEW")], required_issues=["INVALID_OR_MISSING_CURRENCY"])

    repeated = text("SYN-DUP-001", [("Storage Fee", "1", "24.00", "24.00", "USD", "CC")],
                    totals="TOTAL USD 24.00")
    add("duplicate", {"first.eml": email_bytes(repeated, "First copy"),
                      "second.eml": email_bytes(repeated, "Another copy")},
        [expected("SYN-DUP-001", "Storage Fee", "Storage Charge", "24.00", term="CC"),
         expected("SYN-DUP-001", "Storage Fee", "Storage Charge", "24.00", term="CC", operation="NO_CHANGE")])
    conflict = repeated.replace("24.00", "25.00")
    add("conflict", {"first.eml": email_bytes(repeated, "First amount"),
                     "second.eml": email_bytes(conflict, "Conflicting amount")},
        [expected("SYN-DUP-001", "Storage Fee", "Storage Charge", "24.00", term="CC", operation="PENDING_REVIEW"),
         expected("SYN-DUP-001", "Storage Fee", "Storage Charge", "25.00", term="CC", operation="PENDING_REVIEW")],
        required_issues=["CROSS_SOURCE_CONFLICT_OR_AMBIGUOUS_ROW"])
    for case in cases:
        for label in case["expected"]:
            name, totals, subtotal, tax = case["name"], {"USD": "100.00"}, {}, {}
            if name == "eml-ar":
                totals = {"EUR": "100.00"}
            elif name == "xlsx-update":
                totals = {"USD": "40.00"}
            elif name == "docx-tax":
                totals, subtotal, tax = {"CNY": "105.00"}, {"CNY": "100.00"}, {"CNY": "5.00"}
            elif name == "doc-credit":
                totals = {"USD": "15.00"}
            elif name == "currency-review":
                totals = {}
            elif name == "duplicate":
                totals = {"USD": "24.00"}
            elif name == "conflict":
                totals = {"USD": label["charge_details.amount"]}
            label.update({"audit_trail.monetary_summary.totals": totals,
                          "audit_trail.monetary_summary.subtotals": subtotal,
                          "audit_trail.monetary_summary.tax": tax})
    write_json(ROOT / "manifest.json", {"synthetic_data": True, "entity": ENTITY,
               "credit_note_policy": "linked_signed_adjustment", "cases": cases})
    print(f"Generated {len(cases)} labeled synthetic scenarios")


if __name__ == "__main__":
    generate()
