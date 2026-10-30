import hashlib
import io
import json
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path

from bs4 import BeautifulSoup

from .config import Settings
from .models import Source
from .ocr import OCRFailure, RemoteOCR, structured_charges
from .office import convert_office, find_libreoffice, has_word


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def kind_of(data: bytes, name: str) -> str:
    suffix = Path(name).suffix.lower()
    if data.startswith(b"%PDF-"):
        return ".pdf"
    if data.startswith((b"\x89PNG", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*")):
        return ".image"
    return suffix


class Parser:
    def __init__(self, settings: Settings, urls: dict[str, str] | None = None):
        self.s = settings
        # sha256 keys are unambiguous, including EML attachments; filenames are not accepted as URL keys.
        self.urls = urls or {}
        self.sources: list[Source] = []
        self.raw_ocr: dict[str, object] = {}
        self.total_bytes = 0
        self._ocr = None
        self._raster_ocr_used = False

    def parse_file(self, path: Path) -> list[Source]:
        size = path.stat().st_size
        if size > self.s.max_file_bytes:
            return self.parse_bytes(b"", path.name, forced_issue="FILE_SIZE_LIMIT")
        return self.parse_bytes(path.read_bytes(), path.name)

    def parse_bytes(self, data: bytes, name: str, parent: str | None = None,
                    depth: int = 0, forced_issue: str | None = None) -> list[Source]:
        sha = digest(data)
        source = Source(source_id=f"src-{len(self.sources)+1:04d}-{sha[:12]}", filename=name,
                        sha256=sha, parent_id=parent, media_type=kind_of(data, name))
        self.sources.append(source)
        self.total_bytes += len(data)
        if forced_issue:
            source.issues.append(forced_issue)
            return [source]
        if (len(data) > self.s.max_file_bytes or self.total_bytes > self.s.max_batch_bytes
                or len(self.sources) > self.s.max_documents or depth > self.s.max_depth):
            source.issues.append("DOCUMENT_RESOURCE_LIMIT")
            return [source]
        children = []
        try:
            kind = source.media_type
            if kind in {".xlsx", ".docx"}:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    entries = archive.infolist()
                    if len(entries) > 10000 or sum(e.file_size for e in entries) > self.s.max_batch_bytes:
                        raise ValueError("OFFICE_EXPANDED_SIZE_LIMIT")
            if kind == ".eml":
                message = BytesParser(policy=policy.default).parsebytes(data)
                body = message.get_body(preferencelist=("plain", "html"))
                text = body.get_content() if body else ""
                if body and body.get_content_type() == "text/html":
                    text = BeautifulSoup(text, "html.parser").get_text("\n")
                source.text = "\n".join(f"{key}: {message.get(key, '')}" for key in
                                        ("Subject", "From", "To", "Date")) + "\n\n" + text
                source.method = "email-stdlib"
                for i, part in enumerate(message.iter_attachments()):
                    if len(self.sources) >= self.s.max_documents:
                        source.issues.append("ATTACHMENT_COUNT_LIMIT")
                        break
                    if part.get_content_type() == "message/rfc822":
                        payload = part.get_payload()
                        raw = payload[0].as_bytes() if isinstance(payload, list) and payload else b""
                        filename = part.get_filename() or f"attached-{i+1}.eml"
                    else:
                        raw = part.get_payload(decode=True) or b""
                        filename = part.get_filename() or f"attached-{i+1}"
                    children += self.parse_bytes(raw, filename, source.source_id, depth+1)
            elif kind in {".pdf", ".image", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
                if self.s.ocr_mode == "remote":
                    url = self.urls.get(sha)
                    raw = None
                    if url:
                        source.text, raw = RemoteOCR(self.s).recognize(url)
                    elif self.s.ocr_file_base_url:
                        from .file_store import OCRFileStore
                        # The specified endpoint rejected TIFF in live testing. Convert each frame
                        # to lossless PNG while keeping the original TIFF SHA256 and evidence text.
                        if data.startswith((b"II*\x00", b"MM\x00*")):
                            from PIL import Image
                            image = Image.open(io.BytesIO(data))
                            if image.n_frames > self.s.max_pages:
                                raise ValueError("IMAGE_PAGE_LIMIT")
                            payloads = []
                            for frame in range(image.n_frames):
                                image.seek(frame)
                                if image.width * image.height > 24_000_000:
                                    raise ValueError("IMAGE_PIXEL_LIMIT")
                                buffer = io.BytesIO()
                                image.convert("RGB").save(buffer, format="PNG")
                                with OCRFileStore(self.s).publish(buffer.getvalue(), ".png") as file_url:
                                    _, payload = RemoteOCR(self.s).recognize(file_url)
                                payloads.append(payload)
                            image.close()
                            # Only the observed synchronous structured contract is merged.
                            merged = [item for payload in payloads for item in payload["data"]["list"]]
                            raw = {"success": True, "data": {"list": merged},
                                   "transport": "TIFF_FRAMES_AS_PNG", "original_sha256": sha,
                                   "frame_responses": payloads}
                            from .ocr import ocr_text
                            source.text = ocr_text(raw)
                        else:
                            with OCRFileStore(self.s).publish(data, kind) as file_url:
                                source.text, raw = RemoteOCR(self.s).recognize(file_url)
                    else:
                        source.issues.append("OCR_PUBLIC_URL_REQUIRED")
                    if raw is not None:
                        self.raw_ocr[source.source_id] = raw
                        source.method = "exam-ocr-api"
                        if source.text.startswith("[OCR_STRUCTURED_DATA;"):
                            source.ocr_charge_rows, contract_issues = structured_charges(raw)
                            source.issues.extend(contract_issues)
                            self._raster_ocr_used = False
                            original = self.local_text(data, kind)
                            source.raster_ocr_used = self._raster_ocr_used
                            if original.strip():
                                source.text = original
                                source.method = "exam-ocr-api+local-evidence"
                            else:
                                source.method = "exam-ocr-api-structured"
                                source.issues.append("OCR_ORIGINAL_EVIDENCE_UNAVAILABLE")
                else:
                    self._raster_ocr_used = False
                    source.text = self.local_text(data, kind)
                    source.raster_ocr_used = self._raster_ocr_used
                    source.method = "local-preview"
                    source.issues.append("LOCAL_PREVIEW_REQUIRES_EXAM_OCR")
            elif kind == ".xlsx":
                import openpyxl
                values = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
                formulas = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=False)
                parts = []
                cell_count = 0
                try:
                    for ws in values:
                        parts.append(f"[SHEET {ws.title}]")
                        for row in ws:
                            cell_count += len(row)
                            if cell_count > 200000:
                                raise ValueError("EXCEL_CELL_LIMIT")
                            parts.append(f"row {row[0].row}: " + " | ".join(
                                "" if c.value is None else str(c.value) for c in row))
                        for row in formulas[ws.title]:
                            for c in row:
                                if c.data_type == "f" and ws[c.coordinate].value is None:
                                    source.issues.append("EXCEL_UNCACHED_FORMULA")
                    source.text = "\n".join(parts)
                    source.method = "openpyxl"
                finally:
                    values.close()
                    formulas.close()
            elif kind == ".xls":
                import xlrd
                book = xlrd.open_workbook(file_contents=data, on_demand=True)
                try:
                    parts = []
                    count = 0
                    for sheet in book.sheets():
                        count += sheet.nrows * sheet.ncols
                        if count > 200000:
                            raise ValueError("EXCEL_CELL_LIMIT")
                        parts.append(f"[SHEET {sheet.name}]")
                        for i in range(sheet.nrows):
                            cells = []
                            for cell in sheet.row(i):
                                value = (xlrd.xldate_as_datetime(cell.value, book.datemode).isoformat()
                                         if cell.ctype == xlrd.XL_CELL_DATE else cell.value)
                                cells.append(str(value))
                            parts.append(f"row {i+1}: " + " | ".join(cells))
                    source.text = "\n".join(parts)
                    source.method = "xlrd"
                finally:
                    book.release_resources()
            elif kind == ".docx":
                source.text = self.word_text(data)
                source.method = "python-docx"
            elif kind == ".doc":
                if not find_libreoffice(self.s) and not has_word():
                    source.issues.append("DOC_REQUIRES_LIBREOFFICE")
                else:
                    source.text = self.word_text(convert_office(data, ".doc", "docx", self.s))
                    source.method = ("libreoffice" if find_libreoffice(self.s) else "word") + "+python-docx"
            else:
                source.issues.append("UNSUPPORTED_FORMAT")
            if len(source.text) > self.s.max_text_chars:
                source.text = source.text[:self.s.max_text_chars]
                source.issues.append("TEXT_TRUNCATED")
            if not source.text.strip() and not source.issues:
                source.issues.append("EMPTY_DOCUMENT")
        except OCRFailure as exc:
            # OCRFailure messages are adapter-authored constants, never provider response bodies.
            source.issues.append("OCR_FAILED:" + str(exc))
        except Exception as exc:
            # File contents, URL query strings and HTTP headers must not leak into logs.
            source.issues.append(f"PARSE_FAILED:{type(exc).__name__}")
        source.issues = list(dict.fromkeys(source.issues))
        return [source, *children]

    @staticmethod
    def word_text(data: bytes) -> str:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        document = Document(io.BytesIO(data))
        parts = []
        for node in document.element.body:
            if node.tag.endswith("}p"):
                parts.append(Paragraph(node, document).text)
            elif node.tag.endswith("}tbl"):
                table = Table(node, document)
                parts.extend(" | ".join(c.text for c in row.cells) for row in table.rows)
        return "\n".join(parts)

    def image_text(self, data: bytes) -> str:
        self._raster_ocr_used = True
        import numpy as np
        from PIL import Image, ImageOps
        from rapidocr_onnxruntime import RapidOCR
        document = Image.open(io.BytesIO(data))
        count = getattr(document, "n_frames", 1)
        if count > self.s.max_pages:
            raise ValueError("IMAGE_PAGE_LIMIT")
        texts = []
        for i in range(count):
            document.seek(i)
            image = ImageOps.exif_transpose(document).convert("RGB")
            if image.width * image.height > 24_000_000:
                raise ValueError("IMAGE_PIXEL_LIMIT")
            if self._ocr is None:
                self._ocr = RapidOCR()
            rows, _ = self._ocr(np.array(image))
            text = self.image_rows(rows or [])
            texts.append((f"[FRAME {i+1}]\n" if count > 1 else "") + text)
        document.close()
        return "\n".join(texts)

    @staticmethod
    def image_rows(rows) -> str:
        # Preserve geometric row structure for invoices, not one isolated word per line.
        groups: list[list] = []
        for box, text, score in sorted(rows, key=lambda r: (r[0][0][1], r[0][0][0])):
            y = sum(p[1] for p in box) / 4
            height = max(p[1] for p in box) - min(p[1] for p in box)
            match = next((g for g in groups if abs(g[0]-y) < max(6, height * 0.6)), None)
            if match is None:
                groups.append([y, [(box[0][0], text)]])
            else:
                match[1].append((box[0][0], text))
        return "\n".join(" | ".join(t for _, t in sorted(items)) for _, items in sorted(groups))

    def local_text(self, data: bytes, kind: str) -> str:
        if kind != ".pdf":
            return self.image_text(data)
        import pdfplumber
        parts = []
        with pdfplumber.open(io.BytesIO(data)) as document:
            if len(document.pages) > self.s.max_pages:
                raise ValueError("PDF_PAGE_LIMIT")
            for i, page in enumerate(document.pages):
                text = page.extract_text(layout=False) or ""
                if len(text.strip()) < 50:
                    buffer = io.BytesIO()
                    page.to_image(resolution=160).original.save(buffer, format="PNG")
                    text = self.image_text(buffer.getvalue())
                parts.append(f"[PAGE {i+1}]\n{text}")
        return "\n".join(parts)

    def manifest(self) -> list[dict]:
        return [{"source_id": s.source_id, "filename": s.filename, "sha256": s.sha256,
                 "parent_id": s.parent_id, "format": s.media_type, "issues": s.issues}
                for s in self.sources]


def write_json(path: Path, data: object):
    """Decimal-safe legal JSON numbers. Atomic replace prevents partial result files."""
    import simplejson
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(simplejson.dumps(data, ensure_ascii=False, indent=2, use_decimal=True,
                                         allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path):
    from decimal import Decimal
    return json.loads(path.read_text(encoding="utf-8-sig"), parse_float=Decimal)
