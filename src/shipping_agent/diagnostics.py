"""Small, explicit connectivity check. This cannot query paid balance or subscription quota."""
import time
from datetime import datetime, timedelta, timezone

from .config import Settings
from .extractor import LLMExtractor


def provider_code(exc):
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                body = response.json()
            except ValueError:
                body = None
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict) and error.get("code") is not None:
            return str(error["code"])
    return None


def check_model(settings: Settings, live: bool = False) -> dict:
    result = {"checked_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
              "model": settings.llm_model, "base_url": settings.llm_base_url,
              "key_configured": bool(settings.llm_api_key.get_secret_value()),
              "live_requested": live, "paid_balance_verified": False,
              "balance_note": "此检查不查询账户余额、付费套餐或剩余额度。"}
    if not result["key_configured"]:
        return {**result, "ok": False, "status": "missing_key", "message": "请先在本地 .env 配置模型密钥。"}
    if not live:
        return {**result, "ok": True, "status": "configured_not_tested",
                "message": "已检测到配置，尚未联网验证。使用 doctor --live 发起小规模调用。"}
    bounded = settings.model_copy(update={"llm_timeout_seconds": min(settings.llm_timeout_seconds, 30)})
    attempts = []
    for attempt in range(2):
        started = time.monotonic()
        try:
            message = LLMExtractor(bounded).model.bind(max_tokens=64).invoke("Reply with exactly: CONNECTION_OK")
            attempts.append({"attempt": attempt+1, "ok": True,
                             "seconds": round(time.monotonic()-started, 3)})
            return {**result, "ok": True, "status": "call_succeeded", "message": "当前配置的模型调用成功。",
                    "response": message.content, "reported_model": message.response_metadata.get("model_name"),
                    "usage": message.usage_metadata, "attempts": attempts}
        except Exception as exc:
            code = provider_code(exc)
            status = getattr(exc, "status_code", None)
            attempts.append({"attempt": attempt+1, "ok": False, "error_type": type(exc).__name__,
                             "http_status": status, "provider_code": code,
                             "seconds": round(time.monotonic()-started, 3)})
            if attempt == 0 and status in {500, 502, 503, 504}:
                time.sleep(1)
                continue
            kind, explanation = "service_error", "本次请求失败，不能据此认定账户余额不足。"
            if code == "1113":
                kind, explanation = "insufficient_balance", "服务返回账户欠费，请到控制台核查余额。"
            elif code in {"1302", "1305", "1308", "1310", "1313"} or status == 429:
                kind, explanation = "rate_or_usage_limit", "服务返回限流或用量限制，请核查业务错误码和账户限额。"
            elif status in {401, 403}:
                kind, explanation = "authentication_or_permission", "凭据或模型权限验证未通过。"
            elif "Timeout" in type(exc).__name__:
                kind, explanation = "timeout", "本次请求超时，尚不能确定服务是否恢复。"
            return {**result, "ok": False, "status": kind, "message": explanation, "attempts": attempts}
    return {**result, "ok": False, "status": "service_error", "attempts": attempts}
