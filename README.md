# 泊知 · 你的航运单据搭档

把邮件、账单和费用表，整理成有原文依据的账单建议。

泊知面向航运和国际货代费用整理，使用 **Python、LangChain、智谱 GLM 和确定性校验规则**。它可以提取费用、对照旧账、判断收付方向，将不明确的内容留给人确认，并导出标准 JSON 或 CSV。系统生成操作建议，不直接入账。

**本地网页 · 多格式解析 · 逐字段证据 · 增量更新 · 103 项自动化测试**

维护者 [sansa333](https://github.com/sansa333) · [GitHub 仓库](https://github.com/sansa333/bozhi)

## 先用一张图看懂

![输入、提取与核对、结果与导出的完整流程](docs/assets/workflow.svg)

| 你提供什么 | 泊知完成什么 | 你得到什么 |
|---|---|---|
| EML 邮件与附件、PDF、图片、Excel、Word | 解析文档、提取单据与费用、核对金额和币种 | 每笔费用一条结构化记录 |
| 当前记账公司、可选的已有账单 JSON | 匹配旧账、检查变更、判断 AR/AP | 新增、更新、保留或待确认建议 |
| 原始文字与明确修改指令 | 验证引文、保留未修改字段、检查冲突 | 来源、逐字段证据、变更前值和待确认原因 |

## 界面是什么样

上传区采用引导式工作台。常用操作直接展示，识别服务由后台统一配置，页面不要求使用者选择识别方式。

![泊知的实际工作台界面](docs/assets/01-workspace.jpg)

## 输入什么，输出什么

仓库自带三份可公开分发的合成邮件、一份合成旧账和一套真实模型运行快照。公司、邮箱、单据编号和费用均为演示数据，不是客户账单。

![合成演示的输入与预期输出对照](docs/assets/input-output.svg)

| 输入文件 | 场景 | 预期业务行为 |
|---|---|---|
| [01-new-invoice.eml](examples/walkthrough/01-new-invoice.eml) | Handling Fee 65 USD、Doc Fee 35 USD | 两条 `CREATE`，费用名标准化 |
| [02-update-instruction.eml](examples/walkthrough/02-update-instruction.eml) | 明确要求将 Handling Fee 改为 40 USD | 匹配旧账后 `UPDATE`，其他字段继承 |
| [03-currency-to-confirm.eml](examples/walkthrough/03-currency-to-confirm.eml) | 只写 `$50.00`，未说明币种 | `PENDING_REVIEW`，币种保留为 `null` |
| [existing-bills.json](examples/walkthrough/existing-bills.json) | 已有 Handling Charge 为 30 USD | 用于对照变更前后金额 |

### 第一步，上传材料，填写记账公司

选择三份 `.eml`，记账公司填 `Demo Trading Ltd`，再展开“有旧账单需要一起对照”，导入上面的 `existing-bills.json`。

![实际选择了三份邮件、填写公司并导入旧账](docs/assets/02-inputs.jpg)

### 第二步，开始整理，查看真实处理阶段

页面展示后台实际执行到的阶段和正在处理的文件，不使用模拟百分比。复杂材料或服务限流时可能等待更久。刷新页面会尝试找回同一任务，不会自动重复发起模型调用。

![真实模型调用期间的处理状态](docs/assets/03-processing.jpg)

### 第三步，一起核对结果

下图来自本仓库合成材料的实际运行。完整结果保存在 [result.json](examples/walkthrough/result.json)，运行信息、原文和证据保存在 [report.json](examples/walkthrough/report.json)。模型输出有波动，复现时请以本次报告为准，不以截图代替核查。

![真实运行得到的新增、更新和待确认记录](docs/assets/04-results.jpg)

### 第四步，查看字段变更与原文依据

点击“查看依据”，可以核查当前主体、原始费用、修改了哪些字段，以及引文来自哪里。没有充分证据的字段不会被悄悄补全。

![费用详情中的旧值、新值和原文依据](docs/assets/05-evidence.jpg)

### 第五步，导出你需要的格式

JSON 适合交给财务系统或继续处理，CSV 适合人工核对。导出由后台直接生成，金额使用 Decimal，避免浏览器转换金额时丢失精度。下面图片从本次实际 JSON 输出中选取字段，不是界面设计稿。

![本次实际 JSON 输出的字段节选](docs/assets/06-output-json.svg)

完整输出保留 `document_info`、`transaction_party`、`charge_details`、`financial_attributes` 和 `audit_trail`。`audit_trail` 包含来源、证据、待确认原因，以及更新时的 `changed_fields`、`previous_values`。

## 五分钟运行起来

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。以下命令在仓库根目录执行。

```powershell
git clone https://github.com/sansa333/bozhi.git
cd bozhi
uv sync --frozen --extra dev

# 只在没有配置文件时创建，不覆盖已有密钥
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

编辑本地 `.env`，填写自己的密钥。下面是配置格式，仓库不包含可用密钥。

```dotenv
LLM_API_KEY=填写你自己的密钥
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
LLM_MODEL=glm-4.5-flash
OCR_MODE=remote
ACCOUNTING_ENTITY=
```

```powershell
# 小规模真实调用，验证当前配置，而不只是检查“有没有填写密钥”
uv run shipping-agent doctor --live

# 启动本地网页，Windows 也可以运行 .\start.ps1
uv run shipping-agent serve --port 8010
```

打开 [本地工作台](http://127.0.0.1:8010/)。Linux/macOS 也可使用 `uv sync` 和 `uv run shipping-agent serve`，首次配置时自行将 `.env.example` 复制为 `.env`。

## 仓库结构

```text
shipping-bill-agent/
├── src/shipping_agent/   # 解析、模型抽取、证据校验、账单规则、API 与前端
├── tests/                # 默认离线的回归与接口测试
├── examples/walkthrough/ # 可分发的合成输入、实际输出与抽取快照
├── docs/assets/          # 本 README 的真实界面截图与 SVG 流程图
├── docs/                 # 使用说明、harness 设计、验证范围与模型检查记录
├── scripts/              # 演示、文档检查、材料评测、打包
├── .github/workflows/    # GitHub Actions 检查
├── .env.example          # 不含密钥的配置模板
├── pyproject.toml        # 项目与依赖配置
└── uv.lock               # 锁定依赖
```

`.env`、真实业务材料、`data/private`、运行输出、虚拟环境和生成的 ZIP 不进入 Git。网页访问凭据只保存在当前页面内存中。仓库里的演示图片和输出只使用合成业务数据。

## 验证与交付

```powershell
uv sync --frozen --extra dev
uv run pytest -q
uv run ruff check src tests scripts
node --check src/shipping_agent/web.js
uv run python scripts/check_docs.py
uv run python scripts/package.py
```

交付包生成在 `dist/shipping-bill-agent.zip`。CI 执行离线测试、代码检查、前端语法检查和 README 本地图片/链接检查，不会在 GitHub Actions 中偷偷调用付费服务。

| 文档 | 内容 |
|---|---|
| [部署与详细使用](docs/usage.md) | CLI、OCR、格式支持、API 与 Docker |
| [设计与业务思考](docs/design.md) | harness、证据、会计判断与生产边界 |
| [验证记录](docs/validation.md) | 已通过的检查与尚未完成的外部验收 |
| [模型状态](docs/model-status.md) | 连通性、错误类型与额度能确认到什么程度 |
| [开发说明](CONTRIBUTING.md) | 本地开发和修改检查 |
| [数据与凭据](SECURITY.md) | 密钥、运行数据与问题报告方式 |

当前是可运行的本地单进程工作台。任务状态不跨服务重启续跑，已完成报告可从历史记录恢复；DOC 需要 LibreOffice；Docker 配置已提供但尚未在本机运行验收。生产多租户、并发账本写入、审批和财务系统连接需要另行接入。
