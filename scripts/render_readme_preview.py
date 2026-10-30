"""Local visual QA. Run with: uv run --no-project --with markdown python scripts/render_readme_preview.py"""
import shutil
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "output/readme-preview"

if __name__ == "__main__":
    DESTINATION.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "docs/assets", DESTINATION / "docs/assets", dirs_exist_ok=True)
    body = markdown.markdown((ROOT / "README.md").read_text(encoding="utf-8"),
                             extensions=["tables", "fenced_code"])
    style = """body{max-width:1060px;margin:45px auto;padding:0 30px 60px;font:15px/1.9 system-ui,
    'Microsoft YaHei',sans-serif;color:#263c32}h1,h2,h3{line-height:1.5;color:#254d39}
    h1{font-size:36px}h2{font-size:25px;margin-top:48px;border-bottom:1px solid #e3e9df;padding-bottom:12px}
    h3{font-size:19px;margin-top:35px}img{max-width:100%;border:1px solid #e4ebe0;border-radius:8px}
    pre{padding:20px;background:#f5f8f1;border-radius:8px;overflow:auto;font:13px/1.8 Consolas,monospace}
    code{color:#4e7450}table{width:100%;border-collapse:collapse;font-size:13px}
    td,th{border:1px solid #dde6d7;text-align:left;padding:11px}th{background:#f3f7ee}a{color:#387748}
    """
    (DESTINATION / "index.html").write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="UTF-8"><title>泊知 README 预览</title>'
        f'<style>{style}</style><body>{body}</body></html>', encoding="utf-8")
    print(DESTINATION / "index.html")
