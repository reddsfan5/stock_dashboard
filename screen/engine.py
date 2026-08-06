"""
通用选股引擎 — 排序/过滤/输出

设计：管道模式，链式调用，不关心上游数据来源

使用示例
--------
>>> from stock_screen import Screener

# 多级排序
>>> result = (Screener(df)
...     .join_info()                              # 关联行业/名称
...     .filter(sector="电力行业")                  # 只看电力
...     .sort(["行业", "综合评分"], [True, False])  # 行业升序，评分降序
...     .head(50)
...     .to_csv("output.csv"))

# CLI 对应
$ python stock_select.py --sort 行业,asc --sort 综合评分,desc --sector 电力行业
"""

import argparse
import pandas as pd
from typing import List, Optional, Tuple, Union


class Screener:
    """通用选股引擎"""

    def __init__(self, df: pd.DataFrame):
        self._df = df.copy()
        self._info = None

    # ========== 数据关联 ==========

    def join_info(self) -> "Screener":
        """关联股票档案（行业/名称）"""
        try:
            from data.industry import StockInfo
            self._info = StockInfo()
            info_cols = self._info.df[["代码", "名称", "申万1级", "申万2级", "申万3级"]]
            # 如果原来就有这些列，先删掉再 join
            for col in ["名称", "申万1级", "申万2级", "申万3级"]:
                if col in self._df.columns:
                    self._df = self._df.drop(columns=[col])
            self._df = self._df.merge(info_cols, on="代码", how="left")
        except ImportError:
            pass
        return self

    # ========== 过滤 ==========

    def filter(self, sector: Optional[str] = None,
               code_prefix: Optional[str] = None,
               min_amount: Optional[float] = None) -> "Screener":
        """
        过滤条件（可组合）

        Args:
            sector: 行业名（如"电力行业"）
            code_prefix: 代码前缀（如"sh600"）
            min_amount: 最小日均成交额（万元）
        """
        if sector:
            self.join_info()
            # 支持按申万1级或2级或3级筛选
            matched = False
            for col in ["申万1级", "申万2级", "申万3级"]:
                if col in self._df.columns:
                    self._df = self._df[self._df[col] == sector]
                    matched = True
                    break
            if not matched:
                pass  # 列不存在则忽略

        if code_prefix:
            self._df = self._df[
                self._df["代码"].str.startswith(code_prefix)
            ]

        if min_amount is not None:
            col = "10日平均成交额(万元)" if "10日平均成交额(万元)" in self._df.columns else None
            if col:
                self._df = self._df[self._df[col] >= min_amount]

        return self

    # ========== 排序 ==========

    def sort(self, by: Union[str, List[str]],
             ascending: Union[bool, List[bool]] = True) -> "Screener":
        """
        排序

        Args:
            by: 列名或列名列表
            ascending: True/False 或 [True, False, ...]

        Examples:
            .sort("综合评分", ascending=False)
            .sort(["行业", "综合评分"], ascending=[True, False])
        """
        self._df = self._df.sort_values(by=by, ascending=ascending)
        self._df = self._df.reset_index(drop=True)
        return self

    # ========== 输出 ==========

    def head(self, n: int = 50) -> pd.DataFrame:
        """取前 N 行"""
        return self._df.head(n)

    def to_df(self) -> pd.DataFrame:
        """返回完整 DataFrame"""
        return self._df

    def to_csv(self, path: str = "output.csv", index: bool = False) -> "Screener":
        """导出 CSV（自动修复 Timestamp 列名为日期格式）"""
        df = self._df.copy()
        # Timestamp 列名 → "2026-07-24"（去时分秒）
        df.columns = [
            str(c)[:10] if isinstance(c, pd.Timestamp) else str(c)
            for c in df.columns
        ]
        df.to_csv(path, index=index, encoding="utf-8")
        return self

    def print(self, n: int = 50, cols: Optional[List[str]] = None):
        """打印前 N 行"""
        out = self._df.head(n).copy()
        # Timestamp 列名 → 日期格式
        out.columns = [
            str(c)[:10] if isinstance(c, pd.Timestamp) else str(c)
            for c in out.columns
        ]
        if cols:
            out = out[cols]
        print(out.to_string())
        return self

    # ========== 便捷方法 ==========

    def count(self) -> int:
        return len(self._df)

    def columns(self) -> List[str]:
        return list(self._df.columns)

    def sectors_breakdown(self) -> pd.Series:
        """行业分布统计（申万1级）"""
        self.join_info()
        return self._df["申万1级"].value_counts()


# ====================================================================
# CLI 排序参数解析（供各分析脚本复用）
# ====================================================================

def add_sort_args(parser: argparse.ArgumentParser):
    """给 ArgumentParser 加排序/过滤参数"""
    parser.add_argument("--sort", action="append", default=None,
                        metavar="COL,asc|desc",
                        help="排序列，可重复（如 --sort 行业,asc --sort 评分,desc）")
    parser.add_argument("--sector", default=None, help="筛选行业")
    parser.add_argument("--code-prefix", default=None, help="代码前缀过滤")
    parser.add_argument("--min-amount-filter", type=float, default=None,
                        help="最小成交额(万)（与 --min-amount 区分）")
    parser.add_argument("--head", type=int, default=50, help="输出前N行")
    parser.add_argument("--out", default=None, help="输出CSV路径")
    parser.add_argument("--print-cols", default=None, help="打印列，逗号分隔")


def parse_sort_args(args) -> Tuple[Optional[List[str]], Optional[List[bool]]]:
    """解析 --sort COL,asc|desc 参数"""
    if not args.sort:
        return None, None
    by, asc = [], []
    for s in args.sort:
        parts = s.split(",")
        by.append(parts[0].strip())
        asc.append(parts[1].strip().lower() != "desc" if len(parts) > 1 else True)
    return by, asc


def apply_screen_args(screener: Screener, args) -> Screener:
    """把 CLI 参数应用到 Screener"""
    screener.join_info()

    # 过滤
    amount = getattr(args, 'min_amount', None) or getattr(args, 'min_amount_filter', None)
    if args.sector or args.code_prefix or amount:
        screener.filter(sector=args.sector, code_prefix=args.code_prefix,
                        min_amount=amount)

    # 排序
    by, asc = parse_sort_args(args)
    if by:
        screener.sort(by=by, ascending=asc)

    return screener
