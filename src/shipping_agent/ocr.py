"""Exam OCR adapter. A file URL manifest is required; never publish files implicitly."""
import ipaddress
import json
import socket
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import requests

from .config import Settings


class OCRFailure(RuntimeError):
    pass


def structured_charges(payload: dict) -> tuple[list[dict[str, str]], list[str]]:
    """The observed exam contract: data.list[].chargeList[].chargeItem/amount.

    Currency, payer/payee and signed credit semantics are NOT present in this contract.
    They must come from independently grounded document fields, never from guessed keys.
    """
    rows, issues = [], []
    for item in payload["data"]["list"]:
        for row in item["chargeList"]:
            if not isinstance(row, dict) or not isinstance(row.get("chargeItem"), str):
                issues.append("OCR_CHARGE_SCHEMA_UNSUPPORTED")
                continue
            amount = row.get("amount")
            if isinstance(amount, bool) or not row["chargeItem"].strip():
                issues.append("OCR_CHARGE_SCHEMA_UNSUPPORTED")
                continue
            try:
                number = Decimal(str(amount).replace(",", ""))
                if not number.is_finite():
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                issues.append("OCR_CHARGE_AMOUNT_UNRESOLVED")
                continue
            rows.append({"chargeItem": row["chargeItem"], "amount": str(number)})
    return rows, list(dict.fromkeys(issues))


def validate_public_url(url: str) -> str:
    p = urlparse(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.fragment:
        raise OCRFailure("OCR 文件地址必须是不含用户凭据的公网 HTTPS URL")
    try:
        addresses = {x[4][0] for x in socket.getaddrinfo(p.hostname, p.port or 443)}
    except OSError as exc:
        raise OCRFailure("OCR 文件地址 DNS 解析失败") from exc
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise OCRFailure("OCR 文件地址不能指向本机、私网或保留地址")
    return url


def ocr_text(payload: object) -> str:
    """Accepted response contracts only. Unknown/asynchronous envelopes fail explicitly."""
    if isinstance(payload, str) and payload.strip():
        return payload
    if not isinstance(payload, dict):
        raise OCRFailure("无法识别 OCR 返回格式，需要真实响应样例确认适配")
    if payload.get("success") is False or payload.get("error"):
        raise OCRFailure("OCR 服务返回失败状态")
    if "code" in payload and str(payload["code"]) not in {"0", "200"}:
        raise OCRFailure("OCR 服务返回非成功业务状态")
    for key in ("text", "markdown", "content", "ocr_text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    # Observed 2026-10-30 from the exam endpoint with a public dummy PDF.
    # This API may return domain extraction rather than OCR text. Preserve the original
    # structured payload as the evidence source; do not invent document text.
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("list"), list) and data["list"]:
        items = data["list"]
        if all(isinstance(item, dict) and isinstance(item.get("chargeList"), list) for item in items):
            return "[OCR_STRUCTURED_DATA; NOT ORIGINAL DOCUMENT TEXT]\n" + json.dumps(
                data, ensure_ascii=False, indent=2, allow_nan=False)
    for key in ("data", "result"):
        if key in payload:
            try:
                return ocr_text(payload[key])
            except OCRFailure:
                pass
    pages = payload.get("pages")
    if isinstance(pages, list) and pages:
        return "\n".join(f"[PAGE {i+1}]\n{ocr_text(p)}" for i, p in enumerate(pages))
    raise OCRFailure("OCR 未返回可识别文本（可能异步处理或响应协议不同），请提供实际响应样例")


class RemoteOCR:
    def __init__(self, settings: Settings):
        self.settings = settings

    def recognize(self, file_url: str) -> tuple[str, dict]:
        validate_public_url(file_url)
        body = {"file": file_url, "task": "orderChange", "callBack": "", "taskType": ""}
        headers = {}
        if self.settings.ocr_api_key.get_secret_value():
            headers["Authorization"] = f"Bearer {self.settings.ocr_api_key.get_secret_value()}"
        for attempt in range(self.settings.ocr_attempts):
            try:
                r = requests.post(self.settings.ocr_endpoint, json=body, headers=headers,
                                  timeout=(10, 300), allow_redirects=False)
                if r.status_code in {429, 502, 503, 504} and attempt + 1 < self.settings.ocr_attempts:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                if not 200 <= r.status_code < 300:
                    raise OCRFailure("OCR 服务重定向未被跟随")
                # Store raw response privately for replay; never include signed file URLs in logs.
                data = r.json()
                return ocr_text(data), data
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt + 1 == self.settings.ocr_attempts:
                    raise OCRFailure("OCR 网络超时或连接失败") from exc
                time.sleep(0.5 * (attempt + 1))
            except (requests.HTTPError, ValueError) as exc:
                raise OCRFailure("OCR HTTP 或 JSON 响应错误") from exc
        raise OCRFailure("OCR 调用失败")
