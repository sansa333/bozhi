"""Compare a saved real run against a manually inspected, bounded amount reference.

This is not a full accounting ground truth: identity, payer/payee and AR/AP require review.
"""
import argparse
from collections import Counter
from decimal import Decimal
from pathlib import Path

from shipping_agent.parsers import read_json, write_json

EXPECTED = {
    "70dcc3e7d07a0012fc642af256371f25438eed2c7bbe25442b7ffbd193fd34ba":
        [Decimal("65"), Decimal("1310.40"), Decimal("35")],
    "7f0681695a278ea49dc50090bb1bc476b3c73bbc4a3d4862bc31a013d2ad045d": [Decimal("245")],
    "f3ca6432d5597b7254eb805cb02e97e5633a681658fae4967daaa534e349fd93":
        [Decimal("5"), Decimal("21.90")],
    "fad3298ec8fd3da0e092dfbed0a313be28170073f1e01125195a5dfe9b886e58": [Decimal("50")],
    "e85bee3e3c663e985beb6d3f8b1ba33c2bc724376709dc00882278e06809f75a": [],
    "b577030300701c565822321b45178c6837095484795ca3061b70ab880d05f293": [],
}


def evaluate(report: dict) -> dict:
    cases = []
    for source in report["sources"]:
        sha = source["sha256"]
        if sha not in EXPECTED:
            continue
        records = [r for r in report["records"] if source["source_id"] in r["audit_trail"]["sources"]]
        amounts = [Decimal(str(r["charge_details"]["amount"])) for r in records
                   if r["charge_details"]["amount"] is not None]
        expected = Counter(EXPECTED[sha])
        found = Counter(amounts)
        matched = sum((expected & found).values())
        cases.append({"filename": source["filename"], "sha256": sha,
                      "expected_amounts": EXPECTED[sha], "extracted_grounded_amounts": amounts,
                      "amount_multiset_matches": expected == found, "matched_amount_count": matched,
                      "null_amount_records": sum(r["charge_details"]["amount"] is None for r in records),
                      "source_processing_failed": any(any(x.startswith("EXTRACTION_FAILED")
                                                          for x in r["audit_trail"]["issues"])
                                                      for r in records)})
    expected_count = sum(len(EXPECTED[s["sha256"]]) for s in report["sources"] if s["sha256"] in EXPECTED)
    return {"run_id": report["run_id"], "scope": "Manually checked actual line amounts only",
            "not_validated": ["all identity fields", "issuer ambiguity", "accounting entity and AR/AP",
                              "exam OCR on private materials", "generalization beyond these samples"],
            "live_model": report["metadata"]["extraction_mode"] == "live",
            "model": report["metadata"]["model"], "ocr_mode": report["metadata"]["ocr_mode"],
            "expected_nonzero_amount_count": expected_count,
            "matched_nonzero_amount_count": sum(c["matched_amount_count"] for c in cases),
            "all_amount_multisets_match": all(c["amount_multiset_matches"] for c in cases),
            "has_source_failures": any(c["source_processing_failed"] for c in cases),
            "cases": cases}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--out", type=Path, default=Path("output/materials-evaluation.json"))
    args = parser.parse_args()
    result = evaluate(read_json(args.report))
    write_json(args.out, result)
    print(f"Grounded amounts {result['matched_nonzero_amount_count']}/"
          f"{result['expected_nonzero_amount_count']}; "
          f"source failures: {result['has_source_failures']}. Details: {args.out}")
