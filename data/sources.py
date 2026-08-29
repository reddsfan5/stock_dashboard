"""
多数据源层 — akshare(腾讯) 主源 + baostock 备源

背景：2026-08-15 腾讯行情接口 SSL 抖动导致整条定时链路失败，
单数据源是单点故障。本模块提供 baostock 备源，供增量更新失败时降级。

约定
----
- 统一返回格式：日期(datetime)/开盘/最高/最低/收盘/成交额(元)/代码
  screen/*.py 在使用流动性阈值时统一除以 10000 转为万元
- 失败语义：所有 fetch 函数失败/空结果返回 None（由调用方记录 last_failed）
- baostock 线程不安全（全局单 socket 会话），必须包在 baostock_session()
  内且顺序调用（threads=1）
"""

import os
import logging
import time
from contextlib import contextmanager
from typing import Callable, Iterable, List, Optional, Tuple

import pandas as pd
import requests

import akshare as ak

try:
    import baostock as bs
except ImportError:  # baostock 缺失时 kline.py 主源仍可用
    bs = None

logger = logging.getLogger(__name__)

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 沪深主板代码前缀（get_stock_list 的 main 过滤共用）
MAIN_BOARD_PREFIX = (
    "000", "001", "002", "003",     # 深市主板
    "600", "601", "603", "605",     # 沪市主板
)

# baostock 与腾讯历史接口的成交额都统一按“元”写入缓存。
BS_AMOUNT_SCALE = 1.0

_BS_LOGGED_IN = False  # baostock 模块级会话标志

TX_QUOTE_URL = "https://qt.gtimg.cn/q="
TX_QUOTE_BATCH = 400


# ====================================================================
# 数据规范化
# ====================================================================

def _normalize(df: pd.DataFrame, code: str, amount_scale: float = 1.0) -> pd.DataFrame:
    """英文列 → 项目统一格式：日期/开盘/最高/最低/收盘/成交额(元)/代码。"""
    df = df[["date", "open", "high", "low", "close", "amount"]].copy()
    df.columns = ["日期", "开盘", "最高", "最低", "收盘", "成交额"]
    df["代码"] = code
    df["日期"] = pd.to_datetime(df["日期"])
    for c in ["开盘", "最高", "最低", "收盘", "成交额"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if amount_scale != 1.0:
        df["成交额"] = df["成交额"] * amount_scale
    return df


def _to_bs_date(yyyymmdd: str) -> str:
    """YYYYMMDD → YYYY-MM-DD（baostock 参数格式）"""
    return pd.Timestamp(yyyymmdd).strftime("%Y-%m-%d")


# ====================================================================
# 数据源：akshare（腾讯）
# ====================================================================

def ak_fetch_kline(code: str, start: str, end: str, timeout: int = 5) -> Optional[pd.DataFrame]:
    """
    akshare 腾讯接口日K线（主源）。

    Args:
        code: 带交易所前缀的代码，如 sh600519
        start/end: YYYYMMDD
    Returns:
        统一格式 DataFrame；失败/空返回 None
    """
    try:
        df = ak.stock_zh_a_hist_tx(
            symbol=code, start_date=start, end_date=end,
            adjust="", timeout=timeout,
        )
        # 空结果（周末/停牌）= 成功但无新数据，与 None（失败）区分开
        return _normalize(df, code)
    except Exception:
        return None


def _tx_symbol(code: str) -> str:
    """纯数字或项目代码统一为腾讯报价代码。"""
    value = str(code).strip().lower()
    if value.startswith(("sh", "sz")):
        return value
    return ("sh" if value.startswith(("5", "6")) else "sz") + value


def tx_fetch_daily_snapshot(
    codes: Iterable[str], batch_size: int = TX_QUOTE_BATCH,
    timeout: int = 15, retries: int = 3,
) -> Tuple[pd.DataFrame, List[str]]:
    """批量拉取腾讯收盘快照，通常十几次请求即可覆盖全市场。

    返回的数据可直接合并进日 K 缓存，成交额使用报价字段中的精确“元”值。
    单批请求连续失败或返回中缺少某代码时，该代码进入 ``failed``。
    """
    symbols = list(dict.fromkeys(_tx_symbol(code) for code in codes))
    rows = []
    failed: List[str] = []

    for offset in range(0, len(symbols), batch_size):
        batch = symbols[offset:offset + batch_size]
        text = None
        for attempt in range(retries):
            try:
                response = requests.get(
                    TX_QUOTE_URL + ",".join(batch), timeout=(5, timeout)
                )
                response.raise_for_status()
                response.encoding = "gbk"
                text = response.text
                break
            except Exception:
                if attempt + 1 < retries:
                    time.sleep(1 + attempt)
        if text is None:
            failed.extend(batch)
            continue

        returned = set()
        for line in text.split(";"):
            if '="' not in line or "~" not in line:
                continue
            variable, payload = line.split('="', 1)
            symbol = variable.rsplit("_", 1)[-1].strip()
            values = payload.rstrip('"\r\n').split("~")
            if len(values) <= 35:
                continue
            try:
                stamp = values[30]
                amount_parts = values[35].split("/")
                row = {
                    "代码": symbol,
                    "日期": pd.to_datetime(stamp[:8], format="%Y%m%d"),
                    "开盘": float(values[5]),
                    "最高": float(values[33]),
                    "最低": float(values[34]),
                    "收盘": float(values[3]),
                    "成交额": float(amount_parts[2]),
                }
            except (IndexError, TypeError, ValueError):
                continue
            if min(row["开盘"], row["最高"], row["最低"], row["收盘"]) <= 0:
                continue
            rows.append(row)
            returned.add(symbol)
        failed.extend(code for code in batch if code not in returned)

    columns = ["代码", "日期", "开盘", "最高", "最低", "收盘", "成交额"]
    frame = pd.DataFrame(rows, columns=columns)
    if len(frame):
        frame = frame.drop_duplicates(subset=["代码", "日期"], keep="last")
    return frame, failed


# ====================================================================
# 数据源：baostock（备源）
# ====================================================================

@contextmanager
def baostock_session():
    """
    baostock 登录会话。一次 pass 只登录/登出一次（login 是网络往返）。

    用法:
        with baostock_session():
            bs_fetch_kline(...)   # 可顺序调用多次
    """
    global _BS_LOGGED_IN
    if bs is None:
        raise RuntimeError("baostock 未安装: pip install baostock")
    if _BS_LOGGED_IN:
        yield
        return
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock 登录失败: {lg.error_msg}")
    _BS_LOGGED_IN = True
    try:
        yield
    finally:
        bs.logout()
        _BS_LOGGED_IN = False


def bs_code(code: str) -> str:
    """项目格式 → baostock 格式: sh600519 → sh.600519"""
    if code.startswith(("sh", "sz")) and len(code) > 2:
        return f"{code[:2]}.{code[2:]}"
    return code


def bs_fetch_kline(code: str, start: str, end: str) -> Optional[pd.DataFrame]:
    """
    baostock 日K线（备源）。必须在 baostock_session() 内调用。

    复权: adjustflag="3" 不复权，与 akshare adjust="" 对齐
    成交额: baostock 返回“元”，与缓存保持同一单位
    """
    if not _BS_LOGGED_IN:
        raise RuntimeError("baostock 未登录，请在 baostock_session() 内调用")
    try:
        rs = bs.query_history_k_data_plus(
            bs_code(code), "date,open,high,low,close,amount",
            start_date=_to_bs_date(start), end_date=_to_bs_date(end),
            frequency="d", adjustflag="3",
        )
        if rs.error_code != "0":
            logger.warning("baostock 查询失败 %s: %s", code, rs.error_msg)
            return None
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        df = pd.DataFrame(rows, columns=rs.fields)
        df = df[df["close"] != ""]  # 停牌日字段为空
        # 空结果（周末/停牌）= 成功但无新数据，与 None（失败）区分开
        return _normalize(df, code, amount_scale=BS_AMOUNT_SCALE)
    except Exception:
        return None


def bs_get_stock_list(board: str = "all") -> pd.DataFrame:
    """
    baostock 股票列表 → 与 StockData.get_stock_list 同构（代码/名称 两列）。

    过滤规则与 get_stock_list 一致：ST/退、北交所、科创板剔除。
    代码为带前缀项目格式（sh600519）。
    """
    with baostock_session():
        rs = bs.query_stock_basic()
        if rs.error_code != "0":
            raise RuntimeError(f"baostock 股票列表查询失败: {rs.error_msg}")
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
    if not rows:
        raise RuntimeError("baostock 股票列表为空")

    df = pd.DataFrame(rows, columns=rs.fields)  # code,code_name,ipoDate,outDate,type,status
    df = df[(df["type"] == "1") & (df["status"] == "1") & (df["outDate"] == "")]
    df = df[~df["code_name"].str.contains("ST|退", regex=True, na=False)]
    df = df[~df["code"].str.startswith("bj")]
    df = df[~df["code"].str.startswith("sh.688")]

    if board == "main":
        df["code_num"] = df["code"].str.split(".").str[1]
        df = df[df["code_num"].str.startswith(MAIN_BOARD_PREFIX)]
        df = df.drop(columns=["code_num"])

    df["代码"] = df["code"].str.replace(".", "", regex=False)
    return df[["代码", "code_name"]].rename(columns={"code_name": "名称"}).reset_index(drop=True)


# ====================================================================
# 编排
# ====================================================================

def fetch_with_fallback(code: str, start: str, end: str,
                        primary: Callable = ak_fetch_kline,
                        fallback: Callable = bs_fetch_kline):
    """
    主源优先，失败回退备源。

    Returns:
        (df, source_name)；双失败返回 (None, None)
    """
    df = primary(code, start, end)
    if df is not None:
        return df, primary.__name__
    if fallback is not None:
        df = fallback(code, start, end)
        if df is not None:
            return df, fallback.__name__
    return None, None
