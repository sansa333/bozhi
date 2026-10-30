from decimal import Decimal

import pytest
from pydantic import ValidationError

from shipping_agent.engine import Engine
from shipping_agent.models import Charge, DocumentInfo
from shipping_agent.rules import (
    match_bill,
    normalize_charge,
    posting_type,
    proposals,
    reconcile,
    same,
    validate_extraction,
)


@pytest.mark.parametrize(("raw", "expected"), [
    ("H/D Fee", "Handling Charge"), ("Doc Fee", "Documentation Fee"),
    ("THC", "Terminal Handling Charge"), ("Trucking Fee", "Trucking Charge"),
    ("Customs Fee", "Customs Clearance Fee"), ("Storage Fee", "Storage Charge"),
    ("4 ADD'L DETENTION FREE TIME CHARGE", "4 ADD'L DETENTION FREE TIME CHARGE")])
def test_normalization(raw, expected):
    c = normalize_charge(Charge(original_charge_item=raw))
    assert c.charge_item == expected
    assert c.original_charge_item == raw


@pytest.mark.parametrize(("entity", "aliases", "expected"), [
    ("Buyer", [], "AP"), ("Seller", [], "AR"), ("Third Party", [], None),
    (None, [], None), ("Buyer Ltd", ["Buyer"], "AP")])
def test_direction_is_entity_relative(entity, aliases, expected):
    assert posting_type({"payer": "Buyer", "payee": "Seller"}, entity, aliases) == expected


def test_debit_note_alone_does_not_determine_posting():
    assert posting_type({"issuer": "Seller", "recipient": "Buyer"}, "Buyer", []) is None
    assert posting_type({"payer": "Buyer", "payee": "Buyer"}, "Buyer", []) is None


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", True])
def test_amount_rejects_nonfinite_and_bool(value):
    with pytest.raises(ValidationError):
        Charge(amount=value)


def test_grounded_proposal(sample):
    source, ex = sample
    b = proposals(source, ex, "Buyer", [])[0]
    assert b.audit_trail.issues == []
    assert b.charge_details.amount == Decimal("40.00")
    assert b.financial_attributes.posting_type == "AP"
    for e in b.audit_trail.evidence:
        assert source.text[e["start"]:e["end"]] == e["quote"]


def test_fabricated_amount_is_null_and_reviewed(sample):
    source, ex = sample
    ex.lines[0].charge_details.amount = Decimal(999)
    b = proposals(source, ex, "Buyer", [])[0]
    assert b.charge_details.amount is None
    assert "UNGROUNDED_FIELD:charge_details.amount" in b.audit_trail.issues


def test_total_mismatch(sample):
    _, ex = sample
    ex.totals["USD"] = Decimal(50)
    assert "TOTAL_MISMATCH:USD" in validate_extraction(ex)


def test_same_name_different_rows_are_preserved(sample):
    source, ex = sample
    other = ex.lines[0].model_copy(deep=True)
    other.source_line = "row 2"
    ex.lines.append(other)
    ex.totals["USD"] = Decimal(80)
    assert len(proposals(source, ex, "Buyer", [])) == 2
    assert "DUPLICATE_SOURCE_ROW" not in validate_extraction(ex)
    other.source_line = "row 1"
    assert "DUPLICATE_SOURCE_ROW" in validate_extraction(ex)


def test_quantity_rate_and_mixed_currency(sample):
    _, ex = sample
    ex.lines[0].charge_details.quantity = Decimal(2)
    ex.lines[0].charge_details.unit_price = Decimal(25)
    assert "QUANTITY_RATE_MISMATCH" in validate_extraction(ex)
    ex.lines[0].charge_details.quantity = None
    ex.tax = {"USD": Decimal(2)}
    ex.totals = {"USD": Decimal(42)}
    assert validate_extraction(ex) == []


def old_and_new(sample):
    source, ex = sample
    new = proposals(source, ex, "Buyer", [])[0]
    old = new.model_copy(deep=True)
    old.bill_id = "ledger-1"
    old.charge_details.amount = Decimal(30)
    return source, ex, old, new


def test_amount_only_update_preserves_every_other_field(sample):
    source, ex, old, new = old_and_new(sample)
    before = old.model_dump()
    ex.lines[0].explicit_changes = ["charge_details.amount"]
    ex.lines[0].change_quote = "Please update amount to USD 40.00."
    matched, method = match_bill(new, [old])
    result = reconcile(new, matched, method, ex.lines[0], source)
    assert result.operation == "UPDATE"
    assert result.charge_details.amount == Decimal(40)
    assert result.audit_trail.changed_fields == ["charge_details.amount"]
    for group in ("document_info", "transaction_party", "financial_attributes"):
        assert result.model_dump()[group] == before[group]
    assert old.model_dump() == before  # No mutation of original ledger.


def test_invoice_difference_without_authorization_is_reviewed(sample):
    source, ex, old, new = old_and_new(sample)
    result = reconcile(new, old, "MATCH_TIER_1", ex.lines[0], source)
    assert result.operation == "PENDING_REVIEW"
    assert result.charge_details.amount == Decimal(30)
    assert "UNAUTHORIZED_DIFFERENCE:charge_details.amount" in result.audit_trail.issues


def test_model_cannot_broaden_amount_update_to_currency(sample):
    source, ex, old, new = old_and_new(sample)
    new.charge_details.currency = "CNY"
    ex.lines[0].explicit_changes = ["charge_details.amount", "charge_details.currency"]
    ex.lines[0].change_quote = "Please update amount to USD 40.00."
    result = reconcile(new, old, "MATCH_TIER_1", ex.lines[0], source)
    assert result.charge_details.currency == "USD"
    assert result.operation == "PENDING_REVIEW"


def test_unchanged_invoice_is_no_change(sample):
    source, ex, old, new = old_and_new(sample)
    old.charge_details.amount = new.charge_details.amount
    assert reconcile(new, old, "MATCH_TIER_1", ex.lines[0], source).operation == "NO_CHANGE"


def test_null_never_erases_existing(sample):
    source, ex, old, new = old_and_new(sample)
    new.charge_details.payment_term = None
    old.charge_details.amount = new.charge_details.amount
    result = reconcile(new, old, "MATCH_TIER_1", ex.lines[0], source)
    assert result.charge_details.payment_term == "PP"


@pytest.mark.parametrize("tier", [1, 2, 3, 4])
def test_all_matching_tiers(sample, tier):
    _, _, old, new = old_and_new(sample)
    if tier > 1:
        old.document_info.reference_no = new.document_info.reference_no = None
    if tier == 2:
        old.document_info.bill_of_lading = new.document_info.bill_of_lading = "BL-1"
    if tier == 3:
        old.document_info.booking_no = new.document_info.booking_no = "BK-1"
    if tier == 4:
        old.document_info.container_no = new.document_info.container_no = "ABCD1234567"
        old.charge_details.amount = new.charge_details.amount
    matched, rule = match_bill(new, [old])
    assert matched is old
    assert rule == f"MATCH_TIER_{tier}"


def test_ambiguous_and_identity_collision(sample):
    _, _, old, new = old_and_new(sample)
    assert match_bill(new, [old, old.model_copy(deep=True)])[1] == "AMBIGUOUS_BILL_MATCH"
    old.transaction_party.issuer = "Different Company"
    assert match_bill(new, [old])[1] == "MATCH_IDENTITY_CONFLICT"


def test_cross_source_conflict_and_duplicates(sample):
    source, ex = sample
    left = proposals(source, ex, "Buyer", [])[0]
    right = left.model_copy(deep=True)
    right.audit_trail.sources = ["s2"]
    Engine.cross_source_checks([left, right])
    assert right.operation == "NO_CHANGE"
    right.charge_details.amount = Decimal(50)
    Engine.cross_source_checks([left, right])
    assert all(b.operation == "PENDING_REVIEW" for b in (left, right))


def test_same_source_equal_rows_are_not_collapsed(sample):
    source, ex = sample
    left = proposals(source, ex, "Buyer", [])[0]
    right = left.model_copy(deep=True)
    Engine.cross_source_checks([left, right])
    assert [b.operation for b in (left, right)] == ["CREATE", "CREATE"]


def test_unknown_entity_and_credit_require_review(sample):
    source, ex = sample
    assert "ACCOUNTING_ENTITY_REQUIRED" in proposals(source, ex, None, [])[0].audit_trail.issues
    ex.document_info.document_type = "Credit Note"
    assert "CREDIT_NOTE_ACCOUNTING_POLICY_REQUIRED" in proposals(source, ex, "Buyer", [])[0].audit_trail.issues


def test_invalid_currency_and_low_confidence(sample):
    source, ex = sample
    ex.lines[0].charge_details.currency = "ZZZ"
    ex.lines[0].confidence = 0.5
    issues = proposals(source, ex, "Buyer", [])[0].audit_trail.issues
    assert "LOW_CONFIDENCE" in issues
    assert "INVALID_OR_MISSING_CURRENCY" in issues


def test_merged_update_revalidates_old_quantity_and_rate(sample):
    source, ex, old, new = old_and_new(sample)
    old.charge_details.quantity = Decimal(1)
    old.charge_details.unit_price = Decimal(30)
    ex.lines[0].explicit_changes = ["charge_details.amount"]
    ex.lines[0].change_quote = "Please update amount to USD 40.00."
    result = reconcile(new, old, "MATCH_TIER_1", ex.lines[0], source)
    assert result.operation == "PENDING_REVIEW"
    assert "MERGED_QUANTITY_RATE_MISMATCH" in result.audit_trail.issues


def test_amount_update_with_omitted_currency_inherits_old_currency(sample):
    source, ex, old, new = old_and_new(sample)
    new.charge_details.currency = None
    new.audit_trail.issues.append("INVALID_OR_MISSING_CURRENCY")
    ex.lines[0].explicit_changes = ["charge_details.amount"]
    ex.lines[0].change_quote = "Please update amount to USD 40.00."
    result = reconcile(new, old, "MATCH_TIER_1", ex.lines[0], source)
    assert result.operation == "UPDATE"
    assert result.charge_details.currency == "USD"


def test_bare_dollar_sign_does_not_ground_usd(sample):
    source, ex = sample
    for e in ex.lines[0].evidence:
        if e.path == "charge_details.currency":
            e.quote = "$"
    source.text += "\n$"
    b = proposals(source, ex, "Buyer", [])[0]
    assert b.charge_details.currency is None


@pytest.mark.parametrize("raw", ["tomorrow", "2026-02-30", "10/24/2026", "20261024"])
def test_document_date_must_be_valid_iso(raw):
    with pytest.raises(ValidationError):
        DocumentInfo(document_date=raw)


def test_totals_are_kept_separate_by_currency(sample):
    _, ex = sample
    other = ex.lines[0].model_copy(deep=True)
    other.source_line = "row 2"
    other.charge_details.currency = "CNY"
    other.charge_details.amount = Decimal(100)
    ex.lines.append(other)
    ex.totals["CNY"] = Decimal(100)
    assert validate_extraction(ex) == []
    ex.totals["USD"] = Decimal(140)
    assert "TOTAL_MISMATCH:USD" in validate_extraction(ex)


def test_zero_is_not_missing_and_decimal_scale_does_not_make_a_difference():
    assert not same(Decimal(0), None)
    assert same(Decimal("40.00"), Decimal("40"))
    assert same(Decimal("0.00"), Decimal("0"))


def test_model_standardized_name_cannot_bypass_original_evidence(sample):
    source, ex = sample
    ex.lines[0].charge_details.original_charge_item = None
    ex.lines[0].charge_details.charge_item = "Invented charge"
    result = proposals(source, ex, "Buyer", [])[0]
    assert result.charge_details.charge_item is None
    assert "MISSING_CHARGE_NAME" in result.audit_trail.issues


def test_identical_values_with_different_model_positions_need_review(sample):
    _, ex = sample
    another = ex.lines[0].model_copy(deep=True)
    another.source_line = "different invented position"
    ex.lines.append(another)
    assert "IDENTICAL_ROWS_REQUIRE_REVIEW" in validate_extraction(ex)
