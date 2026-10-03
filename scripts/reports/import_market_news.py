#!/usr/bin/env python3
"""将公开渠道收集的标准化资讯导入本地资讯中心。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from data.market_news import MarketNewsRepository, SOURCE_CATALOG


def _load(path: str):
    if path == "-":
        payload = json.load(sys.stdin)
    else:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("items")
    if not isinstance(payload, list):
        raise ValueError("输入必须是数组，或包含 items 数组的对象")
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description="导入市场资讯标题、摘要与来源链接")
    parser.add_argument("--source", required=True, choices=sorted(SOURCE_CATALOG))
    parser.add_argument("--input", required=True, help="JSON 文件；使用 - 从标准输入读取")
    parser.add_argument("--partial", action="store_true", help="标记本次来源覆盖不完整")
    parser.add_argument("--message", default="")
    parser.add_argument("--check", action="store_true", help="只校验，不写入")
    args = parser.parse_args(argv)
    items = _load(args.input)
    repo = MarketNewsRepository()
    normalized = [repo._normalize_import_item(item, args.source) for item in items]
    if args.check:
        result = {"source_key": args.source, "validated": len(normalized), "written": False}
    else:
        result = repo.ingest(
            items, source_key=args.source, complete=not args.partial, message=args.message
        )
        result["written"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
