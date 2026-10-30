import argparse
from pathlib import Path

from .config import Settings
from .diagnostics import check_model
from .engine import Engine, ReplayExtractor, load_existing
from .models import Extraction, bill_output_schema
from .parsers import Parser, read_json, write_json


def main():
    parser = argparse.ArgumentParser(description="航运账单助手：提取、核验并生成账单操作建议")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="盘点文件和邮件附件，生成 SHA256 清单")
    inspect.add_argument("inputs", nargs="+", type=Path)
    inspect.add_argument("--out", type=Path, default=Path("output/manifest.json"))
    run = sub.add_parser("run", help="处理文件；默认使用题目指定 OCR")
    run.add_argument("inputs", nargs="+", type=Path)
    run.add_argument("--existing", type=Path)
    run.add_argument("--urls", type=Path, help="SHA256 -> 公网文件 URL 的 JSON 对象")
    run.add_argument("--entity", help="当前记账主体，不能根据签名猜测")
    run.add_argument("--ocr-mode", choices=["remote", "local"])
    run.add_argument("--replay", type=Path, help="显式重放之前保存的 extractions.json")
    run.add_argument("--out", type=Path, default=Path("output/records.json"))
    schema = sub.add_parser("schema", help="导出账单和模型抽取 JSON Schema")
    schema.add_argument("--out", type=Path, default=Path("docs/extraction.schema.json"))
    serve = sub.add_parser("serve", help="启动本地网页和 API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8010)
    serve.add_argument("--ocr-tunnel", action="store_true",
                       help="显式启用仅供 OCR 读取短时签名文件的临时公网地址")
    file_server = sub.add_parser("file-server", help="启动仅提供签名文件下载的独立服务")
    file_server.add_argument("--host", default="127.0.0.1")
    file_server.add_argument("--port", type=int, default=8011)
    doctor = sub.add_parser("doctor", help="检查配置；--live 显式发起小规模模型调用")
    doctor.add_argument("--live", action="store_true", help="最多两次短请求，不查询付费余额")
    doctor.add_argument("--out", type=Path, help="将脱敏的检查结果保存为 JSON")
    args = parser.parse_args()
    settings = Settings()
    if args.command == "file-server":
        import uvicorn

        from .file_store import create_file_app
        uvicorn.run(create_file_app(settings), host=args.host, port=args.port, access_log=False)
        return
    if args.command == "doctor":
        import simplejson
        result = check_model(settings, args.live)
        if args.out:
            write_json(args.out, result)
        print(simplejson.dumps(result, ensure_ascii=False, indent=2))
        if not result["ok"]:
            raise SystemExit(1)
        return
    if args.command == "serve":
        import uvicorn
        if args.host not in {"127.0.0.1", "localhost", "::1"} and not settings.api_token.get_secret_value():
            parser.error("对外监听必须设置 API_TOKEN")
        if args.ocr_tunnel:
            from .tunnel import ocr_tunnel
            with ocr_tunnel(settings, port=args.port+1):
                print("已启用短时签名 OCR 下载，退出后自动关闭；仅上传需识别的 PDF 和图片。")
                uvicorn.run("shipping_agent.api:app", host=args.host, port=args.port)
        else:
            uvicorn.run("shipping_agent.api:app", host=args.host, port=args.port)
        return
    if args.command == "schema":
        write_json(args.out, Extraction.model_json_schema())
        write_json(args.out.with_name("bill.schema.json"), bill_output_schema())
        return
    paths = []
    for p in args.inputs:
        paths.extend(sorted(x for x in p.iterdir() if x.is_file()) if p.is_dir() else [p])
    if args.command == "inspect":
        # No remote network calls. Preview may use local OCR so attachments can be inventoried.
        settings.ocr_mode = "local"
        p = Parser(settings)
        for path in paths:
            p.parse_file(path)
        write_json(args.out, p.manifest())
        print(f"发现 {len(p.sources)} 个文档（含附件），清单已写入 {args.out.resolve()}")
    else:
        if args.ocr_mode:
            settings.ocr_mode = args.ocr_mode
        urls = read_json(args.urls) if args.urls else {}
        if not isinstance(urls, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                               for k, v in urls.items()):
            parser.error("--urls 必须是 SHA256 到 URL 的 JSON 对象")
        extractor = ReplayExtractor(read_json(args.replay)) if args.replay else None
        report = Engine(settings, extractor).run(paths, load_existing(args.existing), urls, args.entity)
        write_json(args.out, [r.model_dump() for r in report.records])
        write_json(args.out.with_name(args.out.stem + ".report.json"), report.model_dump())
        print(f"输出 {len(report.records)} 条，待审核 {report.metadata['pending_count']} 条。"
              f"结果 {args.out.resolve()}，运行 ID {report.run_id}")


if __name__ == "__main__":
    main()
