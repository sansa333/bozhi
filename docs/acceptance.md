# 题目要求与完整交付验收

本轮按用户确认，缺少的记账主体、旧账和补充材料采用明确标注的合成数据。主体为 `Demo Trading Ltd`，交易方为虚构公司，邮箱使用 `.invalid`。这些配置不代表用户的实际业务身份。

## 要求对应的实现

| 题目要求 | 实现 | 可核查材料 |
|---|---|---|
| Python 和 LangChain | Runnable 工作流、ChatOpenAI、结构化输出和有限修复 | `src/shipping_agent/engine.py`、`extractor.py` |
| EML 及递归附件 | 邮件头、纯文本/HTML 正文、附件父子关系和摘要 | [邮件及 PDF 附件](../examples/acceptance/eml-attachment/notice.eml) |
| PDF、扫描 PDF、PNG、JPG、TIFF | 指定 OCR；文本 PDF 或本地图像 OCR 提供独立引文，再按费用名称、金额和行数核对 | [合成样本与标签](../examples/acceptance/manifest.json) |
| Excel 和 Word | XLS、XLSX、DOCX；DOC 使用 LibreOffice 或 Windows Word 隔离转换 | [Excel 更新](../examples/acceptance/xlsx-update/amendment.xlsx)、[DOC 贷项](../examples/acceptance/doc-credit/credit.doc) |
| 标准化与金额检查 | 费用别名、ISO 币种、PP/CC、数量单价、税前小计、税额和总计分别核对 | [含税 DOCX](../examples/acceptance/docx-tax/invoice.docx) |
| 匹配和增量更新 | 四级匹配、显式字段授权、继承旧值、保存变更前值 | [更新旧账](../examples/acceptance/xlsx-update/existing.json) |
| AR/AP、重复及冲突 | 按记账主体和交易方向；重复、冲突及多重匹配单独处理 | [验收清单](../examples/acceptance/manifest.json) |
| 合法 JSON 和处理依据 | Pydantic、Decimal、引文及位置、原始响应、变更记录、冲减政策 | [真实调用快照](../examples/acceptance/runs/) |
| harness 和至少五点思考 | 受控阶段、证据和财务检查、有限重试、重放、CI；10 点补充思考 | [设计说明](design.md) |

## 14 个合成验收场景

样本覆盖文本 PDF、扫描 PDF、PNG、JPG、TIFF、邮件 PDF 附件、邮件应收、XLSX 增量更新、XLS 同名不同金额、DOCX 小计及税额、DOC 贷项、币种缺失、重复与冲突。

每条记录核对 26 项，包括操作类型、单据信息、交易双方、金额、币种、付款方式、数量、单价、记账方向，以及小计、税额和总计。另检查原文引文的字符位置和待确认原因。

扫描图像实测存在 `DEMO` 被识别为 `DEM0` 的情况。遇到字母之间夹有孤立 0/1 等可疑编号，代码不会擅自替换；编号置为 null，候选值保存在 `unresolved_candidates`，结果转 `PENDING_REVIEW`。这是一项保守检查，合法但形似的编号也可能需要人工确认。

验收分别统计 `fields_matched` 和 `fields_safely_rejected`。后者只接受指定字段已置空、已有相应审核原因的情况，不能计作正确识别。错误的非空字段、遗漏费用或无依据的操作均使验收失败。因此“用例通过”不表示所有扫描字段均被识别正确，更不代表生产准确率达到 100%。

最新汇总保存在 [acceptance-summary.json](acceptance-summary.json)。`runs` 保存真实 OCR/模型调用得到的来源、引文和抽取快照。重放重新执行当前规则，不发起新 OCR 或模型请求，运行方式明确标记为 replay。

## 指定 OCR 的实测合同

已用包含实际合成费用的文件验证 `https://gateway.pltplt.com/ai/process`。实际费用字段为 `data.list[].chargeList[].chargeItem` 和 `amount`，还观察到 `chargeCtr`。服务没有返回可直接使用的原始 OCR 文字，也未在该费用结构中提供币种。

因此保留原始服务响应，再从原文件取得独立文本证据；模型的费用名称、金额和数量必须与服务结果逐行一致。币种、交易方向、数量及单价从有引文的原件字段取得，不根据缺失的服务字段猜测。冲突、未知响应或原文不可读时进入待确认。

该服务在本轮测试中拒绝直接处理 TIFF。实现将 TIFF 每一帧转为 PNG 分别调用同一个指定接口，保留原始 TIFF 摘要及各帧响应，再合并校验。没有将其他 OCR 服务冒充指定接口。

## 文件下载与启动

已有授权 HTTPS 存储时可继续提供 SHA256 到 URL 的清单。项目也提供独立的短时签名文件服务，上传文件在识别结束后删除，签名默认 15 分钟过期；应用工作台不通过该下载服务公开。

合成测试可显式使用 Cloudflare 临时通道。安装程序仅下载官方发布并核对 SHA256：

```powershell
uv run python scripts/install_cloudflared.py
# 将程序输出的 CLOUDFLARED_PATH 填入本地 .env
.\start.ps1 -OcrTunnel
```

临时通道退出即关闭，不是长期托管。真实业务部署应使用已经授权的 HTTPS 下载服务，配置 `OCR_FILE_BASE_URL` 和不少于 32 个随机字符的 `OCR_FILE_SECRET`，再启动 `shipping-agent file-server`。请求中的签名地址是临时读取凭据，不应公开。

## 贷项政策

默认 `CREDIT_NOTE_POLICY=review`。合成验收显式采用 `linked_signed_adjustment`，要求原文有原单编号，并能唯一匹配旧账的费用、币种、主体及交易双方。新贷项保留独立编号，以负金额冲减原单，并保留原单 AR/AP 分类。原单不被覆盖。

原始金额、转换后金额、原单 ID 和政策保存在 `accounting_adjustment`。超过剩余金额、历史贷项异常、同编号冲突、同批多笔贷项或无法唯一关联时转待确认。已确认贷项再次导入得到 NO_CHANGE。接入真实业务前需要确认该政策适用。

## 验收命令

```powershell
uv sync --frozen --extra dev
uv run pytest -q
uv run python scripts/run_acceptance.py --replay
# 显式重新调用指定 OCR 和当前配置的大模型
uv run python scripts/run_acceptance.py --live --tunnel
uv run ruff check src tests scripts
node --check src/shipping_agent/web.js
uv run python scripts/check_docs.py
uv run python scripts/package.py
```

重新生成样本使用 `scripts/make_acceptance.py`，需要 LibreOffice 或 Windows Word。重新生成会改变文件摘要，应随后重新真实运行，不能沿用旧快照。

这份交付验证题目要求的实现和合成业务流程。真实客户历史对账、外部财务系统写入、审批、并发事务和多租户部署不在合成验收范围内。
