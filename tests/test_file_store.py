from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from shipping_agent.file_store import OCRFileStore, create_file_app
from shipping_agent.parsers import Parser


def configure(settings):
    settings.ocr_file_base_url = "https://downloads.example.invalid"
    settings.ocr_file_secret = SecretStr("test-secret-" * 5)
    return OCRFileStore(settings)


def test_download_signature_expiry_scope_and_removal(settings, monkeypatch):
    store = configure(settings)
    client = TestClient(create_file_app(settings))
    with store.publish(b"%PDF-1.4 sample", ".pdf") as url:
        target = urlsplit(url)
        route = target.path + "?" + target.query
        response = client.get(route)
        assert response.status_code == 200
        assert response.content == b"%PDF-1.4 sample"
        assert response.headers["cache-control"] == "no-store"
        assert client.get(route.replace("signature=", "signature=wrong")).status_code == 403
        monkeypatch.setattr("shipping_agent.file_store.time.time", lambda: 9999999999)
        assert client.get(route).status_code == 403
        monkeypatch.undo()
    assert client.get(route).status_code == 404
    assert client.get("/").status_code == 404
    assert client.get("/docs").status_code == 404
    assert not list(store.root.iterdir())


def test_concurrent_equal_files_have_independent_download_lifetimes(settings):
    store = configure(settings)
    client = TestClient(create_file_app(settings))
    with store.publish(b"%PDF-1.4 same", ".pdf") as first:
        with store.publish(b"%PDF-1.4 same", ".pdf") as second:
            assert first != second
        assert client.get(urlsplit(first).path + "?" + urlsplit(first).query).status_code == 200


def test_file_store_rejects_weak_secret_and_arbitrary_files(settings):
    with pytest.raises(ValueError):
        OCRFileStore(settings)
    store = configure(settings)
    with pytest.raises(ValueError), store.publish(b"script", ".exe"):
        pass


def test_parser_automatically_supplies_signed_original_and_preserves_ocr_rows(settings, sample, monkeypatch):
    configure(settings)
    original, ex = sample
    client = TestClient(create_file_app(settings))
    payload = {"success": True, "data": {"list": [{"chargeList": [
        {"chargeItem": "Handling Fee", "amount": "40.00"}]}]}}

    def recognize(_, url):
        p = urlsplit(url)
        assert client.get(p.path+"?"+p.query).content == b"%PDF-1.4 example"
        from shipping_agent.ocr import ocr_text
        return ocr_text(payload), payload

    monkeypatch.setattr("shipping_agent.parsers.RemoteOCR.recognize", recognize)
    monkeypatch.setattr(Parser, "local_text", lambda *args: original.text)
    parser = Parser(settings)
    source = parser.parse_bytes(b"%PDF-1.4 example", "bill.pdf")[0]
    assert source.method == "exam-ocr-api+local-evidence"
    assert source.issues == []
    assert source.ocr_charge_rows == [{"chargeItem": "Handling Fee", "amount": "40.00"}]
    assert parser.raw_ocr[source.source_id] == payload
    from shipping_agent.rules import proposals
    assert proposals(source, ex, "Buyer", [])[0].audit_trail.issues == []
