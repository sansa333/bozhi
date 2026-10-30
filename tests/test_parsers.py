import io
from email.message import EmailMessage

from docx import Document
from openpyxl import Workbook

from shipping_agent.parsers import Parser


def test_email_body_headers_and_recursive_attachments(settings):
    nested = EmailMessage()
    nested["Subject"] = "Invoice INV-1"
    nested.set_content("Handling Fee USD 40")
    message = EmailMessage()
    message["Subject"] = "请更新费用"
    message["From"] = "a@example.com"
    message.set_content("正文 USD 40")
    message.add_attachment(nested.as_bytes(), maintype="application", subtype="octet-stream", filename="bill.eml")
    sources = Parser(settings).parse_bytes(message.as_bytes(), "root.eml")
    assert len(sources) == 2
    assert "正文 USD 40" in sources[0].text
    assert "From: a@example.com" in sources[0].text
    assert sources[1].parent_id == sources[0].source_id
    assert "Handling Fee USD 40" in sources[1].text


def test_html_email_uses_visible_text(settings):
    m = EmailMessage()
    m.set_content("<p>Handling Fee USD 40</p>", subtype="html")
    s = Parser(settings).parse_bytes(m.as_bytes(), "a.eml")[0]
    assert "Handling Fee USD 40" in s.text
    assert "<p>" not in s.text


def test_xlsx_multisheet_and_uncached_formula(settings):
    w = Workbook()
    w.active.append(["charge", "amount"])
    w.active.append(["Handling", 40])
    w.create_sheet("第二表").append(["THC", "=40+2"])
    b = io.BytesIO()
    w.save(b)
    s = Parser(settings).parse_bytes(b.getvalue(), "费用.xlsx")[0]
    assert "Handling | 40" in s.text
    assert "[SHEET 第二表]" in s.text
    assert "EXCEL_UNCACHED_FORMULA" in s.issues


def test_xls(settings):
    import xlwt
    w = xlwt.Workbook()
    sheet = w.add_sheet("Charges")
    sheet.write(0, 0, "Handling Fee")
    sheet.write(0, 1, 40)
    b = io.BytesIO()
    w.save(b)
    s = Parser(settings).parse_bytes(b.getvalue(), "费用.xls")[0]
    assert "Handling Fee | 40.0" in s.text
    assert not s.issues


def test_docx_order(settings):
    d = Document()
    d.add_paragraph("Invoice INV-1")
    table = d.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Handling Fee"
    table.cell(0, 1).text = "USD 40"
    d.add_paragraph("TOTAL USD 40")
    b = io.BytesIO()
    d.save(b)
    s = Parser(settings).parse_bytes(b.getvalue(), "费用.docx")[0]
    assert s.text.index("INV-1") < s.text.index("Handling Fee") < s.text.index("TOTAL")


def test_remote_pdf_requires_public_url(settings):
    s = Parser(settings).parse_bytes(b"%PDF-1.4\n", "disguised.bin")[0]
    assert s.media_type == ".pdf"
    assert s.issues == ["OCR_PUBLIC_URL_REQUIRED"]


def test_doc_reports_missing_converter(settings, monkeypatch):
    monkeypatch.setattr("shipping_agent.parsers.find_libreoffice", lambda _: None)
    monkeypatch.setattr("shipping_agent.parsers.has_word", lambda: False)
    s = Parser(settings).parse_bytes(b"fake legacy doc", "bill.doc")[0]
    assert s.issues == ["DOC_REQUIRES_LIBREOFFICE"]


def test_resource_limits(settings):
    settings.max_file_bytes = 10
    assert Parser(settings).parse_bytes(b"X"*11, "a.eml")[0].issues == ["DOCUMENT_RESOURCE_LIMIT"]
    assert Parser(settings).parse_bytes(b"", "a.eml", depth=5)[0].issues == ["DOCUMENT_RESOURCE_LIMIT"]


def test_corrupt_office_and_unsupported_format(settings):
    assert Parser(settings).parse_bytes(b"bad", "x.docx")[0].issues[0].startswith("PARSE_FAILED")
    assert Parser(settings).parse_bytes(b"bad", "x.exe")[0].issues == ["UNSUPPORTED_FORMAT"]


def test_office_zip_expansion_limit(settings):
    import zipfile
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("huge.xml", "X"*100000)
    settings.max_batch_bytes = 1000
    s = Parser(settings).parse_bytes(b.getvalue(), "x.xlsx")[0]
    assert s.issues == ["PARSE_FAILED:ValueError"]


def test_legacy_excel_date_is_read_as_date_not_serial(settings):
    from datetime import datetime

    import xlwt
    workbook = xlwt.Workbook()
    workbook.add_sheet("Invoice").write(0, 0, datetime(2026, 10, 30), xlwt.easyxf(num_format_str="YYYY-MM-DD"))
    buffer = io.BytesIO()
    workbook.save(buffer)
    source = Parser(settings).parse_bytes(buffer.getvalue(), "dated.xls")[0]
    assert "2026-10-30" in source.text


def test_all_tiff_frames_are_read_and_page_limit_applies(settings):
    from PIL import Image
    first, second = Image.new("RGB", (20, 20), "white"), Image.new("RGB", (20, 20), "black")
    buffer = io.BytesIO()
    first.save(buffer, format="TIFF", save_all=True, append_images=[second])
    parser = Parser(settings)
    calls = []

    def recognize(image):
        calls.append(image.mean())
        return [([[0, 0], [10, 0], [10, 10], [0, 10]], "page fee", .99)], None

    parser._ocr = recognize
    text = parser.image_text(buffer.getvalue())
    assert "[FRAME 1]" in text and "[FRAME 2]" in text
    assert len(calls) == 2 and calls[0] != calls[1]
    settings.max_pages = 1
    import pytest
    with pytest.raises(ValueError, match="IMAGE_PAGE_LIMIT"):
        parser.image_text(buffer.getvalue())
