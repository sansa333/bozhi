# 开发与验证

使用 Python 3.12 和 uv，在仓库根目录执行：

```bash
uv sync --frozen --extra dev
uv run pytest -q
uv run ruff check src tests scripts
node --check src/shipping_agent/web.js
```

修改账单规则时，请用案例说明输入、旧账状态、预期操作及应保留的字段。涉及外部接口时，将服务替换为显式的测试对象，默认测试不调用模型或 OCR。

`examples/walkthrough` 中的公司、账单和邮箱均为合成数据。修改演示后，用 `scripts/make_walkthrough.py` 重新生成输入，并注明输出来自真实模型还是重放。

更新页面后检查默认界面、文件选择、结果筛选、依据弹窗和历史记录。文档图片放在 `docs/assets`，使用仓库内相对路径，避免依赖本机绝对路径或外部图床。

提交前确认 `.env`、访问凭据、原始业务材料和 `data/private` 未加入 Git。生成交付包：

```bash
uv run python scripts/package.py
```
