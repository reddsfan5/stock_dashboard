#!/usr/bin/env python3
"""
选股管线入口 — 一键运行全部分析模块，生成仪表盘 HTML

用法
----
# ── 基础 ──
  python scripts/screen.py                              # 全部模块 → dashboard.html
  python scripts/screen.py --only continuity,sideways   # 指定模块（逗号分隔）
  python scripts/screen.py --refresh                    # 先更新缓存再跑
  python scripts/screen.py --backfill                   # 拉取120天历史

# ── 标的范围 ──
  python scripts/screen.py                              # 股票+ETF 全部（默认）
  python scripts/screen.py --universe stock             # 仅股票
  python scripts/screen.py --universe etf               # 仅 ETF
  python scripts/screen.py --universe all               # 全部

# ── 选股模块 ──
  continuity  — K线连续性（每天高点持续高于前低）
  sideways    — 横盘震荡（100分振幅+平坦度+重叠率评分）
  trend-up    — 连续上涨（N天收阳，过滤连板妖股）
  trend-down  — 连续下跌（N天收阴，超跌反弹机会）
  hammer      — 金针探底（长下影线探底形态）
  upward_gap  — 持续推高（连续N天最高价>前收×1.015）

# ── 输出 ──
  output/dashboard.html   — 自包含仪表盘（可排序表格+K线弹窗）
                            点击股票代码查看60日K线（←→切换标的，Esc关闭）
"""

import argparse
import os
import sys

import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from data.kline import StockData
from pipeline.runner import discover_modules, run_all
from pipeline.config import load_config, apply_config
from pipeline.reporter import build_screening_html


MAIN_BOARD = (
    "sh600", "sh601", "sh603", "sh605",
    "sz000", "sz001", "sz002", "sz003",
)


def _filter_main_board(cache: "pd.DataFrame") -> "pd.DataFrame":
    """仅保留沪深主板标的"""
    return cache[cache["代码"].str.startswith(MAIN_BOARD)].copy()


class CombinedData:
    """合并股票 + ETF 数据，对选股模块透明"""

    def __init__(self, stock_data: StockData, etf_data=None, etf_cache=None):
        self._stock = stock_data
        self._etf = etf_data
        self._etf_cache = etf_cache
        # ETF 名称映射
        self._etf_names = {}
        if etf_data is not None:
            try:
                etf_list = etf_data.get_list()
                # ETF 代码加前缀: 510050 → sh510050
                for _, r in etf_list.iterrows():
                    code = r["代码"]
                    prefix = "sh" if str(code).startswith(("5", "56", "58")) else "sz"
                    self._etf_names[prefix + str(code)] = r["名称"]
            except Exception:
                pass

    @property
    def cache(self):
        df = self._stock.cache
        if self._etf_cache is not None and len(self._etf_cache) > 0:
            df = pd.concat([df, self._etf_cache], ignore_index=True)

        return df

    def get_kline(self, code: str, days: int = 60):
        # ETF: 查 ETF 缓存
        if self._etf is not None and code in self._etf_names:
            return self._etf.get_kline(code, days)
        return self._stock.get_kline(code, days)

    def get_stock_name(self, code: str) -> str:
        if code in self._etf_names:
            return self._etf_names[code]
        return self._stock.get_stock_name(code)

    def __getattr__(self, name):
        return getattr(self._stock, name)


def main():
    parser = argparse.ArgumentParser(description="选股管线")
    parser.add_argument("--refresh", action="store_true", help="先更新缓存")
    parser.add_argument("--backfill", action="store_true", help="拉取历史数据")
    parser.add_argument("--only", type=str, default=None, help="逗号分隔的模块ID")
    parser.add_argument("--universe", type=str, default="all",
                        choices=["stock", "etf", "all"],
                        help="标的范围: stock=仅股票, etf=仅ETF, all=全部 (默认 all)")
    parser.add_argument("--out", type=str,
                        default=os.path.join(PROJECT_DIR, "output", "dashboard.html"),
                        help="输出 HTML 路径")
    args = parser.parse_args()

    only = args.only.split(",") if args.only else None

    # 1. 发现模块 + 加载配置
    pipeline = discover_modules()
    config = load_config()
    apply_config(config, pipeline)
    print(f"已加载配置: {len(config)} 条, 已发现模块: {len(pipeline)} 个")
    print(f"已发现模块: {', '.join(m.id for m in pipeline)}")

    # 2. 数据准备
    data = StockData()
    stocks = data.get_stock_list(board="all")
    if args.backfill:
        data.backfill(stocks)
    if args.refresh:
        data.update(stocks)

    # 标的范围
    etf_obj, etf_cache = None, None
    if args.universe in ("etf", "all"):
        try:
            from data.etf import ETFData
            etf_obj = ETFData()
            etf_cache = etf_obj.cache_with_prefix
            if len(etf_cache) > 0:
                print(f"ETF 池: {etf_cache['代码'].nunique()} 只, {len(etf_cache):,} 条")
        except Exception:
            pass

    if args.universe == "etf":
        # 仅 ETF：股票池置空
        data = StockData()
        data._cache = pd.DataFrame(columns=["代码", "日期", "开盘", "最高", "最低", "收盘", "成交额"])
        print("标的池: 仅 ETF")
    elif args.universe == "stock":
        # 仅股票：过滤为仅主板
        etf_cache = None
        data._cache = _filter_main_board(data.cache)
        print(f"标的池: 仅股票 ({data._cache['代码'].nunique():,} 只)")
    else:
        # 全部：股票侧过滤为主板
        data._cache = _filter_main_board(data.cache)
        print(f"标的池: {data._cache['代码'].nunique():,} 只股票 + {etf_cache['代码'].nunique() if etf_cache is not None else 0:,} 只ETF")

    combined = CombinedData(data, etf_obj, etf_cache)

    # 3. 执行分析
    results = run_all(combined, pipeline, only=only)

    # 4. 生成仪表盘
    modules = [m for m in pipeline if not only or m.id in only]
    html = build_screening_html(modules, results, data=combined)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    kb = os.path.getsize(args.out) / 1024
    print(f"\n✓ 仪表盘: {args.out} ({kb:.0f}KB)")


if __name__ == "__main__":
    main()
