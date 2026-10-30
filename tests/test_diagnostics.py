from types import SimpleNamespace
from unittest.mock import Mock

from pydantic import SecretStr

from shipping_agent.diagnostics import check_model


def test_diagnostic_does_not_call_model_without_explicit_live(settings, monkeypatch):
    model = Mock()
    monkeypatch.setattr("shipping_agent.diagnostics.LLMExtractor", model)
    settings.llm_api_key = SecretStr("test-only-key")
    result = check_model(settings)
    assert result["status"] == "configured_not_tested"
    model.assert_not_called()
    assert "test-only-key" not in str(result)


def test_missing_key_never_triggers_request(settings, monkeypatch):
    model = Mock()
    monkeypatch.setattr("shipping_agent.diagnostics.LLMExtractor", model)
    assert check_model(settings, live=True)["status"] == "missing_key"
    model.assert_not_called()


def test_success_does_not_claim_paid_balance(settings, monkeypatch):
    settings.llm_api_key = SecretStr("test-only-key")
    model = Mock()
    model.bind.return_value.invoke.return_value = SimpleNamespace(
        content="CONNECTION_OK", response_metadata={"model_name": "test-model"},
        usage_metadata={"input_tokens": 14, "output_tokens": 4, "total_tokens": 18})
    monkeypatch.setattr("shipping_agent.diagnostics.LLMExtractor", lambda s: SimpleNamespace(model=model))
    result = check_model(settings, live=True)
    assert result["status"] == "call_succeeded"
    assert result["paid_balance_verified"] is False
    assert result["usage"]["total_tokens"] == 18


def test_balance_error_is_distinguished_from_generic_rate_limit(settings, monkeypatch):
    settings.llm_api_key = SecretStr("test-only-key")

    class Failure(Exception):
        status_code = 429
        body = {"error": {"code": "1113"}}

    model = Mock()
    model.bind.return_value.invoke.side_effect = Failure()
    monkeypatch.setattr("shipping_agent.diagnostics.LLMExtractor", lambda s: SimpleNamespace(model=model))
    result = check_model(settings, live=True)
    assert result["status"] == "insufficient_balance"
    assert len(result["attempts"]) == 1


def test_network_error_retry_does_not_claim_insufficient_balance(settings, monkeypatch):
    settings.llm_api_key = SecretStr("test-only-key")

    class Failure(Exception):
        status_code = 500
        body = {"error": {"code": "1234"}}

    model = Mock()
    model.bind.return_value.invoke.side_effect = Failure()
    monkeypatch.setattr("shipping_agent.diagnostics.time.sleep", lambda _: None)
    monkeypatch.setattr("shipping_agent.diagnostics.LLMExtractor", lambda s: SimpleNamespace(model=model))
    result = check_model(settings, live=True)
    assert result["status"] == "service_error"
    assert len(result["attempts"]) == 2
    assert result["paid_balance_verified"] is False
