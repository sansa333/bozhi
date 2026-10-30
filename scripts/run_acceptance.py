"""Full-field synthetic acceptance. Live mode calls the specified OCR and configured LLM.

Replay mode verifies source SHA256 and reruns rules over captured parses/extractions;
it does not pretend to make fresh parser, OCR or model measurements.
"""
import argparse
from contextlib import nullcontext
from decimal import Decimal
from pathlib import Path

from shipping_agent.config import Settings
from shipping_agent.engine import Engine, load_existing
from shipping_agent.models import Extraction, RunReport
from shipping_agent.parsers import digest, read_json, write_json
from shipping_agent.rules import credit_adjustment, match_bill, proposals, reconcile

ROOT = Path("examples/acceptance")


def lookup(record, path):
    value = record.model_dump()
    for key in path.split("."):
        value = value[key]
    return value


def equal(actual, expected, path):
    if expected is None or actual is None:
        return actual is None and expected is None
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(equal(actual[k], expected[k], path+"."+k)
                                                       for k in expected)
    if path.startswith("audit_trail.monetary_summary."):
        return Decimal(str(actual)) == Decimal(str(expected))
    if path.split(".")[-1] in {"amount", "quantity", "unit_price"}:
        return Decimal(str(actual)) == Decimal(str(expected))
    return str(actual).strip().casefold() == str(expected).strip().casefold()


def evaluate(report, case):
    remaining = list(report.records)
    differences, checked, matched, rejected = [], 0, 0, 0
    for index, expected in enumerate(case["expected"]):
        if not remaining:
            checked += len(expected)
            differences.append({"row": index, "error": "MISSING_RECORD"})
            continue
        # Choose the best row without relying on model-generated row labels or output order.
        best = max(remaining, key=lambda r: sum(equal(lookup(r, k), v, k) for k, v in expected.items()))
        remaining.remove(best)
        for path, value in expected.items():
            checked += 1
            actual = lookup(best, path)
            if equal(actual, value, path):
                matched += 1
            elif (actual is None and path in case.get("review_nullable_fields", [])
                  and best.operation == "PENDING_REVIEW"
                  and "OCR_IDENTIFIER_AMBIGUITY:"+path in best.audit_trail.issues):
                rejected += 1
            elif (path == "operation" and value == "CREATE" and actual == "PENDING_REVIEW"
                  and any("OCR_IDENTIFIER_AMBIGUITY:"+p in best.audit_trail.issues
                          for p in case.get("review_nullable_fields", []))):
                rejected += 1
            else:
                differences.append({"row": index, "path": path, "expected": value, "actual": actual})
    if remaining:
        differences.append({"error": "EXTRA_RECORDS", "count": len(remaining)})
    issues = {i for r in report.records for i in r.audit_trail.issues}
    for required in case["required_issues"]:
        if required not in issues:
            differences.append({"error": "MISSING_REVIEW_REASON", "issue": required})
    sources = {s.source_id: s for s in report.sources}
    evidence_count = 0
    for record in report.records:
        if not record.audit_trail.evidence:
            differences.append({"error": "MISSING_EVIDENCE"})
        for e in record.audit_trail.evidence:
            evidence_count += 1
            source = sources[e["source_id"]]
            if source.text[e["start"]:e["end"]] != e["quote"]:
                differences.append({"error": "INVALID_EVIDENCE_OFFSET"})
    return {"case": case["name"], "passed": not differences, "fields_checked": checked,
            "fields_matched": matched, "evidence_checked": evidence_count,
            "fields_safely_rejected": rejected,
            "record_count": len(report.records), "differences": differences}


def replay(case, settings, entity):
    report = RunReport.model_validate(read_json(ROOT / "runs" / (case["name"] + ".report.json")))
    snapshots = read_json(ROOT / "runs" / (case["name"] + ".extractions.json"))
    for name in case["files"]:
        path = ROOT / name
        if report.metadata["input_sha256"][path.name] != digest(path.read_bytes()):
            raise ValueError("CAPTURED_INPUT_SHA256_CHANGED")
    existing, records = load_existing(ROOT / case["existing"]), []
    seen = set()
    for source in report.sources:
        if source.sha256 in seen:
            continue
        seen.add(source.sha256)
        ex = Extraction.model_validate(snapshots[source.sha256])
        for line, new in zip(ex.lines, proposals(source, ex, entity, settings.accounting_aliases), strict=True):
            new.bill_id = "bill-" + digest(f"{source.sha256}|{line.source_line}|{entity}".encode())[:24]
            credit = credit_adjustment(new, existing, settings.credit_note_policy)
            if credit is not None:
                records.append(credit)
            else:
                old, method = match_bill(new, existing)
                records.append(reconcile(new, old, method, line, source))
    Engine.cross_source_checks(records)
    report.records = records
    report.metadata.update(extraction_mode="replay", parsing_mode="replay")
    return report


def main():
    parser = argparse.ArgumentParser(description="合成业务全字段验收，真实调用或显式重放")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--live", action="store_true")
    group.add_argument("--replay", action="store_true")
    parser.add_argument("--tunnel", action="store_true", help="为合成样本建立临时文件下载服务")
    parser.add_argument("--cloudflared")
    parser.add_argument("--case", action="append", help="只执行指定场景，可重复")
    parser.add_argument("--file-port", type=int, default=8011)
    parser.add_argument("--out", type=Path, default=Path("output/acceptance-summary.json"))
    args = parser.parse_args()
    manifest = read_json(ROOT / "manifest.json")
    settings = Settings(credit_note_policy=manifest["credit_note_policy"], ocr_mode="remote",
                        data_dir=Path("output/acceptance-private"))
    if args.cloudflared:
        settings.cloudflared_path = args.cloudflared
    if args.tunnel and not args.live:
        parser.error("重放不需要外网隧道")
    if args.tunnel:
        from shipping_agent.tunnel import ocr_tunnel
        context = ocr_tunnel(settings, port=args.file_port)
    else:
        context = nullcontext(settings)
    results = []
    with context:
        for case in manifest["cases"]:
            if args.case and case["name"] not in args.case:
                continue
            print(f"Processing {case['name']} ({'live' if args.live else 'replay'})", flush=True)
            if args.live:
                paths = [ROOT / p for p in case["files"]]
                report = Engine(settings).run(paths, load_existing(ROOT / case["existing"]),
                                              entity=manifest["entity"])
                report.metadata["synthetic_data"] = True
                report.metadata["input_sha256"] = {p.name: digest(p.read_bytes()) for p in paths}
                write_json(ROOT / "runs" / (case["name"]+".report.json"), report.model_dump())
                snapshots = read_json(settings.data_dir / "runs" / report.run_id / "extractions.json")
                write_json(ROOT / "runs" / (case["name"]+".extractions.json"), snapshots)
            else:
                report = replay(case, settings, manifest["entity"])
            result = evaluate(report, case)
            results.append(result)
            print(f"{case['name']}: {'PASS' if result['passed'] else 'FAIL'}; "
                  f"fields {result['fields_matched']}/{result['fields_checked']}; "
                  f"differences {result['differences'][:6]}", flush=True)
            write_json(args.out, {"synthetic_data": True, "mode": "live" if args.live else "replay",
                "completed_cases": len(results), "all_passed": all(r["passed"] for r in results),
                "fields_checked": sum(r["fields_checked"] for r in results),
                "fields_safely_rejected": sum(r["fields_safely_rejected"] for r in results),
                "fields_matched": sum(r["fields_matched"] for r in results), "cases": results})
    if not results or not all(r["passed"] for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
