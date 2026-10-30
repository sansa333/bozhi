from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Source(StrictModel):
    source_id: str
    filename: str
    sha256: str
    parent_id: str | None = None
    media_type: str
    text: str = ""
    method: str = ""
    issues: list[str] = Field(default_factory=list)


class DocumentInfo(StrictModel):
    document_type: str | None = None
    reference_no: str | None = None
    document_date: str | None = None
    bill_of_lading: str | None = None
    booking_no: str | None = None
    container_no: str | None = None

    @field_validator("document_date")
    @classmethod
    def iso_date(cls, value):
        from datetime import date
        if value is not None and date.fromisoformat(value).isoformat() != value:
            raise ValueError("document_date must be YYYY-MM-DD")
        return value


class Parties(StrictModel):
    issuer: str | None = None
    recipient: str | None = None
    payer: str | None = None
    payee: str | None = None


class Charge(StrictModel):
    original_charge_item: str | None = None
    charge_item: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    payment_term: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    # Stable line identifier if the source explicitly supplies one. Never invent a business ID.
    line_id: str | None = None

    @field_validator("amount", "quantity", "unit_price", mode="before")
    @classmethod
    def no_bool(cls, value):
        if isinstance(value, bool):
            raise ValueError("boolean is not an amount")
        return value


class Evidence(StrictModel):
    path: str = Field(description="Field path, e.g. charge_details.amount or transaction_party.payer")
    quote: str = Field(min_length=1, description="Exact verbatim substring of the source text")

    @field_validator("path")
    @classmethod
    def canonical_path(cls, value):
        import re
        # Some compatible models prepend a row collection name. Canonicalize only path syntax,
        # never modify the quoted evidence or replace missing source text.
        return re.sub(r"^lines(?:\[\d+\])?\.", "", value)


class ExtractedLine(StrictModel):
    source_line: str = Field(description="Position in source, e.g. page 1 row 2; preserve distinct rows")
    charge_details: Charge
    evidence: list[Evidence]
    explicit_changes: list[str] = Field(default_factory=list, description="Paths explicitly instructed to change")
    change_quote: str | None = Field(None, description="Verbatim instruction authorizing changes")
    confidence: float = Field(ge=0, le=1)


class Extraction(StrictModel):
    document_info: DocumentInfo = Field(default_factory=DocumentInfo)
    transaction_party: Parties = Field(default_factory=Parties)
    # Evidence for every populated document/party field and total, in addition to row evidence.
    evidence: list[Evidence] = Field(default_factory=list)
    totals: dict[str, Decimal] = Field(default_factory=dict, description="Totals keyed by ISO currency")
    tax: dict[str, Decimal] = Field(default_factory=dict)
    lines: list[ExtractedLine] = Field(default_factory=list)
    no_charge_reason: str | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def compatible_evidence_layout(cls, value):
        if not isinstance(value, dict):
            return value
        value = dict(value)
        shared = value.get("evidence", [])
        if isinstance(shared, list):
            shared = list(shared)
            for group, model in (("document_info", DocumentInfo), ("transaction_party", Parties)):
                block = value.get(group)
                if not isinstance(block, dict) or not isinstance(block.get("evidence"), list):
                    continue
                converted = []
                for item in block["evidence"]:
                    if not isinstance(item, dict) or set(item) != {"path", "quote"}:
                        break
                    path = item["path"]
                    if not isinstance(path, str):
                        break
                    local = path.removeprefix(group + ".")
                    if local not in model.model_fields:
                        break
                    converted.append({"path": group + "." + local, "quote": item["quote"]})
                else:
                    value[group] = {k: v for k, v in block.items() if k != "evidence"}
                    shared.extend(converted)
            value["evidence"] = shared
        # Observed GLM no-charge variant. Only remove semantically empty amendment fields
        # on a response that has no lines. Nonempty/misplaced update instructions still fail.
        if value.get("lines") == []:
            if value.get("explicit_changes") == []:
                value.pop("explicit_changes")
            if "change_quote" in value and value["change_quote"] is None:
                value.pop("change_quote")
        return value


class Financial(StrictModel):
    accounting_entity: str | None = None
    posting_type: Literal["AR", "AP"] | None = None


class Audit(StrictModel):
    ai_reasoning: list[str] = Field(default_factory=list, description="Short evidence-based decisions, not private reasoning")
    sources: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    confidence: float = 0
    matched_bill_id: str | None = None
    changed_fields: list[str] = Field(default_factory=list)
    previous_values: dict[str, Any] = Field(default_factory=dict)
    proposed_operation: str | None = None


class Bill(StrictModel):
    bill_id: str | None = None
    operation: Literal["CREATE", "UPDATE", "NO_CHANGE", "PENDING_REVIEW"] = "CREATE"
    document_info: DocumentInfo = Field(default_factory=DocumentInfo)
    transaction_party: Parties = Field(default_factory=Parties)
    charge_details: Charge = Field(default_factory=Charge)
    financial_attributes: Financial = Field(default_factory=Financial)
    audit_trail: Audit = Field(default_factory=Audit)


class RunReport(StrictModel):
    run_id: str
    records: list[Bill]
    sources: list[Source]
    events: list[dict[str, Any]]
    metadata: dict[str, Any]
