import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any

import pycountry

from .models import Bill, Charge, Extraction, Source

ALIASES = {
    "handling fee": "Handling Charge", "h/d fee": "Handling Charge",
    "h/c": "Handling Charge", "handling charge": "Handling Charge",
    "doc fee": "Documentation Fee", "documentation fee": "Documentation Fee",
    "thc": "Terminal Handling Charge", "terminal handling charge": "Terminal Handling Charge",
    "trucking fee": "Trucking Charge", "trucking charge": "Trucking Charge",
    "customs fee": "Customs Clearance Fee", "customs clearance fee": "Customs Clearance Fee",
    "storage fee": "Storage Charge", "storage charge": "Storage Charge",
}
PAYMENT = {"PP": "PP", "P": "PP", "PREPAID": "PP", "FREIGHT PREPAID": "PP",
           "CC": "CC", "C": "CC", "COLLECT": "CC", "FREIGHT COLLECT": "CC",
           "COD": "COD", "CASH ON DELIVERY": "COD", "PAY IMMEDIATELY": "PAY IMMEDIATELY"}
CURRENCIES = {"US$": "USD", "RMB": "CNY", "人民币": "CNY", "美元": "USD"}


def norm(value: object) -> str:
    return re.sub(r"\s+", " ", "" if value is None else str(value)).strip().casefold()


def normalize_charge(charge: Charge) -> Charge:
    charge = charge.model_copy(deep=True)
    original = charge.original_charge_item or charge.charge_item
    charge.original_charge_item = original
    charge.charge_item = ALIASES.get(norm(original), original)
    if charge.currency:
        charge.currency = CURRENCIES.get(charge.currency.upper(), charge.currency.upper())
    if charge.payment_term:
        charge.payment_term = PAYMENT.get(charge.payment_term.upper(), charge.payment_term.upper())
    return charge


def number_in_quote(value: Decimal, quote: str) -> bool:
    for token in re.findall(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?!\w|\.\d)", quote):
        try:
            if Decimal(token.replace(",", "")) == value:
                return True
        except Exception:
            pass
    return False


def value_in_quote(path: str, value: Any, quote: str) -> bool:
    if isinstance(value, Decimal):
        return number_in_quote(value, quote)
    if path.endswith("document_date"):
        if str(value) in quote:
            return True
        for raw in re.findall(r"\d{1,4}[-/]\d{1,2}[-/]\d{1,4}", quote):
            for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%Y/%m/%d"):
                try:
                    if datetime.strptime(raw, fmt).date().isoformat() == value:
                        return True
                except ValueError:
                    pass
        return False
    if path.endswith("currency"):
        v = str(value).upper()
        return bool(re.search(rf"(?<![A-Z]){re.escape(v)}(?![A-Z])", quote.upper())) or any(
            dest == v and alias in quote for alias, dest in CURRENCIES.items())
    if path.endswith("payment_term"):
        v = str(value).upper()
        return any(dest == v and re.search(rf"(?<!\w){re.escape(raw)}(?!\w)", quote.upper())
                   for raw, dest in PAYMENT.items())
    return norm(value) in norm(quote)


def ground_fields(data: dict, evidence: list, text: str) -> tuple[list[str], list[dict]]:
    issues, accepted = [], []
    by_path = defaultdict(list)
    for e in evidence:
        if e.quote in text:
            by_path[e.path].append(e.quote)
    for group in ("document_info", "transaction_party", "charge_details", "totals", "tax"):
        for key, value in data.get(group, {}).items():
            if value is None or (group == "charge_details" and key == "charge_item"):
                continue
            path = f"{group}.{key}"
            quotes = [q for q in by_path[path] if value_in_quote(path, value, q)]
            if not quotes:
                issues.append(f"UNGROUNDED_FIELD:{path}")
                # Unknown evidence-backed values must be null in output, not fabricated proposals.
                if group not in {"totals", "tax"}:
                    data[group][key] = None
            else:
                q = min(quotes, key=len)
                start = text.find(q)
                accepted.append({"path": path, "quote": q, "start": start, "end": start+len(q)})
    return issues, accepted


def posting_type(parties: dict, entity: str | None, aliases: list[str]) -> str | None:
    if not entity:
        return None
    names = {norm(entity), *(norm(a) for a in aliases)}
    is_payer = bool(parties.get("payer")) and norm(parties["payer"]) in names
    is_payee = bool(parties.get("payee")) and norm(parties["payee"]) in names
    if is_payer == is_payee:
        return None
    return "AP" if is_payer else "AR"


def validate_extraction(ex: Extraction) -> list[str]:
    issues = list(ex.warnings)
    sums: dict[str, Decimal] = defaultdict(Decimal)
    positions = set()
    signatures = set()
    for line in ex.lines:
        c = line.charge_details
        if line.source_line in positions:
            issues.append("DUPLICATE_SOURCE_ROW")
        positions.add(line.source_line)
        signature = (norm(c.original_charge_item), c.amount, norm(c.currency), norm(c.payment_term),
                     c.quantity, c.unit_price)
        if signature in signatures:
            issues.append("IDENTICAL_ROWS_REQUIRE_REVIEW")
        signatures.add(signature)
        if c.amount is not None and c.currency:
            sums[c.currency.upper()] += c.amount
        if c.quantity is not None and c.unit_price is not None and c.amount is not None:
            if abs(c.quantity * c.unit_price-c.amount) > Decimal("0.01"):
                issues.append("QUANTITY_RATE_MISMATCH")
    for currency, total in ex.totals.items():
        if abs(sums.get(currency.upper(), Decimal(0)) + ex.tax.get(currency, Decimal(0))-total) > Decimal("0.01"):
            issues.append(f"TOTAL_MISMATCH:{currency}")
    return issues


def proposals(source: Source, ex: Extraction, entity: str | None, aliases: list[str]) -> list[Bill]:
    output = []
    shared_issues = [*source.issues, *validate_extraction(ex)]
    total_data = {"totals": dict(ex.totals), "tax": dict(ex.tax)}
    total_issues, _ = ground_fields(total_data, ex.evidence, source.text)
    shared_issues += total_issues
    for line in ex.lines:
        raw = {"document_info": ex.document_info.model_dump(),
               "transaction_party": ex.transaction_party.model_dump(),
               "charge_details": line.charge_details.model_dump()}
        issues, evidence = ground_fields(raw, [*ex.evidence, *line.evidence], source.text)
        # The model cannot bypass original-name evidence by populating a standardized name.
        raw["charge_details"]["charge_item"] = None
        bill = Bill(document_info=raw["document_info"], transaction_party=raw["transaction_party"],
                    charge_details=normalize_charge(Charge.model_validate(raw["charge_details"])))
        bill.financial_attributes.accounting_entity = entity
        bill.financial_attributes.posting_type = posting_type(raw["transaction_party"], entity, aliases)
        c = bill.charge_details
        issues += shared_issues
        if not c.charge_item:
            issues.append("MISSING_CHARGE_NAME")
        if c.amount is None:
            issues.append("MISSING_AMOUNT")
        if not c.currency or not pycountry.currencies.get(alpha_3=c.currency):
            issues.append("INVALID_OR_MISSING_CURRENCY")
        if c.payment_term and c.payment_term not in set(PAYMENT.values()):
            issues.append("UNKNOWN_PAYMENT_TERM")
        if line.confidence < 0.85:
            issues.append("LOW_CONFIDENCE")
        if not entity:
            issues.append("ACCOUNTING_ENTITY_REQUIRED")
        if not bill.financial_attributes.posting_type:
            issues.append("AR_AP_DIRECTION_UNRESOLVED")
        if not (bill.document_info.reference_no or bill.document_info.bill_of_lading
                or bill.document_info.booking_no):
            issues.append("MISSING_DOCUMENT_IDENTITY")
        if "credit" in norm(ex.document_info.document_type) or (c.amount is not None and c.amount < 0):
            issues.append("CREDIT_NOTE_ACCOUNTING_POLICY_REQUIRED")
        bill.audit_trail.sources = [source.source_id]
        bill.audit_trail.confidence = line.confidence
        bill.audit_trail.evidence = [{**e, "source_id": source.source_id} for e in evidence]
        bill.audit_trail.ai_reasoning = [f"从 {source.filename} 的 {line.source_line} 提取费用",
                                         "仅保留有原文证据的字段；应收应付按记账主体与付款方向计算"]
        bill.audit_trail.issues = list(dict.fromkeys(issues))
        # Store authorizations in transient attributes used by reconciliation, not business JSON.
        output.append(bill)
    return output


def same(a: Any, b: Any) -> bool:
    if isinstance(a, (Decimal, int, float)) and isinstance(b, (Decimal, int, float)):
        return Decimal(str(a)) == Decimal(str(b))
    return norm(a) == norm(b)


def match_bill(new: Bill, existing: list[Bill]) -> tuple[Bill | None, str | None]:
    d, c, p = new.document_info, new.charge_details, new.transaction_party
    candidates = [b for b in existing if same(b.charge_details.charge_item, c.charge_item)]
    if not c.charge_item:
        return None, None
    tiers = [
        lambda b: d.reference_no and same(d.reference_no, b.document_info.reference_no),
        lambda b: d.bill_of_lading and c.currency and same(d.bill_of_lading, b.document_info.bill_of_lading)
                  and same(c.currency, b.charge_details.currency),
        lambda b: d.booking_no and c.currency and same(d.booking_no, b.document_info.booking_no)
                  and same(c.currency, b.charge_details.currency),
        lambda b: (d.container_no and d.document_date and p.issuer and p.recipient and c.amount is not None
                   and same(d.container_no, b.document_info.container_no)
                   and same(d.document_date, b.document_info.document_date)
                   and same(p.issuer, b.transaction_party.issuer)
                   and same(p.recipient, b.transaction_party.recipient)
                   and c.amount == b.charge_details.amount),
    ]
    for i, test in enumerate(tiers):
        found = [b for b in candidates if test(b)]
        if not found:
            continue
        if c.line_id:
            exact = [b for b in found if same(c.line_id, b.charge_details.line_id)]
            if exact:
                found = exact
        if len(found) > 1:
            return None, "AMBIGUOUS_BILL_MATCH"
        old = found[0]
        for x, y in ((new.financial_attributes.accounting_entity, old.financial_attributes.accounting_entity),
                     (p.issuer, old.transaction_party.issuer), (p.recipient, old.transaction_party.recipient)):
            if x and y and not same(x, y):
                return None, "MATCH_IDENTITY_CONFLICT"
        return old, f"MATCH_TIER_{i+1}"
    return None, None


UPDATABLE = {f"{group}.{name}" for group, model in
             (("document_info", Bill.model_fields["document_info"].annotation),
              ("transaction_party", Bill.model_fields["transaction_party"].annotation),
              ("charge_details", Charge)) for name in model.model_fields
             if name not in {"line_id"}}

CHANGE_TERMS = {
    "amount": r"amount|金额|费用|收费",
    "currency": r"currency|币种|货币",
    "payment_term": r"payment\s*(?:term|method)|付款方式|付费方式|预付|到付",
    "quantity": r"quantity|数量",
    "unit_price": r"unit\s*(?:price|rate)|单价",
    "original_charge_item": r"charge\s*(?:item|name)|费用名称|费目",
    "charge_item": r"charge\s*(?:item|name)|费用名称|费目",
    "document_type": r"document\s*type|单据类型",
    "reference_no": r"reference|invoice\s*(?:no|number)|单据号|账单号",
    "document_date": r"document\s*date|invoice\s*date|单据日期|账单日期",
    "bill_of_lading": r"bill\s*of\s*lading|b/l|提单",
    "booking_no": r"booking|订舱",
    "container_no": r"container|箱号",
    "issuer": r"issuer|签发方", "recipient": r"recipient|接收方",
    "payer": r"payer|付款方", "payee": r"payee|收款方",
}


def reconcile(new: Bill, old: Bill | None, match: str | None, line, source: Source) -> Bill:
    if old is None:
        if match:
            new.audit_trail.issues.append(match)
        return finalize(new)
    original = old.model_dump()
    incoming = new.model_dump()
    result = old.model_copy(deep=True)
    result.audit_trail = new.audit_trail.model_copy(deep=True)
    result.operation = "NO_CHANGE"
    result.audit_trail.matched_bill_id = old.bill_id
    result.audit_trail.ai_reasoning.append(f"按规则 {match} 匹配已有账单；未获授权字段保留原值")
    evidence_paths = {e["path"] for e in new.audit_trail.evidence}
    # An ordinary invoice is evidence of a discrepancy, not authorization to overwrite it.
    authorize = bool(line.change_quote and line.change_quote in source.text and re.search(
        r"update|amend|revise|change|correct|调整|修改|更新|更正|改为", line.change_quote, re.I))
    for group in ("document_info", "transaction_party", "charge_details"):
        for key, value in incoming[group].items():
            if value is None or same(value, original[group].get(key)):
                continue
            path = f"{group}.{key}"
            # original and standardized names are a single semantic field.
            ep = "charge_details.original_charge_item" if path == "charge_details.charge_item" else path
            permitted = path in line.explicit_changes
            if path == "charge_details.charge_item":
                permitted = permitted or "charge_details.original_charge_item" in line.explicit_changes
            # Amendment authority must mention this field AND its replacement value.
            # A model labeling every extracted field as an update cannot broaden an amount-only instruction.
            field_authorized = bool(authorize and re.search(CHANGE_TERMS.get(key, r"(?!)"),
                                                           line.change_quote, re.IGNORECASE)
                                    and (value_in_quote(ep, value, line.change_quote)
                                         or path == "charge_details.charge_item"))
            if path in UPDATABLE and permitted and field_authorized and ep in evidence_paths:
                setattr(getattr(result, group), key, value)
                result.audit_trail.changed_fields.append(path)
                result.audit_trail.previous_values[path] = original[group].get(key)
            elif path not in {"charge_details.original_charge_item", "charge_details.charge_item"}:
                result.audit_trail.issues.append(f"UNAUTHORIZED_DIFFERENCE:{path}")
    if line.explicit_changes and not authorize:
        result.audit_trail.issues.append("INVALID_CHANGE_AUTHORIZATION")
    unsupported_changes = set(line.explicit_changes) - UPDATABLE
    if unsupported_changes:
        result.audit_trail.issues.append("UNSUPPORTED_CHANGE_PATH")
    if result.audit_trail.changed_fields:
        result.operation = "UPDATE"
    # Required fields absent in a delta may inherit a valid old value. This does not excuse
    # fabricated values (UNGROUNDED_FIELD) or failures in OCR/evidence validation.
    inherited_valid = set()
    if result.charge_details.amount is not None:
        inherited_valid.add("MISSING_AMOUNT")
    if result.charge_details.currency and pycountry.currencies.get(alpha_3=result.charge_details.currency):
        inherited_valid.add("INVALID_OR_MISSING_CURRENCY")
    if result.document_info.reference_no or result.document_info.bill_of_lading or result.document_info.booking_no:
        inherited_valid.add("MISSING_DOCUMENT_IDENTITY")
    if (new.financial_attributes.accounting_entity and
            same(new.financial_attributes.accounting_entity, old.financial_attributes.accounting_entity)
            and old.financial_attributes.posting_type):
        inherited_valid.add("AR_AP_DIRECTION_UNRESOLVED")
    result.audit_trail.issues = [i for i in result.audit_trail.issues if i not in inherited_valid]
    charge = result.charge_details
    if charge.amount is not None and charge.quantity is not None and charge.unit_price is not None:
        if abs(charge.amount-charge.quantity*charge.unit_price) > Decimal("0.01"):
            result.audit_trail.issues.append("MERGED_QUANTITY_RATE_MISMATCH")
    # Re-derive financial direction only after permitted merges; never inherit contradictory AR/AP.
    if new.financial_attributes.posting_type and (
        not old.financial_attributes.posting_type
        or new.financial_attributes.posting_type != old.financial_attributes.posting_type):
        result.audit_trail.issues.append("POSTING_TYPE_REVIEW_REQUIRED")
    return finalize(result)


def finalize(bill: Bill) -> Bill:
    bill.audit_trail.issues = list(dict.fromkeys(bill.audit_trail.issues))
    if bill.operation != "PENDING_REVIEW":
        bill.audit_trail.proposed_operation = bill.operation
    if bill.audit_trail.issues:
        bill.operation = "PENDING_REVIEW"
    return bill
