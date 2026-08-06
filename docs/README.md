# A股量化选股系统 — 文档索引

## 系统概览

本系统是一套完整的 A 股量化选股与回测工具链，覆盖数据管理 → 多维度选股 → 历史回测 → 资金模拟 → 报告输出的全流程。

## 文档导航

| 文档 | 内容 | 适合 |
|------|------|------|
| [01-快速开始](01-快速开始.md) | 环境搭建、首次运行、核心概念 | 新用户 |
| [02-数据层](02-数据层.md) | K线缓存管理、申万行业分类 | 需要了解数据来源 |
| [03-选股系统](03-选股系统.md) | 5 个选股模块完整说明 | 选股使用者 |
| [04-回测系统](04-回测系统.md) | 13 个回测策略详解 | 策略研究者 |
| [05-模拟交易](05-模拟交易.md) | 3 个资金模拟引擎 | 实盘前验证 |
| [06-管线编排](06-管线编排.md) | 自动发现、并行执行、报告生成 | 系统开发者 |
| [07-配置参考](07-配置参考.md) | 所有可配置参数一览 | 调参优化 |

## 项目结构

```
stock/
├── data/           # 数据层：K线缓存 + 行业分类
├── screen/         # 选股层：5 个选股模块
├── backtest/       # 回测层：统计回测 + 网格回测
├── pipeline/       # 编排层：模块发现、并行执行、报告生成
├── scripts/        # 入口脚本和模拟引擎
├── config/         # YAML 配置文件
├── cache/          # 本地数据缓存（parquet）
├── output/         # 生成的 HTML 报告和 CSV
└── docs/           # 文档（你在这里）
```

## 快速命令

```bash
# 更新数据
python scripts/update_cache.py

# 选股
python scripts/screen.py                        # 全部模块
python scripts/screen.py --only sideways        # 只看横盘

# 回测
python scripts/backtest.py                      # 全部策略
python scripts/backtest.py --only overlap,rising --start 2024-01-01

# 模拟
python scripts/sim_portfolio.py --capital 100000 --target 2.0
python scripts/sim_dip_buy.py --capital 100000 -m 2.0
```

## 数据流

```
akshare API ──→ data/kline.py (StockData) ──→ cache/stock_kline_cache.parquet
                                                      │
                    ┌─────────────────────────────────┤
                    ▼                                 ▼
            screen/*.py (选股)                backtest/engine.py (回测)
                    │                                 │
                    ▼                                 ▼
            pipeline/runner.py                  scripts/backtest.py
                    │                                 │
                    ▼                                 ▼
            output/dashboard.html              output/stats_report.html

cache/stock_kline_cache.parquet ──→ scripts/sim_*.py (模拟) ──→ output/*.html
```
