#!/usr/bin/env python3
"""Validate and publish a generated market brief into the local SQLite archive."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from data.market_briefs import KINDS, MarketBriefRepository


def _load_payload(path: str | None) -> dict:
    if path:
        target = Path(path).expanduser().resolve()
        if not target.is_file():
            raise ValueError(f"输入文件不存在: {target}")
        raw = target.read_text(encoding="utf-8")
    else:
        raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("请通过 --input 或 stdin 提供 JSON 简报")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"简报 JSON 无效: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("简报 JSON 顶层必须是对象")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="发布晨间/收盘风格简报")
    parser.add_argument("--kind", required=True, choices=sorted(KINDS))
    parser.add_argument("--input", help="JSON 文件；省略时从 stdin 读取")
    parser.add_argument("--db", type=Path, help="测试或迁移使用的 SQLite 路径")
    parser.add_argument("--check", action="store_true", help="仅校验，不写入")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = _load_payload(args.input)
        if args.check:
            result = MarketBriefRepository.validate(payload, kind=args.kind)
            print(json.dumps({"ok": True, "kind": result["kind"], "brief_date": result["brief_date"]}, ensure_ascii=False))
            return 0
        repository = MarketBriefRepository(args.db) if args.db else MarketBriefRepository()
        result = repository.publish(payload, kind=args.kind)
        print(json.dumps({
            "ok": True,
            "kind": result["kind"],
            "brief_date": result["brief_date"],
            "revision": result["revision"],
            "idempotent": result["idempotent"],
        }, ensure_ascii=False))
        return 0
    except (OSError, ValueError, LookupError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
