"""
成交驱动型网格交易回测

模拟中国银河证券"成交驱动型"网格：
- 以实际成交价（收盘价）判断触发，不用 high/low 虚触
- 成交一笔 → 自动在对面网格线挂反向单
- 每根 K 线只触发最近的一个网格线

用法：
    python grid_backtest.py
"""

import akshare as ak
import pandas as pd
import numpy as np


# ======================
# 参数
# ======================

STOCK = "sh600519"          # 股票代码（新浪格式：sh/sz + 6位）
GRID_PCT = 0.5              # 每格间距（%）
GRID_LEVELS = 5             # 中枢上下各 N 层（总共 2N+1 层）
BASE_POSITION = 100         # 底仓（股）
TRADE_LOT = 100             # 每格交易量（股）
INITIAL_CASH = 1_000_000    # 初始现金

# 手续费（模拟银河证券真实费率）
STAMP_TAX = 0.0005          # 印花税（卖出单边 0.05%）
COMMISSION = 0.00025        # 佣金（买卖双向 0.025%，最低 5 元）


# ======================
# 获取数据
# ======================

def get_intraday(symbol, period="1"):
    """获取近期分钟线，返回最新一个交易日"""
    df = ak.stock_zh_a_minute(symbol=symbol, period=period, adjust="")

    df["day"] = pd.to_datetime(df["day"])
    df = df.sort_values("day").reset_index(drop=True)

    # 新浪返回的 price 列是字符串，转数值
    for c in ["open", "high", "low", "close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # 取最新一个交易日
    latest_date = df["day"].dt.date.max()
    df = df[df["day"].dt.date == latest_date].copy()

    return df


# ======================
# 构建网格
# ======================

def build_grid(center_price, pct, levels):
    """以 center_price 为中枢，上下各 levels 层，构建网格"""
    grid = []
    for i in range(-levels, levels + 1):
        price = float(center_price * (1 + pct / 100 * i))
        grid.append(round(price, 2))
    return sorted(grid)


# ======================
# 回测
# ======================

def fee(amount, is_sell=False):
    """计算手续费（佣金 + 印花税）"""
    c = max(amount * COMMISSION, 5)  # 佣金最低 5 元
    s = amount * STAMP_TAX if is_sell else 0
    return c + s


def backtest(df, grid, base_position, trade_lot, initial_cash):
    """
    成交驱动型网格回测（模拟中国银河模式）：

    - 只用收盘价（实际成交价）判断触发，不用 K 线 high/low 虚触
    - 每根 K 线只触发一个网格线：prev_close → close 路径上最先碰到的
    - 成交后自动在对侧挂反向单
    - 含手续费（佣金 + 印花税）
    """

    cash = initial_cash
    position = base_position
    trades = []
    total_fee = 0

    mid = len(grid) // 2
    pending_buy = [False] * len(grid)
    pending_sell = [False] * len(grid)

    # 初始挂单：中枢以下挂买单，中枢以上挂卖单
    for i in range(mid):
        pending_buy[i] = True
    for i in range(mid + 1, len(grid)):
        pending_sell[i] = True

    prev_close = float(df.iloc[0]["open"])  # 以开盘价为起点

    for _, bar in df.iterrows():
        t = bar["day"]
        c = float(bar["close"])  # 本 K 线收盘价（代表实际成交价）

        # 找出 prev_close → c 路径上穿过的网格线（只触发最近的一个）
        if c > prev_close:
            # 价格上涨：从低到高检查穿过了哪个卖单网格线（触发最近的那个）
            for i, g in enumerate(grid):
                if pending_sell[i] and prev_close < g <= c:
                    if position >= trade_lot:
                        amount = trade_lot * g
                        f = fee(amount, is_sell=True)
                        position -= trade_lot
                        cash += amount - f
                        total_fee += f
                        trades.append({
                            "时间": t, "方向": "卖出", "价格": g,
                            "数量": trade_lot, "金额": amount, "手续费": round(f, 2),
                        })
                        pending_sell[i] = False
                        if i > 0:
                            pending_buy[i - 1] = True  # 下一格挂买单
                    break  # 每根 K 只触发一次

        elif c < prev_close:
            # 价格下跌：从高到低检查穿过了哪个买单网格线（触发最近的那个）
            for i in range(len(grid) - 1, -1, -1):
                if pending_buy[i] and prev_close > grid[i] >= c:
                    g = grid[i]
                    amount = trade_lot * g
                    f = fee(amount, is_sell=False)
                    if cash >= amount + f:
                        position += trade_lot
                        cash -= amount + f
                        total_fee += f
                        trades.append({
                            "时间": t, "方向": "买入", "价格": g,
                            "数量": trade_lot, "金额": amount, "手续费": round(f, 2),
                        })
                        pending_buy[i] = False
                        if i < len(grid) - 1:
                            pending_sell[i + 1] = True  # 上一格挂卖单
                    break  # 每根 K 只触发一次

        prev_close = c

    # 按收盘价平仓
    final_price = float(df.iloc[-1]["close"])
    final_value = cash + position * final_price

    return trades, cash, position, final_value, total_fee


# ======================
# 主程序
# ======================

def main():
    print(f"股票: {STOCK}")
    print(f"网格: ±{GRID_LEVELS} 层 × {GRID_PCT}%  底仓{BASE_POSITION}股  每格{TRADE_LOT}股")
    print(f"手续费: 佣金{COMMISSION*100:.3f}%(最低5元) + 印花税{STAMP_TAX*100:.2f}%(卖)")
    print()

    # 获取数据
    df = get_intraday(STOCK, period="1")
    if len(df) == 0:
        print("未获取到数据")
        return

    date = df["day"].dt.date.iloc[0]
    open_price = float(df.iloc[0]["open"])
    high = float(df["high"].max())
    low = float(df["low"].min())
    close = float(df.iloc[-1]["close"])
    print(f"日期: {date}  开盘: {open_price}  最高: {high}  最低: {low}  收盘: {close}")
    print(f"分钟线: {len(df)} 根  日振幅: {(high-low)/low*100:.2f}%")

    # 构建网格（以开盘价为中枢）
    grid = build_grid(open_price, GRID_PCT, GRID_LEVELS)
    print(f"\n网格线:")
    mid = len(grid) // 2
    for i, g in enumerate(grid):
        tag = "← 中枢" if i == mid else ("卖单" if i > mid else "买单")
        print(f"  {g:>10.2f}  {tag}")

    # 回测
    trades, cash, position, final_value, total_fee = backtest(
        df, grid, BASE_POSITION, TRADE_LOT, INITIAL_CASH
    )

    print(f"\n=== 回测结果 ===")
    print(f"交易次数: {len(trades)}  总手续费: {total_fee:.2f}")

    if trades:
        buys = sum(1 for t in trades if t["方向"] == "买入")
        sells = sum(1 for t in trades if t["方向"] == "卖出")
        buy_amt = sum(t["金额"] for t in trades if t["方向"] == "买入")
        sell_amt = sum(t["金额"] for t in trades if t["方向"] == "卖出")
        print(f"买入: {buys} 次 金额 {buy_amt:,.0f}  卖出: {sells} 次 金额 {sell_amt:,.0f}")
        print()
        for t in trades[:15]:
            print(f"  {str(t['时间'])[:16]}  {t['方向']}  @{t['价格']:>8}  ×{t['数量']}股  ={t['金额']:>10,.0f}  费{t['手续费']:>6}")
        if len(trades) > 15:
            print(f"  ... 共 {len(trades)} 笔")

    initial_value = INITIAL_CASH + BASE_POSITION * open_price
    profit = final_value - initial_value
    print(f"\n初始市值: {initial_value:,.0f}  (现金{INITIAL_CASH:,} + {BASE_POSITION}股×{open_price})")
    print(f"最终市值: {final_value:,.0f}  (现金{cash:,.0f} + {position}股×{close}={position*close:,.0f})")
    print(f"网格收益: {profit:+,.0f}  ({profit/initial_value*100:+.2f}%)")

    # 持有不动对比
    hold_profit = BASE_POSITION * (close - open_price)
    print(f"持有不动: {hold_profit:+,.0f}  ({hold_profit/initial_value*100:+.2f}%)")
    print(f"超额收益: {profit - hold_profit:+,.0f}")


if __name__ == "__main__":
    main()
