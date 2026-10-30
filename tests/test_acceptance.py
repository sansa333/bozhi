import importlib.util
import json
from pathlib import Path

import pytest
import simplejson
from jsonschema import Draft202012Validator

from shipping_agent.config import Settings
from shipping_agent.models import bill_output_schema
from shipping_agent.office import find_libreoffice, has_word
from shipping_agent.parsers import Parser, read_json

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("acceptance_runner", ROOT / "scripts/run_acceptance.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
manifest = read_json(ROOT / "examples/acceptance/manifest.json")


@pytest.mark.parametrize("case", manifest["cases"], ids=lambda c: c["name"])
def test_real_service_captures_replay_with_exact_fields_or_explicit_safe_review(settings, case, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", ROOT / "examples/acceptance")
    settings.credit_note_policy = manifest["credit_note_policy"]
    report = runner.replay(case, settings, manifest["entity"])
    result = runner.evaluate(report, case)
    assert result["passed"], result["differences"]
    assert report.metadata["extraction_mode"] == "replay"
    assert report.metadata["parsing_mode"] == "replay"
    schema = bill_output_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    records = json.loads(simplejson.dumps([r.model_dump() for r in report.records], use_decimal=True,
                                          allow_nan=False))
    for record in records:
        validator.validate(record)
        value = record["charge_details"]["amount"]
        assert value is None or isinstance(value, (int, float))


@pytest.mark.parametrize(("path", "expected"), [
    ("pdf/invoice.pdf", "SYN-PDF-001"), ("xls-same-name/invoice.xls", "SYN-XLS-001"),
    ("xlsx-update/amendment.xlsx", "Please update"), ("docx-tax/invoice.docx", "SUBTOTAL CNY 100.00"),
    ("doc-credit/credit.doc", "SYN-ORIGINAL-001"),
])
def test_actual_file_formats_parse_without_using_saved_source_text(settings, path, expected):
    settings.ocr_mode = "local"
    if path.endswith(".doc") and not (find_libreoffice(settings) or has_word()):
        pytest.skip("Install LibreOffice or Microsoft Word to run the real legacy DOC test")
    source = Parser(settings).parse_file(ROOT / "examples/acceptance" / path)[0]
    assert expected in source.text
    assert not any(i.startswith("PARSE_FAILED") for i in source.issues)


def test_acceptance_labels_are_synthetic_and_do_not_depend_on_local_credentials():
    assert manifest["synthetic_data"] is True
    assert manifest["entity"] == "Demo Trading Ltd"
    assert not Settings(_env_file=None).llm_api_key.get_secret_value()
