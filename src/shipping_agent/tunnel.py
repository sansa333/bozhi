"""Explicit opt-in development tunnel for the signed file-only OCR service."""
import os
import queue
import re
import secrets
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager

import requests
import uvicorn
from pydantic import SecretStr

from .config import Settings
from .file_store import create_file_app


@contextmanager
def ocr_tunnel(settings: Settings, port: int = 8011):
    binary = settings.cloudflared_path or shutil.which("cloudflared")
    if not binary:
        raise ValueError("请安装 cloudflared 并设置 CLOUDFLARED_PATH，或自行配置 OCR_FILE_BASE_URL")
    settings.ocr_file_secret = SecretStr(secrets.token_hex(32))
    server = uvicorn.Server(uvicorn.Config(create_file_app(settings), host="127.0.0.1", port=port,
                                         access_log=False, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    process = None
    old = {k: os.environ.get(k) for k in ("OCR_FILE_BASE_URL", "OCR_FILE_SECRET")}
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("OCR 下载服务未能启动，请检查端口")
            time.sleep(.1)
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        process = subprocess.Popen([binary, "tunnel", "--url", f"http://127.0.0.1:{port}",
                                    "--protocol", "http2", "--no-autoupdate"],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace", **flags)
        addresses = queue.Queue()

        def read_output():
            for line in process.stdout:
                match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if match:
                    addresses.put(match[0])

        threading.Thread(target=read_output, daemon=True).start()
        try:
            settings.ocr_file_base_url = addresses.get(timeout=45)
        except queue.Empty:
            raise RuntimeError("临时 OCR 下载地址建立失败，请检查网络") from None
        # A printed hostname can precede DNS propagation and tunnel registration.
        deadline = time.monotonic() + 45
        while True:
            try:
                response = requests.get(settings.ocr_file_base_url, timeout=(3, 5))
                if response.status_code == 404:
                    break
            except requests.RequestException:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("OCR 临时地址尚不能访问，请稍后重试")
            time.sleep(1)
        os.environ["OCR_FILE_BASE_URL"] = settings.ocr_file_base_url
        os.environ["OCR_FILE_SECRET"] = settings.ocr_file_secret.get_secret_value()
        yield settings
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
        server.should_exit = True
        thread.join(timeout=10)
