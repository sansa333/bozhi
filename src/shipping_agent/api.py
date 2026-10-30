import asyncio
import csv
import io
import json
import re
import secrets
import time
import uuid
from decimal import Decimal
from pathlib import Path

import simplejson
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from starlette.concurrency import run_in_threadpool

from .config import Settings
from .engine import Engine
from .models import Bill
from .parsers import read_json
from .rules import normalize_charge

app = FastAPI(title="泊知 · 航运单据助手", version="0.1.0")
# Reports persist on disk; active job state belongs to this single-process local workspace.
jobs: dict[str, dict] = {}
tasks: set[asyncio.Task] = set()


def auth(authorization: str | None = Header(None)):
    expected = Settings().api_token.get_secret_value()
    if expected and not secrets.compare_digest(authorization or "", "Bearer " + expected):
        raise HTTPException(401, "请在连接设置中填写有效的访问凭据")


def json_response(data: object):
    return Response(simplejson.dumps(data, ensure_ascii=False, use_decimal=True, allow_nan=False),
                    media_type="application/json")


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "web.html").read_text(encoding="utf-8")


@app.get("/assets/{name}")
def asset(name: str):
    if name not in {"web.css", "web.js"}:
        raise HTTPException(404)
    return FileResponse(Path(__file__).parent / name,
                        media_type="text/css" if name.endswith("css") else "text/javascript")


@app.get("/health")
def health():
    return {"status": "ok", "posting_performed": False}


@app.get("/api/workspace", dependencies=[Depends(auth)])
def workspace():
    s = Settings()
    return {"name": "泊知", "accounting_entity": s.accounting_entity or "",
            "max_file_bytes": s.max_file_bytes, "max_batch_bytes": s.max_batch_bytes,
            "max_documents": s.max_documents, "model_configured": bool(s.llm_api_key.get_secret_value())}


async def prepare(files, settings, existing_json, urls_json):
    try:
        if not files or len(files) > settings.max_documents:
            raise HTTPException(413, "文件数量超过上限，请分批上传")
        try:
            old = json.loads(existing_json, parse_float=Decimal)
            if not isinstance(old, list):
                raise ValueError("existing_json 必须是数组")
            existing = [Bill.model_validate(b) for b in old]
            if any(b.operation == "PENDING_REVIEW" for b in existing):
                raise ValueError("待审核候选不能作为已确认账单导入")
            for i, b in enumerate(existing):
                b.bill_id = b.bill_id or f"existing-api-{i+1}"
                b.charge_details = normalize_charge(b.charge_details)
            if len({b.bill_id for b in existing}) != len(existing):
                raise ValueError("bill_id 重复")
            urls = json.loads(urls_json)
            if not isinstance(urls, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                   for k, v in urls.items()):
                raise ValueError("urls_json 必须是 SHA256 到 URL 的对象")
        except (ValueError, TypeError):
            raise HTTPException(422, "账单或文件访问清单格式不正确，请检查上传的 JSON 文件") from None
        blobs, total = [], 0
        for f in files:
            data = await f.read(settings.max_file_bytes+1)
            total += len(data)
            if len(data) > settings.max_file_bytes or total > settings.max_batch_bytes:
                raise HTTPException(413, "文件太大，请减少文件数量或压缩后重试")
            name = (f.filename or "unnamed").replace("\\", "/").split("/")[-1]
            blobs.append((name, data))
        return blobs, existing, urls
    finally:
        for f in files:
            await f.close()


@app.post("/api/process", dependencies=[Depends(auth)])
async def process(files: list[UploadFile] = File(...), entity: str = Form(""),
                  existing_json: str = Form("[]"), urls_json: str = Form("{}"),
                  ocr_mode: str | None = Form(None)):
    settings = Settings()
    if ocr_mode is not None:
        if ocr_mode not in {"remote", "local"}:
            raise HTTPException(422, "ocr_mode 必须是 remote 或 local")
        settings.ocr_mode = ocr_mode
    blobs, existing, urls = await prepare(files, settings, existing_json, urls_json)
    report = await run_in_threadpool(Engine(settings).run_blobs, blobs, existing, urls, entity)
    return json_response(report.model_dump())


async def execute_job(job_id, settings, blobs, existing, urls, entity):
    loop = asyncio.get_running_loop()

    def update(event):
        def apply():
            jobs[job_id]["progress"] = event
        loop.call_soon_threadsafe(apply)

    try:
        report = await asyncio.to_thread(Engine(settings).run_blobs, blobs, existing, urls, entity, update)
        jobs[job_id].update(status="completed", run_id=report.run_id, finished_at=time.time())
    except Exception as exc:
        jobs[job_id].update(status="failed", error="本次整理未能完成，请稍后重试。",
                            error_type=type(exc).__name__, finished_at=time.time())


@app.post("/api/jobs", status_code=202, dependencies=[Depends(auth)])
async def create_job(files: list[UploadFile] = File(...), entity: str = Form(""),
                     existing_json: str = Form("[]"), urls_json: str = Form("{}")):
    if sum(j["status"] == "running" for j in jobs.values()) >= 2:
        for f in files:
            await f.close()
        raise HTTPException(429, "已有两批单据正在整理，请稍后再试")
    settings = Settings()
    blobs, existing, urls = await prepare(files, settings, existing_json, urls_json)
    # No await between this second check and registration; concurrent uploads share the same cap.
    if sum(j["status"] == "running" for j in jobs.values()) >= 2:
        raise HTTPException(429, "已有两批单据正在整理，请稍后再试")
    for key in list(jobs):
        if jobs[key]["status"] != "running" and time.time()-jobs[key].get("finished_at", 0) > 3600:
            jobs.pop(key)
    job_id = uuid.uuid4().hex
    jobs[job_id] = {"job_id": job_id, "status": "running", "created_at": time.time(),
                    "progress": {"stage": "queued", "file_total": len(blobs)}}
    task = asyncio.create_task(execute_job(job_id, settings, blobs, existing, urls, entity))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return jobs[job_id]


@app.get("/api/jobs/{job_id}", dependencies=[Depends(auth)])
def job_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "任务状态暂时不可用，请到处理记录查看已保存的结果")
    return jobs[job_id]


@app.get("/api/runs", dependencies=[Depends(auth)])
def history():
    folder = Settings().data_dir / "runs"
    reports = sorted(folder.glob("*/report.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]
    items = []
    for path in reports:
        if not re.fullmatch(r"[a-f0-9]{32}", path.parent.name):
            continue
        try:
            report = read_json(path)
            source_names = [s["filename"] for s in report["sources"] if not s.get("parent_id")]
            items.append({"run_id": report["run_id"], "created_at": path.stat().st_mtime,
                          "filenames": source_names, "metadata": report["metadata"]})
        except (ValueError, KeyError, OSError):
            continue
    return json_response({"items": items})


@app.get("/api/runs/{run_id}", dependencies=[Depends(auth)])
def saved_run(run_id: str):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise HTTPException(404, "没有找到这次处理记录")
    path = Settings().data_dir / "runs" / run_id / "report.json"
    if not path.is_file():
        raise HTTPException(404, "没有找到这次处理记录")
    report = read_json(path)
    # Browser numbers use IEEE-754; provide exact display strings independently of export data.
    totals: dict[str, Decimal] = {}
    line_amounts = []
    for record in report["records"]:
        charge = record["charge_details"]
        amount = charge["amount"]
        line_amounts.append(format(Decimal(str(amount)), "f") if amount is not None else None)
        if amount is not None:
            currency = charge["currency"] or "币种待确认"
            totals[currency] = totals.get(currency, Decimal(0)) + Decimal(str(amount))
    report["display_summary"] = {"line_amounts": line_amounts,
                                 "totals": [{"currency": c, "amount": format(v, "f")}
                                            for c, v in totals.items()]}
    return json_response(report)


@app.get("/api/runs/{run_id}/export", dependencies=[Depends(auth)])
def export_run(run_id: str, format: str = "json"):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise HTTPException(404, "没有找到这次处理记录")
    path = Settings().data_dir / "runs" / run_id / "report.json"
    if not path.is_file():
        raise HTTPException(404, "没有找到这次处理记录")
    records = read_json(path)["records"]
    if format == "json":
        return json_response(records)
    if format != "csv":
        raise HTTPException(422, "支持导出 JSON 或 CSV")
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["处理建议", "单据编号", "费用名称", "金额", "币种", "当前记账主体", "应收应付", "待确认原因"])

    def cell(value):
        if value is None:
            return ""
        # Protect text cells from Excel formula evaluation; numeric Decimal values remain numeric.
        if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
            return "'" + value
        return value

    for r in records:
        writer.writerow([cell(v) for v in [r["operation"], r["document_info"]["reference_no"],
                         r["charge_details"]["charge_item"], r["charge_details"]["amount"],
                         r["charge_details"]["currency"], r["financial_attributes"]["accounting_entity"],
                         r["financial_attributes"]["posting_type"], " | ".join(r["audit_trail"]["issues"])]])
    return Response(output.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8")
