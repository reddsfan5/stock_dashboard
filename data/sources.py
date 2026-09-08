"""
多数据源层 — akshare(腾讯) 主源 + baostock 备源

背景：2026-08-15 腾讯行情接口 SSL 抖动导致整条定时链路失败，
单数据源是单点故障。本模块提供 baostock 备源，供增量更新失败时降级。

约定
----
- 统一返回格式：代码/日期/开盘/最高/最低/收盘/前收/成交量(股)/成交额(元)/换手率(%)
  screen/*.py 在使用流动性阈值时统一除以 10000 转为万元
- 失败语义：所有 fetch 函数失败/空结果返回 None（由调用方记录 last_failed）
- baostock 线程不安全（全局单 socket 会话），必须包在 baostock_session()
  内且顺序调用（threads=1）
"""

import os
import logging
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterable, List, Optional, Tuple

import pandas as pd
import requests

import akshare as ak

from data.schema import ensure_daily_bar_schema

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
_SINA_RUNTIME_LOCK = threading.Lock()
_SINA_RUNTIME = None

TX_QUOTE_URL = "https://qt.gtimg.cn/q="
TX_QUOTE_BATCH = 400


def baostock_available() -> bool:
    """当前 Python 环境是否具备可选的 baostock 备源。"""
    return bs is not None


# ====================================================================
# 数据规范化
# ====================================================================

def normalize_source_daily_bars(
    df: pd.DataFrame,
    code: str,
    amount_scale: float = 1.0,
    turnover_scale: float = 1.0,
) -> pd.DataFrame:
    """英文列映射为项目日 K 契约，并统一金额/换手率单位。"""
    required = ["date", "open", "high", "low", "close", "amount"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"数据源缺少字段: {', '.join(missing)}")
    selected = required + [column for column in ("volume", "turnover") if column in df]
    df = df[selected].copy().rename(columns={
        "date": "日期", "open": "开盘", "high": "最高", "low": "最低",
        "close": "收盘", "volume": "成交量", "amount": "成交额",
        "turnover": "换手率%",
    })
    df["代码"] = code
    if amount_scale != 1.0:
        df["成交额"] = df["成交额"] * amount_scale
    if "换手率%" in df and turnover_scale != 1.0:
        df["换手率%"] = pd.to_numeric(df["换手率%"], errors="coerce") * turnover_scale
    return ensure_daily_bar_schema(df)


# 兼容早期内部调用；新代码使用语义明确的公共名称。
_normalize = normalize_source_daily_bars


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
        # AkShare 腾讯适配器的 turnover 是小数（0.0019 = 0.19%）。
        return normalize_source_daily_bars(df, code, turnover_scale=100.0)
    except Exception:
        return None


def _ensure_sina_runtime():
    """在主调用线程初始化一次 V8，避免并发回退时竞争初始化地址池。

    AkShare 的新浪日线适配器会为每次解码创建 MiniRacer。macOS 后台任务
    首次同时创建多个实例时，mini-racer 可能在原生层直接退出（code 133），
    Python 无法捕获。保留一个进程级哨兵实例可先完成全局初始化。
    """
    global _SINA_RUNTIME
    if _SINA_RUNTIME is not None:
        return
    with _SINA_RUNTIME_LOCK:
        if _SINA_RUNTIME is None:
            from py_mini_racer import MiniRacer
            runtime = MiniRacer()
            runtime.eval("1+1")
            _SINA_RUNTIME = runtime


def sina_fetch_kline(code: str, start: str, end: str) -> Optional[pd.DataFrame]:
    """新浪全历史日 K，适合首次重建和字段迁移。

    与腾讯分页历史接口相比，该接口单次返回完整日期区间；原始成交量单位是股，
    turnover 是小数，适配后统一为百分数。
    """
    _ensure_sina_runtime()
    for attempt in range(3):
        try:
            frame = ak.stock_zh_a_daily(
                symbol=code,
                start_date=start,
                end_date=end,
                adjust="",
            )
            return normalize_source_daily_bars(
                frame, code, turnover_scale=100.0
            )
        except Exception:
            if attempt < 2:
                time.sleep(1 + attempt)
    return None


def _tx_symbol(code: str) -> str:
    """纯数字或项目代码统一为腾讯报价代码。"""
    value = str(code).strip().lower()
    if value.startswith(("sh", "sz")):
        return value
    return ("sh" if value.startswith(("5", "6")) else "sz") + value


def _float_at(values, index: int, scale: float = 1.0):
    """安全读取腾讯报价数组中的可选数值字段。"""
    try:
        value = values[index]
        if value in ("", "--", None):
            return float("nan")
        return float(value) * scale
    except (IndexError, TypeError, ValueError):
        return float("nan")


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
                    "前收": _float_at(values, 4),
                    # 腾讯报价量以手为单位，标准缓存统一为股。
                    "成交量": _float_at(values, 6, 100.0),
                    "成交额": float(amount_parts[2]),
                    "换手率%": _float_at(values, 38),
                    "市盈率_动态": _float_at(values, 39),
                    # 腾讯市值字段以亿元为单位。
                    "流通市值": _float_at(values, 44, 1e8),
                    "总市值": _float_at(values, 45, 1e8),
                    "供应商量比": _float_at(values, 49),
                }
            except (IndexError, TypeError, ValueError):
                continue
            if min(row["开盘"], row["最高"], row["最低"], row["收盘"]) <= 0:
                continue
            rows.append(row)
            returned.add(symbol)
        failed.extend(code for code in batch if code not in returned)

    columns = [
        "代码", "日期", "开盘", "最高", "最低", "收盘", "前收",
        "成交量", "成交额", "换手率%", "供应商量比", "市盈率_动态",
        "总市值", "流通市值",
    ]
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
    """项目格式 → baostock 格式: sh600519 → sh.600519；bj920xxx → bj.920xxx"""
    if code.startswith(("sh", "sz", "bj")) and len(code) > 2:
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
            bs_code(code), "date,open,high,low,close,volume,amount,turn",
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
        df = df.rename(columns={"turn": "turnover"})
        return normalize_source_daily_bars(df, code, amount_scale=BS_AMOUNT_SCALE)
    except Exception:
        return None


def bs_get_stock_list(board: str = "all") -> pd.DataFrame:
    """
    baostock 股票列表 → 与 StockData.get_stock_list 同构（代码/名称 两列）。

    过滤规则与 get_stock_list 对齐：默认保留科创/北交；仍排除 ST/退。
    代码为带前缀项目格式（sh600519 / bj920xxx）。
    注意：baostock 对北交所日 K 支持弱，管线对 bj* 会优先新浪回退。
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

    if board == "main":
        df["code_num"] = df["code"].str.split(".").str[1]
        df = df[df["code_num"].str.startswith(MAIN_BOARD_PREFIX)]
        df = df.drop(columns=["code_num"])
    elif board == "hs":
        df = df[~df["code"].str.startswith("bj.")]
    elif board != "all":
        raise ValueError("board 须为 main / hs / all，收到: {!r}".format(board))

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
