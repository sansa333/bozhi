from collections import Counter
from decimal import Decimal
from pathlib import Path

from shipping_agent.engine import Engine, ReplayExtractor, load_existing
from shipping_agent.parsers import read_json


def test_repository_walkthrough_replays_without_services_and_retains_evidence(settings):
    folder = Path(__file__).resolve().parents[1] / "examples/walkthrough"
    snapshots = read_json(folder / "extractions.json")
    report = Engine(settings, ReplayExtractor(snapshots)).run(
        [folder / name for name in ("01-new-invoice.eml", "02-update-instruction.eml",
                                   "03-currency-to-confirm.eml")],
        load_existing(folder / "existing-bills.json"), entity="Demo Trading Ltd")
    assert report.metadata["extraction_mode"] == "replay"
    assert Counter(r.operation for r in report.records) == {"CREATE": 2, "UPDATE": 1, "PENDING_REVIEW": 1}
    update = next(r for r in report.records if r.operation == "UPDATE")
    assert update.charge_details.amount == Decimal(40)
    assert update.audit_trail.previous_values["charge_details.amount"] == Decimal(30)
    assert update.audit_trail.changed_fields == ["charge_details.amount"]
    pending = next(r for r in report.records if r.operation == "PENDING_REVIEW")
    assert pending.charge_details.amount == Decimal(50)
    assert pending.charge_details.currency is None
    sources = {s.source_id: s for s in report.sources}
    for record in report.records:
        for evidence in record.audit_trail.evidence:
            source = sources[evidence["source_id"]]
            assert source.text[evidence["start"]:evidence["end"]] == evidence["quote"]
