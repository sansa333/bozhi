from decimal import Decimal

import pytest
from langchain_core.exceptions import OutputParserException
from langchain_core.prompts import ChatPromptTemplate
from pydantic import ValidationError

from shipping_agent.extractor import (
    SYSTEM_PROMPT,
    DecimalJSONOutputParser,
    supplement_explicit_amount_columns,
    supplement_explicit_totals,
)
from shipping_agent.models import Evidence, Extraction


def test_prompt_has_only_declared_variables():
    p = ChatPromptTemplate.from_messages([("system", SYSTEM_PROMPT + "\nSchema:\n{schema}"),
                                         ("human", "{source_id} {feedback} {text}")])
    assert set(p.input_variables) == {"schema", "source_id", "feedback", "text"}
    p.invoke({"schema": "{}", "source_id": "s1", "feedback": "", "text": "test"})


def test_no_charge_glm_empty_root_fields_are_compatible():
    ex = Extraction.model_validate({"lines": [], "explicit_changes": [], "change_quote": None,
                                    "no_charge_reason": "B/L without amounts"})
    assert ex.lines == []


def test_nonempty_root_changes_are_not_ignored():
    with pytest.raises(ValidationError):
        Extraction.model_validate({"lines": [], "explicit_changes": ["charge_details.amount"]})


def test_evidence_path_canonicalization_never_rewrites_quotes():
    e = Evidence(path="lines[0].charge_details.amount", quote="USD 40.00")
    assert e.path == "charge_details.amount"
    assert e.quote == "USD 40.00"


def test_missing_total_evidence_is_supplemented_only_from_unique_explicit_line(sample):
    source, ex = sample
    ex.evidence = [e for e in ex.evidence if e.path != "totals.USD"]
    fixed, paths = supplement_explicit_totals(ex, source)
    assert paths == ["totals.USD"]
    assert fixed.totals == ex.totals
    assert fixed.lines == ex.lines
    assert fixed.evidence[-1].quote == "TOTAL USD 40.00"
    assert not any(e.path == "totals.USD" for e in ex.evidence)


@pytest.mark.parametrize("line", ["SUBTOTAL USD 40.00", "TOTAL CNY 40.00", "TOTAL USD 99.00",
                                  "TOTAL USD 40.00\nTOTAL USD 99.00", "previous TOTAL USD 40.00"])
def test_total_supplement_never_uses_ambiguous_or_different_declaration(sample, line):
    source, ex = sample
    source.text = line
    ex.evidence = []
    _, paths = supplement_explicit_totals(ex, source)
    assert paths == []


def test_nested_field_evidence_is_moved_without_changing_quotes_or_business_fields():
    raw = {"document_info": {"reference_no": "INV-1", "evidence": [
        {"path": "reference_no", "quote": "Invoice INV-1"}]},
        "transaction_party": {"payer": "Buyer", "evidence": [
            {"path": "transaction_party.payer", "quote": "Buyer pays Seller"}]}}
    ex = Extraction.model_validate(raw)
    assert ex.document_info.reference_no == "INV-1"
    assert ex.evidence[0].path == "document_info.reference_no"
    assert ex.evidence[0].quote == "Invoice INV-1"
    assert ex.evidence[1].path == "transaction_party.payer"
    assert "evidence" in raw["document_info"]


def test_nested_evidence_with_unknown_field_is_not_silently_accepted():
    with pytest.raises(ValidationError):
        Extraction.model_validate({"document_info": {"evidence": [
            {"path": "not_a_field", "quote": "made up"}]}})


def test_printed_amount_is_not_lost_when_currency_is_ambiguous(sample):
    source, ex = sample
    source.text = "Description | Unit price | Amount\nHandling Fee | 40.00 | $50.00"
    ex.lines[0].charge_details.amount = None
    ex.lines[0].evidence = [Evidence(path="charge_details.amount", quote="$50.00")]
    fixed, paths = supplement_explicit_amount_columns(ex, source)
    assert fixed.lines[0].charge_details.amount == 50
    assert paths == ["lines[0].charge_details.amount"]
    assert ex.lines[0].charge_details.amount is None


@pytest.mark.parametrize("text,quote", [
    ("Description | Unit price\nHandling Fee | $50.00", "$50.00"),
    ("Description | Amount\nHandling Fee | $I00.00", "$I00.00"),
    ("Description | Amount\nHandling Fee | $50.00\nHandling Fee | $50.00", "$50.00"),
    ("Description | Amount\nHandling Fee | $50.00", "$99.00"),
])
def test_amount_supplement_rejects_rates_ambiguous_rows_and_ocr_errors(sample, text, quote):
    source, ex = sample
    source.text = text
    ex.lines[0].charge_details.amount = None
    ex.lines[0].evidence = [Evidence(path="charge_details.amount", quote=quote)]
    fixed, paths = supplement_explicit_amount_columns(ex, source)
    assert paths == []
    assert fixed.lines[0].charge_details.amount is None


def test_model_json_parser_preserves_decimal_precision_before_pydantic():
    result = DecimalJSONOutputParser().invoke('{"amount": 9007199254740993.123456789}')
    assert result["amount"] == Decimal("9007199254740993.123456789")


@pytest.mark.parametrize("value", ['{"amount": 40', '{"amount": NaN}', '{"amount": Infinity}'])
def test_truncated_or_nonfinite_model_json_is_not_silently_repaired(value):
    with pytest.raises(OutputParserException):
        DecimalJSONOutputParser().invoke(value)


def test_json_fence_is_accepted_without_changing_values():
    assert DecimalJSONOutputParser().invoke('```json\n{"amount": 40.01}\n```')["amount"] == Decimal("40.01")
