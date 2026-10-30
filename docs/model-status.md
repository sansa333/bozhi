# 模型连通性与额度核查

这是 2026-10-30 的一次检查记录，机器可读结果见 [model-status.json](model-status.json)。它说明检查当时的结果，不保证外部服务持续可用。

| 项目 | 本次结果 |
|---|---|
| 请求模型名 | glm-4.5-flash |
| 服务 | 智谱 BigModel 的标准 API 地址 |
| 本地密钥是否已配置 | 是，密钥未进入文档或 Git |
| 13:50:27（北京时间）的请求 | 成功，返回 CONNECTION_OK |
| token 使用 | 输入 15、输出 4、合计 19 |
| 付费余额 / 剩余套餐额度 | 未查询，不能由本次请求推断 |
| 直接请求 glm-4.7-flash | 观察到 HTTP 429 和请求超时，尚未验证稳定可用 |

初次检查曾遇到业务码 `1234`、HTTP 500，重试后恢复。智谱将 `1234` 定义为网络错误，将 `1113` 定义为账户欠费。不能把服务端网络错误或无明确业务码的 429 一律解释为余额不足。[官方错误码](https://docs.bigmodel.cn/cn/api/api-code)

智谱官方已发布 GLM-4.7-Flash，并说明旧的 GLM-4.5-Flash 请求会路由至新型号。本项目暂保留这次已经实测成功的兼容模型名；这不代表确认了后端实际使用的模型权重。更换 `LLM_MODEL` 后应再次验证连通性。[官方公告](https://www.zhipuai.cn/zh/news/148)、[模型文档](https://docs.bigmodel.cn/cn/guide/models/free/glm-4.7-flash)

自行检查：

```bash
# 仅检查本地配置，不调用模型
uv run shipping-agent doctor

# 明确发起小规模请求，最多两次短调用；不会查询账户余额
uv run shipping-agent doctor --live --out output/model-check.json
```

免费模型调用成功只能证明该模型在当时可以访问，不能证明付费模型的余额、Coding Plan 或其他套餐仍有剩余额度。准确余额、赠送额度有效期和套餐限额以账户控制台为准。
