"""Short-lived, signed, read-only OCR downloads. No application UI is exposed here."""
import hashlib
import hmac
import secrets
import time
from contextlib import contextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from .config import Settings


class OCRFileStore:
    def __init__(self, settings: Settings):
        self.s = settings
        self.root = settings.data_dir / "ocr-files"
        self.secret = settings.ocr_file_secret.get_secret_value()
        if len(self.secret) < 32:
            raise ValueError("OCR_FILE_SECRET must contain at least 32 characters")

    def sign(self, file_id: str, expires: int) -> str:
        return hmac.new(self.secret.encode(), f"{file_id}:{expires}".encode(), hashlib.sha256).hexdigest()

    @contextmanager
    def publish(self, data: bytes, suffix: str):
        if not self.s.ocr_file_base_url.startswith("https://"):
            raise ValueError("OCR_FILE_BASE_URL must be HTTPS")
        if len(data) > self.s.max_file_bytes:
            raise ValueError("FILE_SIZE_LIMIT")
        if suffix not in {".pdf", ".image", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
            raise ValueError("OCR_FILE_TYPE_NOT_ALLOWED")
        self.root.mkdir(parents=True, exist_ok=True)
        # Per-request objects prevent one concurrent OCR call deleting another call's file.
        if suffix == ".image":
            suffix = ".png" if data.startswith(b"\x89PNG") else (
                ".jpg" if data.startswith(b"\xff\xd8\xff") else ".tiff")
        file_id = secrets.token_hex(24) + suffix
        path = self.root / file_id
        path.write_bytes(data)
        expires = int(time.time()) + self.s.ocr_file_ttl_seconds
        try:
            yield (f"{self.s.ocr_file_base_url.rstrip('/')}/files/{file_id}"
                   f"?expires={expires}&signature={self.sign(file_id, expires)}")
        finally:
            path.unlink(missing_ok=True)

    def resolve(self, file_id: str, expires: int, signature: str):
        import re
        if not re.fullmatch(r"[a-f0-9]{48}\.(?:pdf|png|jpg|jpeg|tif|tiff)", file_id):
            raise HTTPException(404)
        remaining = expires - time.time()
        if remaining < 0 or remaining > self.s.ocr_file_ttl_seconds:
            raise HTTPException(403, "Expired download")
        if not hmac.compare_digest(self.sign(file_id, expires), signature):
            raise HTTPException(403, "Invalid download signature")
        path = self.root / file_id
        if not path.is_file():
            raise HTTPException(404)
        return path


def create_file_app(settings: Settings) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    store = OCRFileStore(settings)

    @app.get("/files/{file_id}")
    def download(file_id: str, expires: int, signature: str):
        path = store.resolve(file_id, expires, signature)
        import mimetypes
        return FileResponse(path, media_type=mimetypes.guess_type(file_id)[0] or "application/octet-stream",
                            headers={"Cache-Control": "no-store"})

    return app
