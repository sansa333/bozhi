import hashlib
import re
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Callable, Protocol

from langchain_core.runnables import RunnableLambda

from . import __version__
from .config import Settings
from .extractor import PROMPT_VERSION, LLMExtractor
from .models import Audit, Bill, Extraction, RunReport, Source
from .parsers import Parser, digest, read_json, write_json
from .rules import credit_adjustment, finalize, match_bill, norm, normalize_charge, proposals, reconcile


class Extractor(Protocol):
    def extract(self, source: Source) -> Extraction: ...


class ReplayExtractor:
    """Explicit replay of saved extraction snapshots; no implied new model evaluation."""
    def __init__(self, snapshots: dict[str, dict]):
        self.snapshots = snapshots

    def extract(self, source: Source) -> Extraction:
        if source.sha256 not in self.snapshots:
            raise ValueError("REPLAY_SNAPSHOT_NOT_FOUND")
        return Extraction.model_validate(self.snapshots[source.sha256])


class Engine:
    def __init__(self, settings: Settings, extractor: Extractor | None = None):
        self.s = settings
        self.extractor = extractor

    def run(self, paths: list[Path], existing: list[Bill] | None = None,
            urls: dict[str, str] | None = None, entity: str | None = None) -> RunReport:
        return self.run_blobs([(p.name, p.read_bytes()) if p.stat().st_size <= self.s.max_file_bytes
                               else (p.name, None) for p in paths], existing, urls, entity)

    def run_blobs(self, blobs: list[tuple[str, bytes | None]], existing: list[Bill] | None = None,
                  urls: dict[str, str] | None = None, entity: str | None = None,
                  progress: Callable[[dict], None] | None = None) -> RunReport:
        started = time.monotonic()
        existing = existing or []
        entity = (entity or self.s.accounting_entity or "").strip() or None
        run_id = uuid.uuid4().hex
        folder = self.s.data_dir / "runs" / run_id
        folder.mkdir(parents=True)
        parser = Parser(self.s, urls)
        records, events = [], []
        snapshots = {}
        parsed = set()
        # LangChain stages make runtime boundaries inspectable and replaceable in tests.
        parse_stage = RunnableLambda(lambda pair: parser.parse_bytes(
            pair[1] or b"", pair[0], forced_issue="FILE_SIZE_LIMIT" if pair[1] is None else None))
        def announce(stage: str, **details):
            if progress:
                progress({"stage": stage, **details})

        for file_index, pair in enumerate(blobs):
            announce("reading", filename=pair[0], file_index=file_index+1, file_total=len(blobs))
            if len(parser.sources) >= self.s.max_documents:
                records.append(Bill(operation="PENDING_REVIEW", audit_trail=Audit(
                    issues=["BATCH_DOCUMENT_LIMIT"], ai_reasoning=["批次剩余文件未处理，请拆分批次"])))
                break
            for source in parse_stage.invoke(pair):
                if source.sha256 in parsed and source.text:
                    events.append({"stage": "deduplicate", "source_id": source.source_id,
                                   "reason": "IDENTICAL_SOURCE_SHA256"})
                    for r in records:
                        if any(s.sha256 == source.sha256 and s.source_id in r.audit_trail.sources
                               for s in parser.sources if s.source_id != source.source_id):
                            r.audit_trail.sources.append(source.source_id)
                    continue
                parsed.add(source.sha256)
                if not source.text.strip():
                    records.append(self.failure(source, source.issues or ["EMPTY_DOCUMENT"]))
                    continue
                try:
                    announce("extracting", filename=source.filename, file_index=file_index+1,
                             file_total=len(blobs))
                    if self.extractor is None:
                        self.extractor = LLMExtractor(self.s)
                    ex = RunnableLambda(self.extractor.extract).invoke(source)
                    snapshots[source.sha256] = ex.model_dump()
                    fee_lines = []
                    for line in ex.lines:
                        charge = line.charge_details
                        description = charge.original_charge_item or ""
                        free_context = (charge.amount is None and not re.search(r"free\s+time", description,
                                                                               re.IGNORECASE)
                                        and re.search(r"\bfree\b|\bwaived\b|\bno charge\b|免费|免收", description,
                                                      re.IGNORECASE))
                        if free_context:
                            events.append({"stage": "context", "source_id": source.source_id,
                                           "source_line": line.source_line,
                                           "reason": "NON_MONETARY_FREE_SERVICE_CONTEXT"})
                        else:
                            fee_lines.append(line)
                    ex = ex.model_copy(update={"lines": fee_lines})
                    if not ex.lines:
                        events.append({"stage": "extract", "source_id": source.source_id,
                                       "line_count": 0, "reason": ex.no_charge_reason})
                        if source.issues or not ex.no_charge_reason or ex.warnings:
                            records.append(self.failure(source, source.issues + ex.warnings
                                                        + ["NO_CHARGE_REVIEW_REQUIRED"]))
                        continue
                    announce("checking", filename=source.filename, file_index=file_index+1,
                             file_total=len(blobs))
                    items = proposals(source, ex, entity, self.s.accounting_aliases)
                    for line, new in zip(ex.lines, items, strict=True):
                        # IDs identify stable ingestion rows, not proof that a new bill should be posted.
                        new.bill_id = "bill-" + digest(
                            f"{source.sha256}|{line.source_line}|{entity}".encode())[:24]
                        credit = credit_adjustment(new, existing, self.s.credit_note_policy)
                        if credit is not None:
                            records.append(credit)
                        else:
                            old, match = match_bill(new, existing)
                            records.append(reconcile(new, old, match, line, source))
                except Exception as exc:
                    events.append({"stage": "process", "source_id": source.source_id,
                                   "ok": False, "error_type": type(exc).__name__})
                    records.append(self.failure(source, [*source.issues,
                                                         f"EXTRACTION_FAILED:{type(exc).__name__}"]))
        announce("checking_batch", file_total=len(blobs))
        self.cross_source_checks(records)
        if hasattr(self.extractor, "events"):
            events += self.extractor.events
        report = RunReport(run_id=run_id, records=records, sources=parser.sources, events=events,
                           metadata={"version": __version__, "prompt_version": PROMPT_VERSION,
                                     "model": self.s.llm_model, "ocr_mode": self.s.ocr_mode,
                                     "accounting_entity": entity,
                                     "credit_note_policy": self.s.credit_note_policy,
                                     "extraction_mode": "replay" if isinstance(self.extractor, ReplayExtractor)
                                                        else "live",
                                     "seconds": round(time.monotonic()-started, 3),
                                     "record_count": len(records),
                                     "pending_count": sum(r.operation == "PENDING_REVIEW" for r in records),
                                     "posting_performed": False})
        announce("saving", file_total=len(blobs))
        write_json(folder / "report.json", report.model_dump())
        write_json(folder / "records.json", [r.model_dump() for r in records])
        write_json(folder / "extractions.json", snapshots)
        write_json(folder / "ocr-raw.json", parser.raw_ocr)
        announce("completed", run_id=run_id, record_count=len(records))
        return report

    @staticmethod
    def failure(source: Source, issues: list[str]) -> Bill:
        return Bill(operation="PENDING_REVIEW", audit_trail=Audit(
            sources=[source.source_id], issues=list(dict.fromkeys(issues)),
            ai_reasoning=[f"文件 {source.filename} 未能生成可靠费用记录，需人工确认"], confidence=0))

    @staticmethod
    def cross_source_checks(records: list[Bill]):
        by_identity = defaultdict(list)
        for bill in records:
            d, c = bill.document_info, bill.charge_details
            identity = d.reference_no or d.bill_of_lading or d.booking_no
            if identity and c.charge_item:
                key = (norm(identity), norm(c.charge_item), norm(c.currency),
                       norm(bill.financial_attributes.accounting_entity))
                by_identity[key].append(bill)
        for group in by_identity.values():
            for i, left in enumerate(group):
                for right in group[i+1:]:
                    if set(left.audit_trail.sources) & set(right.audit_trail.sources):
                        continue  # Distinct same-name rows in one document are not duplicates.
                    lc, rc = left.charge_details, right.charge_details
                    line_ids_differ = lc.line_id and rc.line_id and norm(lc.line_id) != norm(rc.line_id)
                    if line_ids_differ:
                        continue
                    # Without cross-document row IDs, equal names cannot safely identify repeated rows.
                    multiple = sum(norm(b.charge_details.charge_item) == norm(lc.charge_item)
                                   and bool(set(b.audit_trail.sources) & set(left.audit_trail.sources))
                                   for b in group) > 1
                    if (lc.amount == rc.amount and not multiple and
                            left.transaction_party == right.transaction_party and
                            left.document_info == right.document_info and
                            lc == rc):
                        right.audit_trail.proposed_operation = "NO_CHANGE"
                        right.audit_trail.ai_reasoning.append("与同批另一来源费用一致，标记重复供核查")
                        if not right.audit_trail.issues:
                            right.operation = "NO_CHANGE"
                    else:
                        for bill in (left, right):
                            bill.audit_trail.issues.append("CROSS_SOURCE_CONFLICT_OR_AMBIGUOUS_ROW")
                            finalize(bill)
        # Multiple amendment documents targeting one ledger row require sequential review.
        targets = defaultdict(list)
        for bill in records:
            if bill.audit_trail.matched_bill_id:
                targets[bill.audit_trail.matched_bill_id].append(bill)
        for group in targets.values():
            if len(group) > 1:
                for bill in group:
                    bill.audit_trail.issues.append("MULTIPLE_PROPOSALS_FOR_ONE_EXISTING_BILL")
                    finalize(bill)
        credits = defaultdict(list)
        for bill in records:
            if bill.financial_attributes.entry_type == "REVERSAL" and bill.operation != "NO_CHANGE":
                credits[bill.financial_attributes.original_bill_id].append(bill)
        for group in credits.values():
            if len(group) > 1:
                for bill in group:
                    bill.audit_trail.issues.append("MULTIPLE_CREDITS_FOR_ONE_ORIGINAL_BILL")
                    finalize(bill)


def load_existing(path: Path | None) -> list[Bill]:
    if path is None:
        return []
    raw = read_json(path)
    if not isinstance(raw, list):
        raise ValueError("已有账单必须是 JSON 数组")
    bills = [Bill.model_validate(item) for item in raw]
    for i, bill in enumerate(bills):
        if bill.operation == "PENDING_REVIEW":
            raise ValueError("待审核候选不能作为已确认账单导入")
        bill.charge_details = normalize_charge(bill.charge_details)
        if bill.bill_id is None:
            bill.bill_id = "existing-" + hashlib.sha256(f"{path.name}|{i}".encode()).hexdigest()[:20]
    if len({b.bill_id for b in bills}) != len(bills):
        raise ValueError("已有账单 bill_id 不得重复")
    return bills
