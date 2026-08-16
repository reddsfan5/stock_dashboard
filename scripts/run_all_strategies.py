#!/usr/bin/env python3
"""一键跑全部仿真策略并生成导航页"""

import os, sys, time, subprocess

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_DIR)
sys.path.insert(0, PROJECT_DIR)

STRATEGIES = [
    # (脚本, 描述, 额外参数)
    ("scripts/strategy_01_oversold.py", "超跌反弹T+1", ["--start", "2024-01-01"]),
    ("scripts/strategy_02_breakout.py", "缩量突破T+1", ["--start", "2025-01-01"]),
    ("scripts/strategy_03_trend.py", "均线多头趋势", ["--start", "2025-01-01"]),
    ("scripts/strategy_04_mean_reversion.py", "超卖均值回归", ["--start", "2025-01-01"]),
    ("scripts/strategy_05_5day_hold.py", "均值回归+5天限", ["--start", "2025-01-01"]),
    ("scripts/strategy_06_etf_stop.py", "ETF动量+止损", ["--start", "2025-01-01"]),
    ("scripts/strategy_07_multifactor.py", "月度多因子", ["--start", "2022-01-01"]),
    ("scripts/strategy_08_etf_momentum.py", "ETF动量轮动 ✅", ["--start", "2022-01-01"]),
    ("scripts/strategy_09_pullback.py", "缩量回调洗盘 ✅", ["--start", "2022-01-01"]),
    ("scripts/strategy_10_macd.py", "MACD金叉", ["--start", "2022-01-01"]),
    ("scripts/strategy_11_macross.py", "均线金叉", ["--start", "2022-01-01"]),
    ("scripts/strategy_12_volbreak.py", "放量突破", ["--start", "2022-01-01"]),
    ("scripts/strategy_13_pullback.py", "强趋势回调", ["--start", "2022-01-01"]),
    ("scripts/strategy_14_lowvol.py", "低波强势", ["--start", "2022-01-01"]),
]

if __name__ == "__main__":
    t0 = time.time()

    # 预热：一次性计算全市场指标并缓存到磁盘
    print("预热指标缓存...")
    from data.kline import StockData
    from backtest.indicators import compute_all
    MAIN = ("sh600","sh601","sh603","sh605","sz000","sz001","sz002","sz003")
    ind = compute_all(StockData().cache, MAIN)
    print(f"  缓存就绪: {len(ind)} 个指标\n")

    for script, desc, args in STRATEGIES:
        if not os.path.exists(os.path.join(PROJECT_DIR, script)):
            print(f"⊘ {desc}: 脚本不存在")
            continue
        print(f"\n{'='*50}\n  {desc}\n{'='*50}")
        try:
            subprocess.run([sys.executable, script] + args, timeout=600)
        except subprocess.TimeoutExpired:
            print(f"  ⚠ 超时(10min)")
        except Exception as e:
            print(f"  ✗ {e}")

    subprocess.run([sys.executable, "scripts/gen_index.py"])
    print(f"\n全部完成, 耗时: {time.time()-t0:.0f}秒")
