# 部署与详细使用

基于 Python 3.12、LangChain、智谱 GLM 和规则校验的笔试项目。读取邮件及附件、PDF、图片、Excel、Word，生成 `CREATE`、`UPDATE`、`NO_CHANGE` 或 `PENDING_REVIEW` 账单建议。项目含本地网页、HTTP API、命令行、回归测试和可重放运行记录，不直接修改外部财务系统。

## 快速启动

Windows 在本目录执行：

```powershell
uv sync --frozen --extra dev
# 首次克隆需要复制配置，已配置的机器不要覆盖原 .env
Copy-Item .env.example .env
# 在 .env 填入模型密钥和服务地址，然后启动
.\start.ps1
```

浏览器访问 http://127.0.0.1:8010 。模型配置保存在自己的本地 `.env` 中。交付 ZIP 不包含 `.env`、密钥、原始业务材料和私人运行记录。

没有 uv 时可以使用 Python 3.12：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m shipping_agent.cli serve
```

## 指定 OCR 服务

默认模式 `remote` 调用题目给出的 `https://gateway.pltplt.com/ai/process`，请求体包含 `file`、`task=orderChange`、`callBack` 和 `taskType`。`file` 必须是该服务能下载的 HTTPS 地址。本机路径不能直接交给远程服务。

先生成材料和邮件附件清单：

```powershell
uv run shipping-agent inspect '<你的材料目录>' --out output/manifest.json
```

将需识别的 PDF、图片，包括邮件中的 PDF 附件，放入你授权使用的文件存储。根据清单建立 `urls.json`：

```json
{
  "文件的完整SHA256值": "https://your-authorized-storage.example/bill.pdf?signature=..."
}
```

按同一个 SHA256 映射原始文件和对应附件，避免同名文件错配。未启用文件下载服务时，系统不会自动托管材料。

```powershell
uv run shipping-agent run '<你的材料目录>' --urls urls.json `
  --entity '你的实际记账公司全称' --existing examples/existing-bills.json `
  --out output/records.json
```

2026-10-30 先用公开 dummy PDF 确认该接口返回业务结构，再用含费用的合成 PDF、图像和邮件附件完成联调。已确认费用字段为 `data.list[].chargeList[].chargeItem` 和 `amount`；币种、付款方式等缺失字段不根据接口结果猜测。服务响应原样保留，原文件提供独立引文，模型和服务的费用名称、金额及行数必须一致。未知格式、异步任务、空文本、HTTP 错误均进入待审核。[实际合同与验收说明](acceptance.md)

### 自动生成短时下载地址

使用合成材料开发时，可以显式启用独立文件服务和临时通道：

```powershell
uv run python scripts/install_cloudflared.py
# 将程序输出的 CLOUDFLARED_PATH 写入本地 .env 后
.\start.ps1 -OcrTunnel
```

这会在独立端口提供短时签名的 PDF、图像下载，网页上传和邮件附件不再逐个填写 URL。只开放文件下载，不将工作台暴露到公网；调用结束即删除临时文件，签名默认 900 秒到期。临时通道启动后先验证地址可访问，退出时关闭通道。该方式仅用于合成开发测试，真实业务需授权的下载服务。

部署自有文件服务时，在 `.env` 配置 `OCR_FILE_BASE_URL`、`OCR_FILE_SECRET`，执行 `uv run shipping-agent file-server --port 8011`，将该独立端口接到自己的 HTTPS 反向代理。密钥不少于 32 个随机字符，应用与文件服务使用相同 DATA_DIR 和密钥。TIFF 逐帧转成 PNG 交给同一个指定接口，原始摘要和各帧响应保留。

## 不依赖公网存储的本地预览

```powershell
uv run shipping-agent run '<你的材料目录>' --ocr-mode local --out output/materials-live.json
```

本地模式用 pdfplumber 提取 PDF 文本，扫描件和图片用 RapidOCR，仍真实调用已配置的大模型。凡经本地替代方式识别的 PDF/图片都会标记 `LOCAL_PREVIEW_REQUIRES_EXAM_OCR`。预览用于开发和核查，不能计作题目指定 OCR 的全流程验收。

记账主体没有从材料中得到可靠确认，默认保持为空。填写 `ACCOUNTING_ENTITY` 或传入 `--entity` 后，程序根据有证据的 payer/payee 判断 AR/AP，不能只看 Debit Note、Credit Note 或邮件收发人。`ACCOUNTING_ALIASES` 可配置同一法人的别名。

## 解析范围

| 文件 | 处理方式 | 重要边界 |
|---|---|---|
| EML | 标准库解析头、正文和递归附件 | 正文优先纯文本，HTML 转文本；保留父子来源 |
| PDF、PNG/JPG/TIFF | 指定 OCR API；可选本地预览 | 远程模式必须有可下载 URL |
| XLSX | openpyxl，全部工作表 | 未缓存的公式不虚构结果，转待审核 |
| XLS | xlrd | 使用已保存单元格值 |
| DOCX | python-docx | 按正文顺序保留段落和表格 |
| DOC | LibreOffice；Windows 可用 Word 隔离转换 | 支持 LIBREOFFICE_PATH；转换禁用宏，失败时待审核 |

Excel 和 Word 均有实际格式的合成文件和真实模型运行快照。DOC 在本机使用 Word 实测，Linux CI 使用 LibreOffice。用户原材料没有 Excel 或 Word，因此这些记录属于合成验收。容器构建、健康检查及 DOC 转换另由 CI 检查。

## 账单规则与证据

模型只产生候选字段、逐字段引文和明确修改指令。代码验证引文存在、值与引文一致、币种有效、数量乘单价、税前小计、税额、总计和同一来源的重复行。没有证据或非法币种的字段置为 null，并保留待审核原因。图像可疑编号的候选值保留在 unresolved_candidates，不猜测 O/0 或 I/1。金额内部使用 Decimal，输出为 JSON 数字，禁止 NaN/Infinity。

费用名按题目示例标准化，同时保留原名。`COD` 不被误转为 PP/CC。裸 `$` 不足以确定 USD。两个 Handling Charge 明细不会仅因同名而合并；多重匹配会待审核。

匹配按题目顺序执行：单据号和费用名；提单号、费用名和币种；订舱号、费用名和币种；箱号、交易双方、日期、金额和费用名。发现交易方或记账主体冲突时停止自动匹配。一个已有账单被多份变更材料指向时需人工确认顺序。

`UPDATE` 必须同时满足原文有修改指令、指令提到具体字段和新值、候选值有证据、字段在允许清单内。其余字段继承旧值。普通发票与旧账金额不同只产生差异提示，不自动覆盖。输出包含 `changed_fields` 和 `previous_values`。没有明确清空指令时 null 不覆盖旧值。

贷项默认待确认。`CREDIT_NOTE_POLICY=linked_signed_adjustment` 启用显式关联原单的冲减政策，检查唯一原单、交易双方、币种及剩余可冲减金额，以独立负金额记录保留原单 AR/AP；输出 entry_type、original_bill_id 和 accounting_adjustment。重复冲减、超额或同批多笔指向同一原单时不会自动放行。合成验收显式启用该政策，真实业务应先确认适用性。

`audit_trail.ai_reasoning` 是简短可核查的决定说明，不是模型内部思维过程。每条证据有来源 ID、引文和字符区间。每次运行在 `data/private/runs/<run_id>/` 保留完整报告、标准账单、抽取快照和 OCR 原始响应。相同来源及来源行产生稳定 ingestion ID，供下游幂等处理。跨次财务幂等仍需传入最新已有账单；本项目没有伪装成生产账本。

## API

`POST /api/process` 使用 multipart：`files` 多文件、`entity`、`existing_json`、`urls_json` 和 `ocr_mode`。返回报告对象，其中 `records` 为标准账单数组。`GET /health` 检查服务状态。交互接口说明见 `/docs`。

网页采用引导式工作台，提供拖拽上传、文件清单与移除、已有账单文件导入、真实任务状态、历史记录、结果搜索与状态筛选、逐字段证据详情，以及 JSON 和 CSV 导出。普通操作页不再显示识别方式，网页任务始终使用后台 `.env` 的 `OCR_MODE` 配置。

`POST /api/jobs` 上传并建立后台任务，`GET /api/jobs/{job_id}` 查询实际进度。刷新页面后会尝试恢复同一任务，不会自动重新发起识别。处理记录通过 `GET /api/runs` 和 `GET /api/runs/{run_id}` 获取，导出通过 `GET /api/runs/{run_id}/export?format=json` 或 `format=csv` 获取。

任务状态属于本地单进程服务，最多同时运行两批；服务重启后，可从处理记录找回已完成报告，未完成任务不会自动续跑。多进程或生产部署应另接持久化队列。

金额显示和分类汇总使用后台 Decimal 生成的精确字符串。导出直接从后台获取原始数据，避免浏览器数值精度影响金额；CSV 对可能被表格软件当作公式执行的文本做了保护。页面内业务内容通过 textContent 渲染。访问凭据仅在当前页面内存中使用，不持久化到浏览器存储。

默认只监听本机。绑定其他地址必须设置 `API_TOKEN`，调用时使用 `Authorization: Bearer ...`。Docker 启动前也需设置 Token，网页“连接设置”中填写相同 Token。

## 验证与交付

无需密钥的合成演示可直接查看 CREATE 和 UPDATE 行为，明确使用重放，不冒充真实模型结果：

```powershell
uv run python scripts/demo.py
```

```powershell
uv run pytest -q
uv run ruff check src tests scripts
uv run python scripts/evaluate_materials.py output/materials-live.report.json
uv run shipping-agent schema
uv run python scripts/package.py
```

交付包在 `dist/shipping-bill-agent.zip`。依赖锁定在 `uv.lock`，pip 用锁定版本导出的 `requirements.txt`。具体已验证结果与未完成的外部联调见 [validation.md](validation.md)，harness 设计与业务补充见 [design.md](design.md)。

运行重放：

```powershell
uv run shipping-agent run '<材料目录>' --ocr-mode local `
  --replay data/private/runs/<运行ID>/extractions.json --out output/replay.json
```

重放明确标记 `extraction_mode=replay`，不计为一次新的模型准确率评测。

本实现参考 [LangChain Runnable 文档](https://reference.langchain.com/python/langchain-core/runnables) 和 [LangChain 结构化输出说明](https://docs.langchain.com/oss/python/langchain/structured-output)。本项目使用兼容模型的 JSON 输出、Pydantic 验证和有限纠错重试，没有假定服务端支持严格 JSON Schema。
