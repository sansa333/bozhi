"""Build code-native SVG diagrams for the README. No generated/mock UI screenshots."""
import json
from html import escape
from pathlib import Path

ASSETS = Path("docs/assets")
FONT = "'Segoe UI','Microsoft YaHei','PingFang SC',sans-serif"


def text(x, y, value, size=20, color="#233f3c", weight=400):
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" fill="{color}">{escape(value)}</text>')


def card(x, y, width, height, fill="#fff", stroke="#dce7df"):
    return f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="20" fill="{fill}" stroke="{stroke}"/>'


def frame(title, subtitle, content, height=620):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="{height}" viewBox="0 0 1400 {height}" '
            f'role="img" aria-label="{escape(title)}"><rect width="1400" height="{height}" fill="#f5f7f4"/>'
            '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">'
            '<path d="M0 0 L8 4 L0 8" fill="#86a18b"/></marker></defs>'
            + text(60, 47, "BOZHI / 泊知", 14, "#6f927b", 600)
            + text(60, 99, title, 32, "#1e4137", 600)
            + text(60, 138, subtitle, 17, "#738679") + content + '</svg>')


def workflow():
    body = ""
    data = [(60, "01  上传材料", "#eaf2e8", ["邮件正文与递归附件", "PDF、扫描图片", "Excel、Word 费用表", "已有账单 JSON 与记账主体"]),
            (520, "02  提取与核对", "#fff", ["按格式解析，按需调用 OCR", "大模型提取字段与原文依据", "校验金额、币种、合计与重复", "匹配旧账，只更新明确变更"]),
            (980, "03  查看与导出", "#edf3ed", ["新增 / 更新 / 保留 / 待确认", "按费用与单据搜索、筛选", "逐字段查看来源和处理依据", "下载标准 JSON 或 CSV"])]
    for x, title, fill, lines in data:
        body += card(x, 186, 360, 277, fill) + text(x+25, 230, title, 22, "#386449", 600)
        for i, line in enumerate(lines):
            body += f'<circle cx="{x+28}" cy="{276+i*45}" r="3" fill="#8aab8d"/>'
            body += text(x+44, 282+i*45, line, 17, "#627860")
    for x in (440, 900):
        body += f'<path d="M{x} 325h58" stroke="#86a18b" stroke-width="2" marker-end="url(#arrow)"/>'
    body += card(60, 500, 1280, 73, "#e9f0e5", "#dce7d7")
    body += text(88, 543, "遇到缺失信息、冲突或低置信度时，明确标为待确认。系统生成操作建议，不直接入账。", 20, "#5d7b54")
    return frame("从一份材料，到可核查的账单建议", "输入清楚、处理有依据、输出可复核。", body)


def input_output():
    body = card(60, 182, 590, 355, "#fff") + card(750, 182, 590, 355, "#eef4ea")
    body += text(86, 225, "输入：仓库自带的合成演示材料", 23, "#3b6749", 600)
    left = [("01-new-invoice.eml", "Handling Fee 65.00 USD；Doc Fee 35.00 USD"),
            ("02-update-instruction.eml", "明确要求将 Handling Fee 更新为 USD 40.00"),
            ("03-currency-to-confirm.eml", "只有 $50.00，未明确是哪一种美元"),
            ("existing-bills.json", "原账金额 USD 30.00；记账公司 Demo Trading Ltd")]
    for i, (a, b) in enumerate(left):
        body += text(87, 270+i*67, a, 18, "#47624a", 600) + text(87, 297+i*67, b, 16, "#7b8a73")
    body += '<path d="M675 355h48" stroke="#86a18b" stroke-width="2" marker-end="url(#arrow)"/>'
    body += text(776, 225, "输出：每个费用项目单独一条记录", 23, "#3b6749", 600)
    right = [("CREATE", "65.00 与 35.00 两条新增建议", "#6b9265"),
             ("UPDATE", "30.00 → 40.00；未修改的字段继承原值", "#6c8ba2"),
             ("PENDING_REVIEW", "保留金额 50.00，币种为 null，等待确认", "#aa8a50"),
             ("JSON / CSV", "同时保留原文证据、来源和处理说明", "#6b9265")]
    for i, (a, b, color) in enumerate(right):
        body += text(777, 270+i*67, a, 17, color, 600) + text(777, 297+i*67, b, 16, "#72856a")
    body += text(61, 581, "以上为合成案例的预期业务行为；本次实际调用结果和截图见下方演示报告。", 17, "#8b977f")
    return frame("输入什么，输出什么？", "三种典型情况，用同一批材料一起演示。所有公司、邮箱和单据均为合成数据。", body)


def output_json():
    records = json.loads(Path("examples/walkthrough/result.json").read_text(encoding="utf-8"))
    record = next(r for r in records if r["operation"] == "UPDATE")
    d, c, f, a = (record[k] for k in ("document_info", "charge_details", "financial_attributes", "audit_trail"))
    lines = ['{', '  "operation": "UPDATE",',
             '  "document_info": ' + json.dumps({"reference_no": d["reference_no"]}) + ',',
             '  "charge_details": {',
             '    "charge_item": ' + json.dumps(c["charge_item"]) + ',',
             '    "amount": ' + json.dumps(c["amount"]) + ',',
             '    "currency": ' + json.dumps(c["currency"]), '  },',
             '  "financial_attributes": ' + json.dumps({"posting_type": f["posting_type"]}) + ',',
             '  "audit_trail": {',
             '    "changed_fields": ' + json.dumps(a["changed_fields"]) + ',',
             '    "previous_values": ' + json.dumps(a["previous_values"]), '  }', '}']
    body = card(60, 180, 870, 485, "#203c31", "#203c31")
    for i, line in enumerate(lines):
        body += (f'<text x="85" y="{221+i*30}" xml:space="preserve" font-family="Consolas,monospace" '
                 f'font-size="18" fill="#dfebd5">{escape(line)}</text>')
    notes = [("操作建议", "UPDATE，仍由人确认后入账"),
             ("保留原值", "金额从 30.00 改为 40.00"),
             ("有据可查", "记录变更字段、旧值和原文证据")]
    for i, (title, note) in enumerate(notes):
        body += card(960, 180+i*162, 380, 140, "#edf3e8")
        body += text(983, 222+i*162, title, 19, "#456b40", 600)
        body += text(983, 259+i*162, note, 17, "#788d6d")
    body += text(61, 711, "图片为字段节选；完整结构、证据和来源请查看 examples/walkthrough/result.json。", 17, "#829379")
    return frame("输出里，不只有一个金额", "从真实模型运行的 JSON 中选取更新记录，展示财务字段与可核查的变更依据。", body, height=750)


if __name__ == "__main__":
    ASSETS.mkdir(parents=True, exist_ok=True)
    for name, svg in (("workflow.svg", workflow()), ("input-output.svg", input_output()),
                      ("06-output-json.svg", output_json())):
        (ASSETS / name).write_text(svg, encoding="utf-8")
    print("Generated three README SVG diagrams")
