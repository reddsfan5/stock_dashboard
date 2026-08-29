#!/usr/bin/env python3
"""
一键跑全部仿真策略并生成导航页（多进程并行版）

用法
----
$ python -m scripts.run_all_strategies                 # 全部 14 个策略 + 1 个统计回测并行跑
$ python -m scripts.run_all_strategies --workers 4     # 限制并发数
$ python -m scripts.run_all_strategies --only 08,13    # 只跑指定策略（按脚本编号）

说明
----
- 每个策略独立 subprocess 跑（与旧版一致，隔离崩溃），并发数默认
  min(8, CPU核, 策略数)；每进程约 3GB 内存（股票缓存+指标），8 并发 ≈ 24GB
- 各策略完整输出写到 output/logs/<脚本名>.log，终端只显示一行结果
- 跑完后自动重新生成 index.html / mobile.html 导航页
"""

import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import cpu_count

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_DIR)
sys.path.insert(0, PROJECT_DIR)

STRATEGIES = [
    # (脚本, 描述, 额外参数)
    ("scripts/strategies/strategy_01_oversold.py", "超跌反弹T+1", ["--start", "2024-01-01"]),
    ("scripts/strategies/strategy_02_breakout.py", "缩量突破T+1", ["--start", "2025-01-01"]),
    ("scripts/strategies/strategy_03_trend.py", "均线多头趋势", ["--start", "2025-01-01"]),
    ("scripts/strategies/strategy_04_mean_reversion.py", "超卖均值回归", ["--start", "2025-01-01"]),
    ("scripts/strategies/strategy_05_5day_hold.py", "均值回归+5天限", ["--start", "2025-01-01"]),
    ("scripts/strategies/strategy_06_etf_stop.py", "ETF动量+止损", ["--start", "2025-01-01"]),
    ("scripts/strategies/strategy_07_multifactor.py", "月度多因子", ["--start", "2022-01-01"]),
    ("scripts/strategies/strategy_08_etf_momentum.py", "ETF动量轮动 ✅", ["--start", "2022-01-01"]),
    ("scripts/strategies/strategy_09_pullback.py", "缩量回调洗盘 ✅", ["--start", "2022-01-01"]),
    ("scripts/strategies/strategy_10_macd.py", "MACD金叉", ["--start", "2022-01-01"]),
    ("scripts/strategies/strategy_11_macross.py", "均线金叉", ["--start", "2022-01-01"]),
    ("scripts/strategies/strategy_12_volbreak.py", "放量突破", ["--start", "2022-01-01"]),
    ("scripts/strategies/strategy_13_pullback.py", "强趋势回调", ["--start", "2022-01-01"]),
    ("scripts/strategies/strategy_14_lowvol.py", "低波强势", ["--start", "2022-01-01"]),
    ("scripts/research/backtest_break_resume.py", "连续性中断恢复回测", ["--streaks", "2,3,5,8,10"]),
]

LOG_DIR = os.path.join(PROJECT_DIR, "output", "logs")
TIMEOUT = 600  # 单策略超时（秒）


def run_one(job):
    """单个策略 worker：独立 subprocess 跑，输出落日志文件"""
    script, desc, args = job
    log_file = os.path.join(LOG_DIR, os.path.basename(script).replace(".py", ".log"))
    if not os.path.exists(os.path.join(PROJECT_DIR, script)):
        return desc, "⊘ 脚本不存在", 0
    t0 = time.time()
    try:
        with open(log_file, "w", encoding="utf-8") as f:
            r = subprocess.run(
                [sys.executable, script] + args,
                stdout=f, stderr=subprocess.STDOUT,
                cwd=PROJECT_DIR, timeout=TIMEOUT,
            )
        dt = time.time() - t0
        if r.returncode == 0:
            return desc, f"✓ 完成 {dt:.0f}s", 0
        return desc, f"✗ 退出码 {r.returncode} ({dt:.0f}s, 详见 {log_file})", 1
    except subprocess.TimeoutExpired:
        return desc, f"⚠ 超时 {TIMEOUT}s (详见 {log_file})", 1


def main():
    parser = argparse.ArgumentParser(description="并行跑全部仿真策略")
    parser.add_argument("--workers", type=int, default=None,
                        help=f"并发数（默认 min(8, CPU核={cpu_count()}, 策略数)）")
    parser.add_argument("--only", type=str, default=None,
                        help="只跑指定策略编号，逗号分隔，如 08,13")
    args = parser.parse_args()

    jobs = STRATEGIES
    if args.only:
        wanted = {x.strip() for x in args.only.split(",")}
        jobs = [(s, d, a) for s, d, a in STRATEGIES
                if os.path.basename(s).replace("strategy_", "").split("_")[0] in wanted]
        if not jobs:
            sys.exit(f"✗ --only 未匹配任何策略: {args.only}")

    workers = args.workers or min(8, cpu_count(), len(jobs))
    os.makedirs(LOG_DIR, exist_ok=True)

    t0 = time.time()

    # 预热：一次性计算全市场指标并缓存到磁盘（各子进程从磁盘读，避免重复计算）
    print("预热指标缓存...")
    from data.kline import StockData
    from backtest.indicators import compute_all
    MAIN = ("sh600", "sh601", "sh603", "sh605", "sz000", "sz001", "sz002", "sz003")
    ind = compute_all(StockData().cache, MAIN)
    print(f"  缓存就绪: {len(ind)} 个指标, {workers} 并发启动 {len(jobs)} 个策略\n")

    results = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_one, job): job for job in jobs}
        for future in as_completed(futures):
            desc, status, rc = future.result()
            results.append((desc, status, rc))
            print(f"  [{len(results)}/{len(jobs)}] {desc:<16} {status}")

    failed = [r for r in results if r[2] != 0]
    print(f"\n===== 汇总: {len(results) - len(failed)}/{len(results)} 成功, "
          f"总耗时 {(time.time() - t0):.0f} 秒 =====")
    for desc, status, rc in failed:
        print(f"  ✗ {desc}: {status}")

    # 重新生成导航页（各策略 HTML 已更新）
    subprocess.run([sys.executable, "scripts/reports/gen_index.py"], cwd=PROJECT_DIR)
    subprocess.run([sys.executable, "scripts/reports/gen_mobile.py"], cwd=PROJECT_DIR)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
