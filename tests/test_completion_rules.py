from decimal import Decimal

import pytest

from shipping_agent.engine import Engine
from shipping_agent.models import Evidence
from shipping_agent.ocr import structured_charges
from shipping_agent.rules import (
    credit_adjustment,
    ocr_alignment_issues,
    proposals,
    validate_extraction,
    value_in_quote,
)


def test_ocr_and_model_rows_are_compared_with_multiplicity(sample):
    source, ex = sample
    source.ocr_charge_rows = [{"chargeItem": "H/D Fee", "amount": "40.00"}]
    assert ocr_alignment_issues(source, ex) == []
    source.ocr_charge_rows *= 2
    assert "OCR_MODEL_CHARGE_MISMATCH" in ocr_alignment_issues(source, ex)
    source.ocr_charge_rows = []
    assert "OCR_MODEL_CHARGE_MISMATCH" in ocr_alignment_issues(source, ex)


@pytest.mark.parametrize("raw", ["NaN", "O0.00", None, True])
def test_unknown_ocr_amount_is_reviewed_not_guessed(raw):
    rows, issues = structured_charges({"data": {"list": [{"chargeList": [
        {"chargeItem": "Handling Fee", "amount": raw}]}]}})
    assert rows == []
    assert issues


@pytest.mark.parametrize("text", ["Handling Fee USD 1O0.00", "Amount: I00.00", "金额: 1O0.00"])
def test_numeric_character_ambiguity_has_independent_detection(sample, text):
    source, ex = sample
    source.text = text
    assert "OCR_NUMERIC_AMBIGUITY" in ocr_alignment_issues(source, ex)


def test_subtotal_tax_and_total_are_separately_validated(sample):
    source, ex = sample
    source.text += "\nSUBTOTAL USD 40.00\nTAX USD 2.00\nTOTAL USD 42.00"
    ex.subtotals = {"USD": Decimal(40)}
    ex.tax = {"USD": Decimal(2)}
    ex.totals = {"USD": Decimal(42)}
    ex.evidence += [Evidence(path=p, quote=q) for p, q in [
        ("subtotals.USD", "SUBTOTAL USD 40.00"), ("tax.USD", "TAX USD 2.00"),
        ("totals.USD", "TOTAL USD 42.00")]]
    assert proposals(source, ex, "Buyer", [])[0].audit_trail.issues == []
    ex.subtotals["USD"] = Decimal(99)
    assert "SUBTOTAL_MISMATCH:USD" in validate_extraction(ex)


def test_total_cannot_be_grounded_by_another_currency(sample):
    source, ex = sample
    source.text += "\nTOTAL EUR 40.00"
    for evidence in ex.evidence:
        if evidence.path == "totals.USD":
            evidence.quote = "TOTAL EUR 40.00"
    assert "UNGROUNDED_FIELD:totals.USD" in proposals(source, ex, "Buyer", [])[0].audit_trail.issues


def credit_case(sample):
    source, ex = sample
    original = proposals(source, ex, "Buyer", [])[0]
    original.bill_id = "old-invoice"
    source.text += "\nCredit Note CN-1 against original invoice INV-1"
    ex.document_info.document_type = "Credit Note"
    ex.document_info.reference_no = "CN-1"
    ex.document_info.original_reference_no = "INV-1"
    ex.evidence = [e for e in ex.evidence if e.path not in {
        "document_info.document_type", "document_info.reference_no"}]
    ex.evidence += [Evidence(path=p, quote=q) for p, q in [
        ("document_info.document_type", "Credit Note"), ("document_info.reference_no", "CN-1"),
        ("document_info.original_reference_no", "original invoice INV-1")]]
    return source, ex, original


@pytest.mark.parametrize("posting", ["AR", "AP"])
def test_linked_credit_reverses_amount_and_preserves_original_account(sample, posting):
    source, ex, original = credit_case(sample)
    original.financial_attributes.posting_type = posting
    new = proposals(source, ex, "Buyer", [])[0]
    new.bill_id = "credit-1"
    result = credit_adjustment(new, [original], "linked_signed_adjustment")
    assert result.operation == "CREATE"
    assert result.charge_details.amount == Decimal(-40)
    assert result.financial_attributes.posting_type == posting
    assert result.financial_attributes.original_bill_id == original.bill_id
    assert result.audit_trail.accounting_adjustment["source_amount"] == Decimal(40)
    assert original.charge_details.amount == Decimal(40)
    repeat = proposals(source, ex, "Buyer", [])[0]
    assert credit_adjustment(repeat, [original, result], "linked_signed_adjustment").operation == "NO_CHANGE"


def test_credit_requires_policy_unique_original_and_sufficient_balance(sample):
    source, ex, original = credit_case(sample)
    new = proposals(source, ex, "Buyer", [])[0]
    assert credit_adjustment(new.model_copy(deep=True), [original], "review").operation == "PENDING_REVIEW"
    result = credit_adjustment(new.model_copy(deep=True), [], "linked_signed_adjustment")
    assert "CREDIT_ORIGINAL_BILL_NOT_UNIQUE" in result.audit_trail.issues
    original.charge_details.amount = Decimal(30)
    result = credit_adjustment(new, [original], "linked_signed_adjustment")
    assert "CREDIT_EXCEEDS_ORIGINAL_BALANCE" in result.audit_trail.issues


def test_multiple_credits_do_not_overdraw_one_original_in_a_batch(sample):
    source, ex, original = credit_case(sample)
    new = proposals(source, ex, "Buyer", [])[0]
    result = credit_adjustment(new, [original], "linked_signed_adjustment")
    another = result.model_copy(deep=True)
    another.document_info.reference_no = "CN-2"
    another.audit_trail.sources = ["another-source"]
    Engine.cross_source_checks([result, another])
    assert all("MULTIPLE_CREDITS_FOR_ONE_ORIGINAL_BILL" in r.audit_trail.issues for r in (result, another))


def test_ambiguous_numeric_dates_are_not_silently_assumed_to_be_us_dates():
    assert not value_in_quote("document_info.document_date", "2026-10-11", "10/11/2026")
    assert value_in_quote("document_info.document_date", "2026-10-24", "24/10/2026")
    assert value_in_quote("document_info.document_date", "2026-10-24", "10/24/2026")


def test_raster_identifier_confusion_is_null_and_preserves_unresolved_candidate(sample):
    source, ex = sample
    source.media_type = ".image"
    source.text += "\nBill of Lading: AB0CD1234"
    ex.document_info.bill_of_lading = "AB0CD1234"
    ex.evidence.append(Evidence(path="document_info.bill_of_lading", quote="AB0CD1234"))
    result = proposals(source, ex, "Buyer", [])[0]
    assert result.document_info.bill_of_lading is None
    assert result.audit_trail.unresolved_candidates["document_info.bill_of_lading"] == "AB0CD1234"
    assert "OCR_IDENTIFIER_AMBIGUITY:document_info.bill_of_lading" in result.audit_trail.issues
    source.media_type = ".eml"
    assert proposals(source, ex, "Buyer", [])[0].document_info.bill_of_lading == "AB0CD1234"


def test_identifier_confusion_is_flagged_even_when_model_omits_the_field(sample):
    source, ex = sample
    source.raster_ocr_used = True
    source.text += "\nBooking No: AB0CD1234"
    result = proposals(source, ex, "Buyer", [])[0]
    assert result.document_info.booking_no is None
    assert "OCR_IDENTIFIER_AMBIGUITY:document_info.booking_no" in result.audit_trail.issues


def test_invalid_currency_is_not_retained_as_a_non_iso_output_value(sample):
    source, ex = sample
    source.text = source.text.replace("USD", "ZZZ")
    ex.lines[0].charge_details.currency = "ZZZ"
    for evidence in ex.lines[0].evidence:
        evidence.quote = evidence.quote.replace("USD", "ZZZ")
    result = proposals(source, ex, "Buyer", [])[0]
    assert result.charge_details.currency is None
    assert result.audit_trail.unresolved_candidates["charge_details.currency"] == "ZZZ"
