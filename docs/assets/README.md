# 文档图片来源

本目录所有界面图片均由浏览器实际截图得到，没有在图片中替换处理状态或费用值。

| 文件 | 内容与来源 |
|---|---|
| 01-workspace.jpg | 泊知默认工作台 |
| 02-inputs.jpg | 选择三份合成邮件、填写合成主体、导入合成旧账 |
| 03-processing.jpg | 开发复测期间一次真实模型请求的费用提取阶段 |
| 04-results.jpg | run-summary.json 中所记最终真实运行的结果列表 |
| 05-evidence.jpg | 同一最终运行的更新记录详情 |
| workflow.svg | 代码绘制的流程示意图 |
| input-output.svg | 合成案例的输入与预期业务行为 |
| 06-output-json.svg | 根据最终实际 result.json 的 UPDATE 记录绘制的字段节选 |

截图使用同一组合成输入，可能来自开发和复测中的不同轮次。最终输出以 [运行摘要](../../examples/walkthrough/run-summary.json) 与 [完整报告](../../examples/walkthrough/report.json) 为准。

SVG 可运行 `uv run python scripts/build_doc_diagrams.py` 重新生成。原始客户材料、本地密钥和真实交易信息不在此目录中。
