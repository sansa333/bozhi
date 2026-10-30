import json
import time
from decimal import Decimal
from email.message import EmailMessage

from fastapi.testclient import TestClient
from pydantic import SecretStr

from shipping_agent import api
from shipping_agent.engine import Engine, ReplayExtractor, load_existing
from shipping_agent.parsers import digest, read_json, write_json


def test_replay_pipeline_atomic_numeric_json_and_stable_ids(settings, sample):
    source, ex = sample
    m = EmailMessage()
    m.set_content(source.text)
    raw = m.as_bytes()
    replay = ReplayExtractor({digest(raw): ex.model_dump()})
    report = Engine(settings, replay).run_blobs([("invoice.eml", raw)])
    assert report.records[0].operation == "CREATE"
    assert report.metadata["posting_performed"] is False
    assert report.metadata["extraction_mode"] == "replay"
    record_file = settings.data_dir / "runs" / report.run_id / "records.json"
    raw_data = json.loads(record_file.read_text(encoding="utf-8"))
    assert isinstance(raw_data[0]["charge_details"]["amount"], (int, float))
    assert raw_data[0]["charge_details"]["amount"] == 40
    second = Engine(settings, replay).run_blobs([("invoice.eml", raw)])
    assert second.records[0].bill_id == report.records[0].bill_id


def test_partial_failure_never_disappears(settings):
    report = Engine(settings, ReplayExtractor({})).run_blobs([("a.exe", b"bad"), ("a.pdf", b"%PDF-1.4")])
    assert len(report.records) == 2
    assert all(r.operation == "PENDING_REVIEW" for r in report.records)
    assert "OCR_PUBLIC_URL_REQUIRED" in report.records[1].audit_trail.issues


def test_existing_missing_ids_and_duplicates(settings, sample):
    source, ex = sample
    from shipping_agent.rules import proposals
    bill = proposals(source, ex, "Buyer", [])[0]
    file = settings.data_dir / "existing.json"
    write_json(file, [bill.model_dump()])
    assert load_existing(file)[0].bill_id.startswith("existing-")
    assert read_json(file)[0]["charge_details"]["amount"] == 40


def test_api_upload_validation_auth_and_html(settings, monkeypatch):
    monkeypatch.setattr(api, "Settings", lambda: settings)
    client = TestClient(api.app)
    assert client.get("/").status_code == 200
    assert client.get("/health").json()["posting_performed"] is False
    files = {"files": ("bill.exe", b"bad", "application/octet-stream")}
    assert client.post("/api/process", files=files, data={"existing_json": "{}"}).status_code == 422
    result = client.post("/api/process", files=files).json()
    assert result["records"][0]["operation"] == "PENDING_REVIEW"
    settings.api_token = SecretStr("test-secret")
    assert client.post("/api/process", files=files).status_code == 401
    assert client.post("/api/process", files=files,
                       headers={"Authorization": "Bearer test-secret"}).status_code == 200


def test_upload_limit(settings, monkeypatch):
    monkeypatch.setattr(api, "Settings", lambda: settings)
    settings.max_file_bytes = 10
    response = TestClient(api.app).post("/api/process", files={"files": ("a.eml", b"X"*11)})
    assert response.status_code == 413


def test_workspace_and_assets_do_not_expose_keys(settings, monkeypatch):
    monkeypatch.setattr(api, "Settings", lambda: settings)
    settings.llm_api_key = SecretStr("private-model-key")
    client = TestClient(api.app)
    response = client.get("/api/workspace")
    assert response.json()["model_configured"] is True
    assert "private-model-key" not in response.text
    assert client.get("/assets/web.css").status_code == 200
    assert client.get("/assets/web.js").status_code == 200
    assert client.get("/assets/config.py").status_code == 404


def test_background_job_reports_actual_stages_and_persists_history(settings, sample, monkeypatch):
    monkeypatch.setattr(api, "Settings", lambda: settings)
    source, ex = sample
    m = EmailMessage()
    m.set_content(source.text)
    raw = m.as_bytes()
    engine = Engine(settings, ReplayExtractor({digest(raw): ex.model_dump()}))
    monkeypatch.setattr(api, "Engine", lambda _: engine)
    api.jobs.clear()
    with TestClient(api.app) as client:
        response = client.post("/api/jobs", files={"files": ("invoice.eml", raw)})
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        for _ in range(100):
            job = client.get("/api/jobs/"+job_id).json()
            if job["status"] != "running":
                break
            time.sleep(0.01)
        assert job["status"] == "completed"
        assert job["progress"]["stage"] == "completed"
        assert client.get("/api/runs/"+job["run_id"]).json()["records"][0]["operation"] == "CREATE"
        assert client.get("/api/runs").json()["items"][0]["run_id"] == job["run_id"]
        assert client.get("/api/runs/not-a-run-id").status_code == 404
    api.jobs.clear()


def test_background_jobs_use_server_ocr_configuration(settings, monkeypatch):
    monkeypatch.setattr(api, "Settings", lambda: settings)
    settings.ocr_mode = "local"
    observed = []

    class StubEngine:
        def run_blobs(self, blobs, existing, urls, entity, progress):
            observed.append(settings.ocr_mode)
            raise RuntimeError("test failure, do not expose secrets")

    monkeypatch.setattr(api, "Engine", lambda s: StubEngine())
    api.jobs.clear()
    with TestClient(api.app) as client:
        response = client.post("/api/jobs", files={"files": ("invoice.eml", b"body")},
                               data={"ocr_mode": "remote"})
        for _ in range(100):
            job = client.get("/api/jobs/"+response.json()["job_id"]).json()
            if job["status"] != "running":
                break
            time.sleep(0.01)
        assert observed == ["local"]
        assert job["status"] == "failed"
        assert "secret" not in job["error"]
    api.jobs.clear()


def test_exact_decimal_export_and_csv_formula_safety(settings, sample, monkeypatch):
    monkeypatch.setattr(api, "Settings", lambda: settings)
    source, ex = sample
    from shipping_agent.rules import proposals
    bill = proposals(source, ex, "Buyer", [])[0]
    bill.charge_details.amount = Decimal("9007199254740993.01")
    bill.charge_details.charge_item = "=dangerous()"
    run_id = "a"*32
    report = {"run_id": run_id, "records": [bill.model_dump()], "sources": [], "metadata": {}}
    write_json(settings.data_dir / "runs" / run_id / "report.json", report)
    client = TestClient(api.app)
    shown = client.get(f"/api/runs/{run_id}").json()["display_summary"]
    assert shown["line_amounts"] == ["9007199254740993.01"]
    assert shown["totals"][0]["amount"] == "9007199254740993.01"
    exported = client.get(f"/api/runs/{run_id}/export?format=json")
    assert '9007199254740993.01' in exported.text
    csv_response = client.get(f"/api/runs/{run_id}/export?format=csv")
    assert csv_response.content.startswith(b'\xef\xbb\xbf')
    assert "'=dangerous()" in csv_response.text
    assert client.get(f"/api/runs/{run_id}/export?format=html").status_code == 422


def test_history_requires_same_auth_as_processing(settings, monkeypatch):
    monkeypatch.setattr(api, "Settings", lambda: settings)
    settings.api_token = SecretStr("protected")
    client = TestClient(api.app)
    for url in ("/api/workspace", "/api/runs", "/api/jobs/unknown", "/api/runs/"+"a"*32+"/export"):
        assert client.get(url).status_code == 401
