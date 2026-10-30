from unittest.mock import Mock

import pytest
import requests

from shipping_agent.ocr import OCRFailure, RemoteOCR, ocr_text, validate_public_url


@pytest.mark.parametrize("payload", [
    {"text": "bill USD 40"}, {"code": 200, "data": {"markdown": "bill USD 40"}},
    {"result": {"content": "bill USD 40"}}, {"pages": [{"text": "bill USD 40"}]},
])
def test_known_ocr_response_contracts(payload):
    assert "bill USD 40" in ocr_text(payload)


@pytest.mark.parametrize("payload", [{"task_id": "abc"}, {"success": False, "text": "bad"},
                                    {"code": 500, "text": "bad"}, {}, [], {"data": None}])
def test_unknown_async_or_failed_payload_is_not_assumed_success(payload):
    with pytest.raises(OCRFailure):
        ocr_text(payload)


@pytest.mark.parametrize("url", ["http://example.com/a.pdf", "file:///a.pdf",
                                "https://user:password@example.com/a.pdf"])
def test_url_schemes_and_credentials_rejected(url):
    with pytest.raises(OCRFailure):
        validate_public_url(url)


def test_private_ip_and_dns(settings, monkeypatch):
    monkeypatch.setattr("shipping_agent.ocr.socket.getaddrinfo",
                        lambda *args: [(0, 0, 0, "", ("127.0.0.1", 443))])
    with pytest.raises(OCRFailure):
        validate_public_url("https://localhost/a.pdf")


def test_exam_api_request_exact_body_and_retry(settings, monkeypatch):
    monkeypatch.setattr("shipping_agent.ocr.validate_public_url", lambda u: u)
    monkeypatch.setattr("shipping_agent.ocr.time.sleep", lambda _: None)
    response = Mock(status_code=200)
    response.json.return_value = {"data": {"text": "USD 40"}}
    post = Mock(side_effect=[requests.Timeout(), response])
    monkeypatch.setattr("shipping_agent.ocr.requests.post", post)
    assert RemoteOCR(settings).recognize("https://example.com/a.pdf")[0] == "USD 40"
    assert post.call_count == 2
    assert post.call_args.kwargs["json"] == {"file": "https://example.com/a.pdf",
                                             "task": "orderChange", "callBack": "", "taskType": ""}
    assert post.call_args.kwargs["timeout"] == (10, 300)


def test_network_failure_stays_failed(settings, monkeypatch):
    monkeypatch.setattr("shipping_agent.ocr.validate_public_url", lambda u: u)
    monkeypatch.setattr("shipping_agent.ocr.time.sleep", lambda _: None)
    monkeypatch.setattr("shipping_agent.ocr.requests.post", Mock(side_effect=requests.Timeout()))
    with pytest.raises(OCRFailure):
        RemoteOCR(settings).recognize("https://example.com/a.pdf")


def test_observed_exam_structured_response_is_preserved():
    payload = {"success": True, "data": {"orderType": "None", "list": [
        {"fileType": "PAI-COLOADER-NOTICE", "chargeList": [], "mblNumber": ""}]}}
    result = ocr_text(payload)
    assert result.startswith("[OCR_STRUCTURED_DATA; NOT ORIGINAL DOCUMENT TEXT]")
    assert '"chargeList": []' in result


def test_service_business_objects_are_not_arbitrarily_accepted():
    with pytest.raises(OCRFailure):
        ocr_text({"success": True, "data": {"list": [{"something": "unknown"}]}})
