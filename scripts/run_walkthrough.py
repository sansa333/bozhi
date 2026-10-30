"""Run all shareable README examples, using either live model calls or explicit saved replay."""
import argparse
from collections import Counter
from pathlib import Path

from shipping_agent.config import Settings
from shipping_agent.engine import Engine, ReplayExtractor, load_existing
from shipping_agent.parsers import read_json, write_json


def main():
    parser = argparse.ArgumentParser(description="复现 README 的三份合成邮件案例")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="使用本地 .env 的模型配置真实调用")
    mode.add_argument("--replay", action="store_true", help="重放仓库快照，不调用模型或 OCR")
    parser.add_argument("--out", type=Path, default=Path("output/walkthrough.json"))
    args = parser.parse_args()
    folder = Path("examples/walkthrough")
    paths = [folder / name for name in ("01-new-invoice.eml", "02-update-instruction.eml",
                                       "03-currency-to-confirm.eml")]
    extractor = ReplayExtractor(read_json(folder / "extractions.json")) if args.replay else None
    report = Engine(Settings(), extractor).run(paths, load_existing(folder / "existing-bills.json"),
                                               entity="Demo Trading Ltd")
    write_json(args.out, [r.model_dump() for r in report.records])
    write_json(args.out.with_name(args.out.stem + ".report.json"), report.model_dump())
    print(f"Mode: {report.metadata['extraction_mode']}; operations: "
          f"{dict(Counter(r.operation for r in report.records))}; output: {args.out}")


if __name__ == "__main__":
    main()
